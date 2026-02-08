"""
Confluence API Service.

Handles OAuth authentication and API calls to Atlassian Confluence.
Uses the Confluence Cloud REST API v2.

Atlassian OAuth 2.0 docs:
https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/
"""

import httpx
import secrets
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from urllib.parse import urlencode

from app.core.config import settings
from app.schemas.integration import (
    ConfluenceSpace,
    ConfluencePage,
    SyncResult,
)


class ConfluenceService:
    """
    Service for interacting with Confluence Cloud API.

    Handles:
    - OAuth 2.0 authorization flow
    - Token refresh
    - Spaces and pages CRUD
    - Content conversion (Confluence storage format <-> Tiptap JSON)
    """

    # Atlassian OAuth endpoints
    AUTH_URL = "https://auth.atlassian.com/authorize"
    TOKEN_URL = "https://auth.atlassian.com/oauth/token"
    ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

    # Confluence API base
    API_BASE = "https://api.atlassian.com/ex/confluence"

    def __init__(self, access_token: Optional[str] = None, cloud_id: Optional[str] = None):
        """
        Initialize the Confluence service.

        Args:
            access_token: OAuth access token for API calls
            cloud_id: Atlassian Cloud ID for the site
        """
        self.access_token = access_token
        self.cloud_id = cloud_id
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        """Check if Confluence OAuth is configured."""
        return settings.CONFLUENCE_CONFIGURED

    @property
    def api_url(self) -> str:
        """Get the base API URL for the current cloud instance."""
        if not self.cloud_id:
            raise ValueError("Cloud ID not set")
        return f"{self.API_BASE}/{self.cloud_id}/wiki/api/v2"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create an async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ============ OAuth Flow ============

    def get_authorization_url(self, state: str) -> str:
        """
        Generate the OAuth authorization URL.

        Args:
            state: Random state parameter for CSRF protection

        Returns:
            Authorization URL to redirect the user to
        """
        if not self.is_configured:
            raise ValueError("Confluence OAuth is not configured")

        params = {
            "audience": "api.atlassian.com",
            "client_id": settings.CONFLUENCE_CLIENT_ID,
            "scope": settings.CONFLUENCE_SCOPES,
            "redirect_uri": settings.CONFLUENCE_REDIRECT_URI,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    @staticmethod
    def generate_state() -> str:
        """Generate a random state parameter for OAuth."""
        return secrets.token_urlsafe(32)

    async def exchange_code_for_tokens(self, code: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access and refresh tokens.

        Args:
            code: Authorization code from OAuth callback

        Returns:
            Token response containing access_token, refresh_token, expires_in
        """
        client = await self._get_client()

        data = {
            "grant_type": "authorization_code",
            "client_id": settings.CONFLUENCE_CLIENT_ID,
            "client_secret": settings.CONFLUENCE_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.CONFLUENCE_REDIRECT_URI,
        }

        response = await client.post(self.TOKEN_URL, data=data)
        response.raise_for_status()

        return response.json()

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Refresh the access token using the refresh token.

        Args:
            refresh_token: The refresh token

        Returns:
            New token response
        """
        client = await self._get_client()

        data = {
            "grant_type": "refresh_token",
            "client_id": settings.CONFLUENCE_CLIENT_ID,
            "client_secret": settings.CONFLUENCE_CLIENT_SECRET,
            "refresh_token": refresh_token,
        }

        response = await client.post(self.TOKEN_URL, data=data)
        response.raise_for_status()

        return response.json()

    async def get_accessible_resources(self) -> List[Dict[str, Any]]:
        """
        Get list of Atlassian sites the user has access to.

        Returns:
            List of accessible resources with id, name, url
        """
        if not self.access_token:
            raise ValueError("Access token not set")

        client = await self._get_client()
        headers = {"Authorization": f"Bearer {self.access_token}"}

        response = await client.get(self.ACCESSIBLE_RESOURCES_URL, headers=headers)
        response.raise_for_status()

        return response.json()

    # ============ Spaces ============

    async def list_spaces(self, limit: int = 50) -> List[ConfluenceSpace]:
        """
        List all Confluence spaces the user has access to.

        Args:
            limit: Maximum number of spaces to return

        Returns:
            List of Confluence spaces
        """
        if not self.access_token or not self.cloud_id:
            raise ValueError("Access token and cloud_id required")

        client = await self._get_client()
        headers = {"Authorization": f"Bearer {self.access_token}"}

        response = await client.get(
            f"{self.api_url}/spaces",
            params={"limit": limit},
            headers=headers,
        )
        response.raise_for_status()

        data = response.json()
        spaces = []

        for space in data.get("results", []):
            spaces.append(ConfluenceSpace(
                id=space["id"],
                key=space["key"],
                name=space["name"],
                type=space.get("type", "global"),
                description=space.get("description", {}).get("plain", {}).get("value"),
                icon=space.get("icon", {}).get("path"),
            ))

        return spaces

    async def get_space(self, space_key: str) -> Optional[ConfluenceSpace]:
        """Get a specific space by key."""
        if not self.access_token or not self.cloud_id:
            raise ValueError("Access token and cloud_id required")

        client = await self._get_client()
        headers = {"Authorization": f"Bearer {self.access_token}"}

        # V2 API uses space ID, but we can search by key
        response = await client.get(
            f"{self.api_url}/spaces",
            params={"keys": space_key, "limit": 1},
            headers=headers,
        )
        response.raise_for_status()

        data = response.json()
        results = data.get("results", [])

        if not results:
            return None

        space = results[0]
        return ConfluenceSpace(
            id=space["id"],
            key=space["key"],
            name=space["name"],
            type=space.get("type", "global"),
        )

    # ============ Pages ============

    async def list_pages(
        self,
        space_id: str,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List pages in a Confluence space.

        Args:
            space_id: The space ID (not key)
            limit: Maximum pages to return
            cursor: Pagination cursor

        Returns:
            Dict with pages list and pagination info
        """
        if not self.access_token or not self.cloud_id:
            raise ValueError("Access token and cloud_id required")

        client = await self._get_client()
        headers = {"Authorization": f"Bearer {self.access_token}"}

        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        response = await client.get(
            f"{self.api_url}/spaces/{space_id}/pages",
            params=params,
            headers=headers,
        )
        response.raise_for_status()

        data = response.json()
        pages = []

        for page in data.get("results", []):
            pages.append(ConfluencePage(
                id=page["id"],
                title=page["title"],
                space_key=page.get("spaceId", space_id),
                version=page.get("version", {}).get("number", 1),
                web_url=page.get("_links", {}).get("webui"),
            ))

        return {
            "pages": pages,
            "cursor": data.get("_links", {}).get("next"),
            "total": len(pages),
        }

    async def get_page(self, page_id: str, include_body: bool = True) -> Optional[ConfluencePage]:
        """
        Get a specific page by ID.

        Args:
            page_id: The page ID
            include_body: Whether to include page body content

        Returns:
            ConfluencePage or None if not found
        """
        if not self.access_token or not self.cloud_id:
            raise ValueError("Access token and cloud_id required")

        client = await self._get_client()
        headers = {"Authorization": f"Bearer {self.access_token}"}

        params = {}
        if include_body:
            params["body-format"] = "storage"

        try:
            response = await client.get(
                f"{self.api_url}/pages/{page_id}",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

        page = response.json()

        return ConfluencePage(
            id=page["id"],
            title=page["title"],
            space_key=page.get("spaceId", ""),
            version=page.get("version", {}).get("number", 1),
            web_url=page.get("_links", {}).get("webui"),
            body_html=page.get("body", {}).get("storage", {}).get("value") if include_body else None,
        )

    async def create_page(
        self,
        space_id: str,
        title: str,
        body_html: str,
        parent_id: Optional[str] = None,
    ) -> ConfluencePage:
        """
        Create a new page in Confluence.

        Args:
            space_id: Space ID to create page in
            title: Page title
            body_html: Page content in Confluence storage format
            parent_id: Optional parent page ID

        Returns:
            Created page
        """
        if not self.access_token or not self.cloud_id:
            raise ValueError("Access token and cloud_id required")

        client = await self._get_client()
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        data = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {
                "representation": "storage",
                "value": body_html,
            },
        }

        if parent_id:
            data["parentId"] = parent_id

        response = await client.post(
            f"{self.api_url}/pages",
            json=data,
            headers=headers,
        )
        response.raise_for_status()

        page = response.json()

        return ConfluencePage(
            id=page["id"],
            title=page["title"],
            space_key=space_id,
            version=page.get("version", {}).get("number", 1),
            web_url=page.get("_links", {}).get("webui"),
        )

    async def update_page(
        self,
        page_id: str,
        title: str,
        body_html: str,
        version: int,
    ) -> ConfluencePage:
        """
        Update an existing page in Confluence.

        Args:
            page_id: Page ID to update
            title: New title
            body_html: New content in Confluence storage format
            version: Current version number (for optimistic locking)

        Returns:
            Updated page
        """
        if not self.access_token or not self.cloud_id:
            raise ValueError("Access token and cloud_id required")

        client = await self._get_client()
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        data = {
            "id": page_id,
            "status": "current",
            "title": title,
            "body": {
                "representation": "storage",
                "value": body_html,
            },
            "version": {
                "number": version + 1,
                "message": "Updated from Merlin",
            },
        }

        response = await client.put(
            f"{self.api_url}/pages/{page_id}",
            json=data,
            headers=headers,
        )
        response.raise_for_status()

        page = response.json()

        return ConfluencePage(
            id=page["id"],
            title=page["title"],
            space_key=page.get("spaceId", ""),
            version=page.get("version", {}).get("number", 1),
            web_url=page.get("_links", {}).get("webui"),
        )

    # ============ Content Conversion ============

    @staticmethod
    def confluence_to_tiptap(storage_html: str) -> Dict[str, Any]:
        """
        Convert Confluence storage format HTML to Tiptap JSON.

        This is a simplified conversion. In production, use a proper
        HTML parser and handle all Confluence macros.

        Args:
            storage_html: Confluence storage format HTML

        Returns:
            Tiptap document JSON
        """
        import re

        # Basic conversion - strip tags and create simple doc
        # In production, use BeautifulSoup or similar for proper parsing
        text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', storage_html, flags=re.IGNORECASE)
        text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.strip()

        # Build Tiptap document
        content = []
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.startswith('### '):
                content.append({
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": line[4:]}],
                })
            elif line.startswith('## '):
                content.append({
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": line[3:]}],
                })
            elif line.startswith('# '):
                content.append({
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": line[2:]}],
                })
            elif line.startswith('- '):
                content.append({
                    "type": "bulletList",
                    "content": [{
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": line[2:]}],
                        }],
                    }],
                })
            else:
                content.append({
                    "type": "paragraph",
                    "content": [{"type": "text", "text": line}] if line else [],
                })

        return {
            "type": "doc",
            "content": content if content else [{"type": "paragraph", "content": []}],
        }

    @staticmethod
    def tiptap_to_confluence(tiptap_json: Dict[str, Any]) -> str:
        """
        Convert Tiptap JSON to Confluence storage format HTML.

        Args:
            tiptap_json: Tiptap document JSON

        Returns:
            Confluence storage format HTML
        """
        def node_to_html(node: Dict[str, Any]) -> str:
            node_type = node.get("type", "")
            content = node.get("content", [])
            attrs = node.get("attrs", {})

            if node_type == "doc":
                return "".join(node_to_html(child) for child in content)

            elif node_type == "paragraph":
                inner = "".join(node_to_html(child) for child in content)
                return f"<p>{inner}</p>"

            elif node_type == "heading":
                level = attrs.get("level", 1)
                inner = "".join(node_to_html(child) for child in content)
                return f"<h{level}>{inner}</h{level}>"

            elif node_type == "text":
                text = node.get("text", "")
                marks = node.get("marks", [])
                for mark in marks:
                    mark_type = mark.get("type", "")
                    if mark_type == "bold":
                        text = f"<strong>{text}</strong>"
                    elif mark_type == "italic":
                        text = f"<em>{text}</em>"
                    elif mark_type == "code":
                        text = f"<code>{text}</code>"
                    elif mark_type == "link":
                        href = mark.get("attrs", {}).get("href", "")
                        text = f'<a href="{href}">{text}</a>'
                return text

            elif node_type == "bulletList":
                items = "".join(node_to_html(child) for child in content)
                return f"<ul>{items}</ul>"

            elif node_type == "orderedList":
                items = "".join(node_to_html(child) for child in content)
                return f"<ol>{items}</ol>"

            elif node_type == "listItem":
                inner = "".join(node_to_html(child) for child in content)
                return f"<li>{inner}</li>"

            elif node_type == "codeBlock":
                inner = "".join(node_to_html(child) for child in content)
                lang = attrs.get("language", "")
                return f'<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">{lang}</ac:parameter><ac:plain-text-body><![CDATA[{inner}]]></ac:plain-text-body></ac:structured-macro>'

            elif node_type == "blockquote":
                inner = "".join(node_to_html(child) for child in content)
                return f"<blockquote>{inner}</blockquote>"

            elif node_type == "horizontalRule":
                return "<hr />"

            else:
                # Unknown node type - try to extract text
                return "".join(node_to_html(child) for child in content)

        return node_to_html(tiptap_json)

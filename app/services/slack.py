"""
Slack API Service.

Handles OAuth authentication and API calls to Slack.
Uses the Slack Web API for fetching workspace info, channels, messages, and users.

Slack OAuth 2.0 docs:
https://api.slack.com/authentication/oauth-v2
"""

import httpx
import secrets
import hmac
import hashlib
import time
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from urllib.parse import urlencode

from app.core.config import settings


class SlackChannel:
    """Represents a Slack channel."""
    def __init__(
        self,
        id: str,
        name: str,
        is_private: bool = False,
        is_archived: bool = False,
        topic: Optional[str] = None,
        purpose: Optional[str] = None,
        num_members: int = 0,
    ):
        self.id = id
        self.name = name
        self.is_private = is_private
        self.is_archived = is_archived
        self.topic = topic
        self.purpose = purpose
        self.num_members = num_members


class SlackUser:
    """Represents a Slack user."""
    def __init__(
        self,
        id: str,
        name: str,
        real_name: Optional[str] = None,
        display_name: Optional[str] = None,
        email: Optional[str] = None,
        avatar_url: Optional[str] = None,
        is_bot: bool = False,
        is_admin: bool = False,
    ):
        self.id = id
        self.name = name
        self.real_name = real_name
        self.display_name = display_name
        self.email = email
        self.avatar_url = avatar_url
        self.is_bot = is_bot
        self.is_admin = is_admin


class SlackMessage:
    """Represents a Slack message."""
    def __init__(
        self,
        ts: str,
        text: str,
        user_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        thread_ts: Optional[str] = None,
        reply_count: int = 0,
        reactions: Optional[List[Dict]] = None,
        attachments: Optional[List[Dict]] = None,
        files: Optional[List[Dict]] = None,
    ):
        self.ts = ts  # Slack timestamp (also serves as message ID)
        self.text = text
        self.user_id = user_id
        self.channel_id = channel_id
        self.thread_ts = thread_ts
        self.reply_count = reply_count
        self.reactions = reactions or []
        self.attachments = attachments or []
        self.files = files or []

    @property
    def timestamp(self) -> datetime:
        """Convert Slack ts to datetime."""
        return datetime.fromtimestamp(float(self.ts))


class SlackTeam:
    """Represents a Slack workspace/team."""
    def __init__(
        self,
        id: str,
        name: str,
        domain: str,
        icon_url: Optional[str] = None,
    ):
        self.id = id
        self.name = name
        self.domain = domain
        self.icon_url = icon_url


class SlackService:
    """
    Service for interacting with Slack API.

    Handles:
    - OAuth 2.0 authorization flow
    - Workspace/team info
    - Channels listing and history
    - Users listing
    - Message posting
    - File sharing
    """

    # Slack OAuth endpoints
    AUTH_URL = "https://slack.com/oauth/v2/authorize"
    TOKEN_URL = "https://slack.com/api/oauth.v2.access"

    # Slack API base
    API_BASE = "https://slack.com/api"

    def __init__(self, access_token: Optional[str] = None):
        """
        Initialize the Slack service.

        Args:
            access_token: OAuth access token for API calls
        """
        self.access_token = access_token
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        """Check if Slack OAuth is configured."""
        return settings.SLACK_CONFIGURED

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create an async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _api_call(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make an API call to Slack."""
        if not self.access_token:
            raise ValueError("Access token not set")

        client = await self._get_client()
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        url = f"{self.API_BASE}/{endpoint}"

        if method.upper() == "GET":
            response = await client.get(url, headers=headers, params=params)
        else:
            response = await client.post(url, headers=headers, json=data, params=params)

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            raise Exception(f"Slack API error: {error}")

        return result

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
            raise ValueError("Slack OAuth is not configured")

        params = {
            "client_id": settings.SLACK_CLIENT_ID,
            "scope": settings.SLACK_SCOPES,
            "redirect_uri": settings.SLACK_REDIRECT_URI,
            "state": state,
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    @staticmethod
    def generate_state() -> str:
        """Generate a random state parameter for OAuth."""
        return secrets.token_urlsafe(32)

    async def exchange_code_for_tokens(self, code: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access token.

        Args:
            code: Authorization code from OAuth callback

        Returns:
            Token response containing access_token, team info, etc.
        """
        client = await self._get_client()

        data = {
            "client_id": settings.SLACK_CLIENT_ID,
            "client_secret": settings.SLACK_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.SLACK_REDIRECT_URI,
        }

        response = await client.post(self.TOKEN_URL, data=data)
        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            raise Exception(f"Slack OAuth error: {error}")

        return result

    def verify_request_signature(
        self,
        signature: str,
        timestamp: str,
        body: bytes,
    ) -> bool:
        """
        Verify a request signature from Slack (for webhooks/events).

        Args:
            signature: X-Slack-Signature header
            timestamp: X-Slack-Request-Timestamp header
            body: Raw request body

        Returns:
            True if signature is valid
        """
        if not settings.SLACK_SIGNING_SECRET:
            return False

        # Check timestamp to prevent replay attacks
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False

        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
        my_signature = (
            "v0="
            + hmac.new(
                settings.SLACK_SIGNING_SECRET.encode(),
                sig_basestring.encode(),
                hashlib.sha256,
            ).hexdigest()
        )

        return hmac.compare_digest(my_signature, signature)

    # ============ Team/Workspace ============

    async def get_team_info(self) -> SlackTeam:
        """Get information about the connected workspace."""
        result = await self._api_call("GET", "team.info")
        team = result["team"]

        return SlackTeam(
            id=team["id"],
            name=team["name"],
            domain=team["domain"],
            icon_url=team.get("icon", {}).get("image_132"),
        )

    # ============ Channels ============

    async def list_channels(
        self,
        include_private: bool = False,
        exclude_archived: bool = True,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List channels in the workspace.

        Args:
            include_private: Include private channels (requires groups:read scope)
            exclude_archived: Exclude archived channels
            limit: Maximum channels to return (max 1000)
            cursor: Pagination cursor

        Returns:
            Dict with channels list and pagination info
        """
        params = {
            "exclude_archived": exclude_archived,
            "limit": min(limit, 1000),
            "types": "public_channel,private_channel" if include_private else "public_channel",
        }
        if cursor:
            params["cursor"] = cursor

        result = await self._api_call("GET", "conversations.list", params=params)

        channels = [
            SlackChannel(
                id=ch["id"],
                name=ch["name"],
                is_private=ch.get("is_private", False),
                is_archived=ch.get("is_archived", False),
                topic=ch.get("topic", {}).get("value"),
                purpose=ch.get("purpose", {}).get("value"),
                num_members=ch.get("num_members", 0),
            )
            for ch in result.get("channels", [])
        ]

        return {
            "channels": channels,
            "cursor": result.get("response_metadata", {}).get("next_cursor"),
        }

    async def get_channel_info(self, channel_id: str) -> SlackChannel:
        """Get information about a specific channel."""
        result = await self._api_call(
            "GET", "conversations.info", params={"channel": channel_id}
        )
        ch = result["channel"]

        return SlackChannel(
            id=ch["id"],
            name=ch["name"],
            is_private=ch.get("is_private", False),
            is_archived=ch.get("is_archived", False),
            topic=ch.get("topic", {}).get("value"),
            purpose=ch.get("purpose", {}).get("value"),
            num_members=ch.get("num_members", 0),
        )

    # ============ Messages ============

    async def get_channel_history(
        self,
        channel_id: str,
        limit: int = 100,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get message history for a channel.

        Args:
            channel_id: Channel ID
            limit: Maximum messages to return
            oldest: Only messages after this timestamp
            latest: Only messages before this timestamp
            cursor: Pagination cursor

        Returns:
            Dict with messages list and pagination info
        """
        params = {
            "channel": channel_id,
            "limit": min(limit, 1000),
        }
        if oldest:
            params["oldest"] = oldest
        if latest:
            params["latest"] = latest
        if cursor:
            params["cursor"] = cursor

        result = await self._api_call("GET", "conversations.history", params=params)

        messages = [
            SlackMessage(
                ts=msg["ts"],
                text=msg.get("text", ""),
                user_id=msg.get("user"),
                channel_id=channel_id,
                thread_ts=msg.get("thread_ts"),
                reply_count=msg.get("reply_count", 0),
                reactions=msg.get("reactions"),
                attachments=msg.get("attachments"),
                files=msg.get("files"),
            )
            for msg in result.get("messages", [])
        ]

        return {
            "messages": messages,
            "has_more": result.get("has_more", False),
            "cursor": result.get("response_metadata", {}).get("next_cursor"),
        }

    async def get_thread_replies(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get replies in a thread.

        Args:
            channel_id: Channel ID
            thread_ts: Timestamp of the parent message
            limit: Maximum replies to return
            cursor: Pagination cursor

        Returns:
            Dict with messages list and pagination info
        """
        params = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": min(limit, 1000),
        }
        if cursor:
            params["cursor"] = cursor

        result = await self._api_call("GET", "conversations.replies", params=params)

        messages = [
            SlackMessage(
                ts=msg["ts"],
                text=msg.get("text", ""),
                user_id=msg.get("user"),
                channel_id=channel_id,
                thread_ts=msg.get("thread_ts"),
                reactions=msg.get("reactions"),
                attachments=msg.get("attachments"),
                files=msg.get("files"),
            )
            for msg in result.get("messages", [])
        ]

        return {
            "messages": messages,
            "has_more": result.get("has_more", False),
            "cursor": result.get("response_metadata", {}).get("next_cursor"),
        }

    async def post_message(
        self,
        channel_id: str,
        text: str,
        thread_ts: Optional[str] = None,
        blocks: Optional[List[Dict]] = None,
        unfurl_links: bool = True,
    ) -> SlackMessage:
        """
        Post a message to a channel.

        Args:
            channel_id: Channel to post to
            text: Message text (also used as fallback for blocks)
            thread_ts: Thread timestamp to reply to
            blocks: Block Kit blocks for rich formatting
            unfurl_links: Whether to unfurl URLs

        Returns:
            The posted message
        """
        data = {
            "channel": channel_id,
            "text": text,
            "unfurl_links": unfurl_links,
        }
        if thread_ts:
            data["thread_ts"] = thread_ts
        if blocks:
            data["blocks"] = blocks

        result = await self._api_call("POST", "chat.postMessage", data=data)

        return SlackMessage(
            ts=result["ts"],
            text=text,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )

    # ============ Users ============

    async def list_users(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List users in the workspace.

        Args:
            limit: Maximum users to return
            cursor: Pagination cursor

        Returns:
            Dict with users list and pagination info
        """
        params = {"limit": min(limit, 1000)}
        if cursor:
            params["cursor"] = cursor

        result = await self._api_call("GET", "users.list", params=params)

        users = [
            SlackUser(
                id=user["id"],
                name=user["name"],
                real_name=user.get("real_name"),
                display_name=user.get("profile", {}).get("display_name"),
                email=user.get("profile", {}).get("email"),
                avatar_url=user.get("profile", {}).get("image_72"),
                is_bot=user.get("is_bot", False),
                is_admin=user.get("is_admin", False),
            )
            for user in result.get("members", [])
            if not user.get("deleted", False)
        ]

        return {
            "users": users,
            "cursor": result.get("response_metadata", {}).get("next_cursor"),
        }

    async def get_user_info(self, user_id: str) -> SlackUser:
        """Get information about a specific user."""
        result = await self._api_call(
            "GET", "users.info", params={"user": user_id}
        )
        user = result["user"]

        return SlackUser(
            id=user["id"],
            name=user["name"],
            real_name=user.get("real_name"),
            display_name=user.get("profile", {}).get("display_name"),
            email=user.get("profile", {}).get("email"),
            avatar_url=user.get("profile", {}).get("image_72"),
            is_bot=user.get("is_bot", False),
            is_admin=user.get("is_admin", False),
        )

    # ============ Search ============

    async def search_messages(
        self,
        query: str,
        sort: str = "timestamp",
        sort_dir: str = "desc",
        count: int = 20,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Search for messages (requires search:read scope).

        Args:
            query: Search query
            sort: Sort by "timestamp" or "score"
            sort_dir: Sort direction "asc" or "desc"
            count: Results per page
            page: Page number

        Returns:
            Dict with matches and pagination info
        """
        params = {
            "query": query,
            "sort": sort,
            "sort_dir": sort_dir,
            "count": count,
            "page": page,
        }

        result = await self._api_call("GET", "search.messages", params=params)

        messages = result.get("messages", {})
        matches = [
            SlackMessage(
                ts=match["ts"],
                text=match.get("text", ""),
                user_id=match.get("user"),
                channel_id=match.get("channel", {}).get("id"),
            )
            for match in messages.get("matches", [])
        ]

        return {
            "messages": matches,
            "total": messages.get("total", 0),
            "page": messages.get("pagination", {}).get("page", 1),
            "pages": messages.get("pagination", {}).get("page_count", 1),
        }

    # ============ Files ============

    async def list_files(
        self,
        channel_id: Optional[str] = None,
        user_id: Optional[str] = None,
        types: Optional[str] = None,
        count: int = 20,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        List files in the workspace.

        Args:
            channel_id: Filter by channel
            user_id: Filter by user
            types: Filter by file types (comma-separated: images, pdfs, etc.)
            count: Results per page
            page: Page number

        Returns:
            Dict with files list and pagination info
        """
        params = {"count": count, "page": page}
        if channel_id:
            params["channel"] = channel_id
        if user_id:
            params["user"] = user_id
        if types:
            params["types"] = types

        result = await self._api_call("GET", "files.list", params=params)

        return {
            "files": result.get("files", []),
            "total": result.get("paging", {}).get("total", 0),
            "page": result.get("paging", {}).get("page", 1),
            "pages": result.get("paging", {}).get("pages", 1),
        }

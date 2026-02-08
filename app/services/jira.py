"""
Jira Integration Service.

Handles OAuth 2.0 authentication and API calls to Jira/Atlassian Cloud.
Syncs Jira issues bidirectionally with internal Tasks.
"""
import httpx
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.integration import Integration, IntegrationProvider

logger = logging.getLogger(__name__)


class JiraError(Exception):
    """Jira API error."""
    pass


class JiraService:
    """Handles Jira OAuth and API operations."""

    AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
    TOKEN_URL = "https://auth.atlassian.com/oauth/token"
    API_BASE_URL = "https://api.atlassian.com"

    def __init__(self):
        self.client_id = settings.JIRA_CLIENT_ID
        self.client_secret = settings.JIRA_CLIENT_SECRET
        self.redirect_uri = settings.JIRA_REDIRECT_URI
        self.scopes = settings.JIRA_SCOPES

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def get_authorization_url(self, state: str) -> str:
        """Generate OAuth authorization URL."""
        params = {
            "audience": "api.atlassian.com",
            "client_id": self.client_id,
            "scope": self.scopes,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code_for_tokens(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access and refresh tokens."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
            )

            if response.status_code != 200:
                logger.error(f"Jira token exchange failed: {response.text}")
                raise JiraError(f"Token exchange failed: {response.status_code}")

            data = response.json()
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token"),
                "expires_in": data.get("expires_in", 3600),
                "token_type": data.get("token_type", "Bearer"),
            }

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired access token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                },
            )

            if response.status_code != 200:
                logger.error(f"Jira token refresh failed: {response.text}")
                raise JiraError("Token refresh failed")

            data = response.json()
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "expires_in": data.get("expires_in", 3600),
            }

    async def get_accessible_resources(self, access_token: str) -> List[Dict]:
        """Get list of Jira sites accessible to the user."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/oauth/token/accessible-resources",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code != 200:
                raise JiraError(f"Failed to get accessible resources: {response.status_code}")

            return response.json()

    async def get_issue(
        self, access_token: str, cloud_id: str, issue_key: str
    ) -> Dict[str, Any]:
        """Get a single Jira issue."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"expand": "changelog,transitions"},
            )

            if response.status_code != 200:
                raise JiraError(f"Failed to get issue {issue_key}: {response.status_code}")

            return response.json()

    async def search_issues(
        self,
        access_token: str,
        cloud_id: str,
        jql: str,
        start_at: int = 0,
        max_results: int = 50,
    ) -> Dict[str, Any]:
        """Search for issues using JQL."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/ex/jira/{cloud_id}/rest/api/3/search",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": max_results,
                    "fields": [
                        "summary", "description", "status", "priority",
                        "assignee", "reporter", "created", "updated",
                        "duedate", "labels", "project", "issuetype",
                    ],
                },
            )

            if response.status_code != 200:
                raise JiraError(f"Search failed: {response.status_code}")

            return response.json()

    async def create_issue(
        self,
        access_token: str,
        cloud_id: str,
        project_key: str,
        issue_type: str,
        summary: str,
        description: Optional[str] = None,
        assignee_id: Optional[str] = None,
        priority: Optional[str] = None,
        due_date: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new Jira issue."""
        fields = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type},
            "summary": summary,
        }

        if description:
            # Atlassian Document Format (ADF)
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }

        if assignee_id:
            fields["assignee"] = {"id": assignee_id}

        if priority:
            fields["priority"] = {"name": priority}

        if due_date:
            fields["duedate"] = due_date

        if labels:
            fields["labels"] = labels

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/ex/jira/{cloud_id}/rest/api/3/issue",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"fields": fields},
            )

            if response.status_code not in (200, 201):
                logger.error(f"Failed to create issue: {response.text}")
                raise JiraError(f"Failed to create issue: {response.status_code}")

            return response.json()

    async def update_issue(
        self,
        access_token: str,
        cloud_id: str,
        issue_key: str,
        fields: Dict[str, Any],
    ) -> None:
        """Update a Jira issue."""
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{self.API_BASE_URL}/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"fields": fields},
            )

            if response.status_code not in (200, 204):
                logger.error(f"Failed to update issue: {response.text}")
                raise JiraError(f"Failed to update issue: {response.status_code}")

    async def transition_issue(
        self,
        access_token: str,
        cloud_id: str,
        issue_key: str,
        transition_id: str,
    ) -> None:
        """Transition an issue to a new status."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/transitions",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"transition": {"id": transition_id}},
            )

            if response.status_code not in (200, 204):
                raise JiraError(f"Failed to transition issue: {response.status_code}")

    async def get_transitions(
        self, access_token: str, cloud_id: str, issue_key: str
    ) -> List[Dict]:
        """Get available transitions for an issue."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/transitions",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code != 200:
                raise JiraError(f"Failed to get transitions: {response.status_code}")

            return response.json().get("transitions", [])


class JiraIntegrationService:
    """Manages Jira integrations for organizations."""

    def __init__(self):
        self.jira_service = JiraService()

    async def get_integration(
        self, session: AsyncSession, organization_id: int
    ) -> Optional[Integration]:
        """Get Jira integration for an organization."""
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == organization_id,
                Integration.provider == IntegrationProvider.JIRA.value,
                Integration.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def create_integration(
        self,
        session: AsyncSession,
        organization_id: int,
        user_id: int,
        tokens: Dict[str, Any],
        cloud_id: str,
        site_name: str,
    ) -> Integration:
        """Create or update Jira integration."""
        from app.models.integration import SyncStatus

        # Check for existing
        existing = await self.get_integration(session, organization_id)

        if existing:
            # Update existing
            existing.access_token = tokens["access_token"]
            existing.refresh_token = tokens.get("refresh_token")
            existing.token_expires_at = datetime.utcnow() + timedelta(
                seconds=tokens.get("expires_in", 3600)
            )
            existing.status = SyncStatus.IDLE
            existing.provider_data = {
                "cloud_id": cloud_id,
                "site_name": site_name,
            }
            await session.commit()
            return existing

        # Create new
        integration = Integration(
            organization_id=organization_id,
            connected_by_id=user_id,
            provider=IntegrationProvider.JIRA,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_expires_at=datetime.utcnow() + timedelta(
                seconds=tokens.get("expires_in", 3600)
            ),
            status=SyncStatus.IDLE,
            provider_data={
                "cloud_id": cloud_id,
                "site_name": site_name,
            },
        )
        session.add(integration)
        await session.commit()
        return integration

    async def get_or_refresh_token(
        self, session: AsyncSession, integration: Integration
    ) -> str:
        """Get a valid access token, refreshing if necessary."""
        if integration.token_expires_at and integration.token_expires_at > datetime.utcnow():
            return integration.access_token

        if not integration.refresh_token:
            raise JiraError("No refresh token available")

        # Refresh the token
        tokens = await self.jira_service.refresh_access_token(integration.refresh_token)

        integration.access_token = tokens["access_token"]
        integration.refresh_token = tokens.get("refresh_token", integration.refresh_token)
        integration.token_expires_at = datetime.utcnow() + timedelta(
            seconds=tokens.get("expires_in", 3600)
        )

        await session.commit()
        return integration.access_token

    async def disconnect(self, session: AsyncSession, organization_id: int) -> bool:
        """Disconnect Jira integration."""
        from app.models.integration import SyncStatus

        integration = await self.get_integration(session, organization_id)
        if integration:
            integration.status = SyncStatus.DISCONNECTED
            integration.access_token = None
            integration.refresh_token = None
            await session.commit()
            return True
        return False


# Singleton instances
jira_service = JiraService()
jira_integration_service = JiraIntegrationService()

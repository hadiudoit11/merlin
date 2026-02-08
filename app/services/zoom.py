"""
Zoom integration service.

Handles:
- OAuth flow (connect/disconnect)
- Fetching recordings and transcripts
- Managing meeting imports
"""
import httpx
import base64
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from urllib.parse import urlencode
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.models.integration import Integration, IntegrationProvider, SyncStatus, MeetingImport


class ZoomError(Exception):
    """Raised when Zoom API calls fail."""
    pass


class ZoomService:
    """Service for Zoom API interactions."""

    OAUTH_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
    OAUTH_TOKEN_URL = "https://zoom.us/oauth/token"
    API_BASE_URL = "https://api.zoom.us/v2"

    def __init__(self):
        self.client_id = settings.ZOOM_CLIENT_ID
        self.client_secret = settings.ZOOM_CLIENT_SECRET
        self.redirect_uri = settings.ZOOM_REDIRECT_URI
        self.scopes = settings.ZOOM_SCOPES

    @property
    def is_configured(self) -> bool:
        return settings.ZOOM_CONFIGURED

    def get_authorization_url(self, state: str) -> str:
        """Generate OAuth authorization URL."""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        return f"{self.OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code_for_tokens(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access and refresh tokens."""
        # Zoom requires Basic auth for token exchange
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                headers=headers,
                data=data,
            )

            if response.status_code != 200:
                raise ZoomError(f"Token exchange failed: {response.text}")

            return response.json()

    async def refresh_tokens(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh expired access token."""
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                headers=headers,
                data=data,
            )

            if response.status_code != 200:
                raise ZoomError(f"Token refresh failed: {response.text}")

            return response.json()

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        access_token: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make authenticated request to Zoom API."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        url = f"{self.API_BASE_URL}{endpoint}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )

            if response.status_code == 401:
                raise ZoomError("Access token expired or invalid")
            elif response.status_code != 200:
                raise ZoomError(f"API error {response.status_code}: {response.text}")

            return response.json()

    async def get_current_user(self, access_token: str) -> Dict[str, Any]:
        """Get current authenticated user info."""
        return await self._make_request("GET", "/users/me", access_token)

    async def list_recordings(
        self,
        access_token: str,
        user_id: str = "me",
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        page_size: int = 30,
    ) -> Dict[str, Any]:
        """
        List cloud recordings for a user.

        Returns meetings with their recordings.
        """
        params = {"page_size": page_size}

        if from_date:
            params["from"] = from_date.strftime("%Y-%m-%d")
        else:
            # Default to last 30 days
            params["from"] = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

        if to_date:
            params["to"] = to_date.strftime("%Y-%m-%d")

        return await self._make_request(
            "GET",
            f"/users/{user_id}/recordings",
            access_token,
            params=params,
        )

    async def get_meeting_recordings(
        self,
        access_token: str,
        meeting_id: str,
    ) -> Dict[str, Any]:
        """Get recordings for a specific meeting."""
        return await self._make_request(
            "GET",
            f"/meetings/{meeting_id}/recordings",
            access_token,
        )

    async def get_recording_transcript(
        self,
        access_token: str,
        meeting_id: str,
        recording_id: str,
    ) -> Optional[str]:
        """
        Get transcript for a recording.

        Note: Transcript must be available (cloud recording with transcript enabled).
        Returns the VTT or plain text transcript.
        """
        try:
            # Get download access token for the transcript file
            recordings = await self.get_meeting_recordings(access_token, meeting_id)

            transcript_url = None
            for file in recordings.get("recording_files", []):
                if file.get("file_type") == "TRANSCRIPT":
                    transcript_url = file.get("download_url")
                    break

            if not transcript_url:
                return None

            # Download the transcript
            # Zoom requires the access token as a query param for downloads
            download_url = f"{transcript_url}?access_token={access_token}"

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(download_url)

                if response.status_code != 200:
                    return None

                return response.text

        except Exception:
            return None

    async def list_meetings(
        self,
        access_token: str,
        user_id: str = "me",
        meeting_type: str = "scheduled",
        page_size: int = 30,
    ) -> Dict[str, Any]:
        """List meetings for a user."""
        params = {
            "type": meeting_type,
            "page_size": page_size,
        }

        return await self._make_request(
            "GET",
            f"/users/{user_id}/meetings",
            access_token,
            params=params,
        )

    async def get_meeting(
        self,
        access_token: str,
        meeting_id: str,
    ) -> Dict[str, Any]:
        """Get meeting details."""
        return await self._make_request(
            "GET",
            f"/meetings/{meeting_id}",
            access_token,
        )

    async def get_past_meeting_participants(
        self,
        access_token: str,
        meeting_id: str,
    ) -> Dict[str, Any]:
        """Get participants from a past meeting."""
        try:
            return await self._make_request(
                "GET",
                f"/past_meetings/{meeting_id}/participants",
                access_token,
            )
        except ZoomError:
            return {"participants": []}


class ZoomIntegrationService:
    """Service for managing Zoom integration state."""

    def __init__(self):
        self.zoom = ZoomService()

    async def get_integration(
        self,
        session: AsyncSession,
        organization_id: int,
    ) -> Optional[Integration]:
        """Get Zoom integration for an organization."""
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == organization_id,
                Integration.provider == IntegrationProvider.ZOOM,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_refresh_token(
        self,
        session: AsyncSession,
        integration: Integration,
    ) -> str:
        """Get valid access token, refreshing if expired."""
        if integration.is_token_expired and integration.refresh_token:
            try:
                tokens = await self.zoom.refresh_tokens(integration.refresh_token)

                integration.access_token = tokens["access_token"]
                integration.refresh_token = tokens.get("refresh_token", integration.refresh_token)
                integration.token_expires_at = datetime.utcnow() + timedelta(
                    seconds=tokens.get("expires_in", 3600)
                )
                integration.status = SyncStatus.IDLE

                await session.commit()

            except ZoomError as e:
                integration.status = SyncStatus.DISCONNECTED
                integration.last_error = str(e)
                await session.commit()
                raise

        return integration.access_token

    async def create_integration(
        self,
        session: AsyncSession,
        organization_id: int,
        user_id: int,
        tokens: Dict[str, Any],
    ) -> Integration:
        """Create or update Zoom integration with OAuth tokens."""
        # Check if integration already exists
        integration = await self.get_integration(session, organization_id)

        if integration:
            # Update existing
            integration.access_token = tokens["access_token"]
            integration.refresh_token = tokens.get("refresh_token")
            integration.token_expires_at = datetime.utcnow() + timedelta(
                seconds=tokens.get("expires_in", 3600)
            )
            integration.status = SyncStatus.IDLE
            integration.connected_by_id = user_id
        else:
            # Create new
            integration = Integration(
                organization_id=organization_id,
                provider=IntegrationProvider.ZOOM,
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token"),
                token_expires_at=datetime.utcnow() + timedelta(
                    seconds=tokens.get("expires_in", 3600)
                ),
                status=SyncStatus.IDLE,
                connected_by_id=user_id,
            )
            session.add(integration)

        await session.commit()
        await session.refresh(integration)
        return integration

    async def disconnect(
        self,
        session: AsyncSession,
        organization_id: int,
    ) -> bool:
        """Disconnect Zoom integration."""
        integration = await self.get_integration(session, organization_id)

        if integration:
            integration.access_token = None
            integration.refresh_token = None
            integration.status = SyncStatus.DISCONNECTED
            await session.commit()
            return True

        return False

    async def list_available_recordings(
        self,
        session: AsyncSession,
        organization_id: int,
        from_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """List recordings available for import."""
        integration = await self.get_integration(session, organization_id)

        if not integration or not integration.is_connected:
            raise ZoomError("Zoom not connected")

        access_token = await self.get_or_refresh_token(session, integration)

        recordings = await self.zoom.list_recordings(
            access_token,
            from_date=from_date,
        )

        # Format for frontend
        meetings = []
        for meeting in recordings.get("meetings", []):
            has_transcript = any(
                f.get("file_type") == "TRANSCRIPT"
                for f in meeting.get("recording_files", [])
            )

            meetings.append({
                "id": meeting.get("uuid"),
                "meeting_id": meeting.get("id"),
                "topic": meeting.get("topic"),
                "start_time": meeting.get("start_time"),
                "duration": meeting.get("duration"),
                "host_email": meeting.get("host_email"),
                "has_transcript": has_transcript,
                "recording_count": len(meeting.get("recording_files", [])),
            })

        return meetings

    async def import_meeting(
        self,
        session: AsyncSession,
        organization_id: int,
        meeting_uuid: str,
        canvas_id: Optional[int] = None,
    ) -> MeetingImport:
        """
        Import a meeting's recording and transcript.

        Creates a MeetingImport record for async processing.
        """
        integration = await self.get_integration(session, organization_id)

        if not integration or not integration.is_connected:
            raise ZoomError("Zoom not connected")

        access_token = await self.get_or_refresh_token(session, integration)

        # Get meeting details
        recordings = await self.zoom.get_meeting_recordings(access_token, meeting_uuid)

        # Get transcript if available
        transcript = None
        recording_id = None
        for file in recordings.get("recording_files", []):
            if file.get("file_type") == "TRANSCRIPT":
                recording_id = file.get("id")
                transcript = await self.zoom.get_recording_transcript(
                    access_token,
                    meeting_uuid,
                    recording_id,
                )
                break

        # Get participants
        participants = await self.zoom.get_past_meeting_participants(
            access_token, meeting_uuid
        )

        # Create import record
        meeting_import = MeetingImport(
            integration_id=integration.id,
            canvas_id=canvas_id,
            external_meeting_id=meeting_uuid,
            meeting_topic=recordings.get("topic"),
            meeting_start_time=datetime.fromisoformat(
                recordings.get("start_time", "").replace("Z", "+00:00")
            ) if recordings.get("start_time") else None,
            meeting_duration_minutes=recordings.get("duration"),
            meeting_host=recordings.get("host_email"),
            meeting_participants=[
                p.get("name") or p.get("user_email")
                for p in participants.get("participants", [])
            ],
            recording_id=recording_id,
            transcript_raw=transcript,
            status="pending" if transcript else "no_transcript",
        )

        session.add(meeting_import)
        await session.commit()
        await session.refresh(meeting_import)

        return meeting_import


# Singleton instances
zoom_service = ZoomService()
zoom_integration_service = ZoomIntegrationService()

"""API endpoints for Zoom integration."""
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, Request, Header
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import secrets
import hashlib
import hmac
import logging

from app.core.database import get_session, async_session_maker
from app.core.config import settings
from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User
from app.models.task import InputEvent
from app.services.zoom import zoom_service, zoom_skill_service, ZoomError
from app.services.transcript_processor import meeting_import_processor, TranscriptProcessingError
from app.services.settings_service import SettingsService
from app.services.input_processor import create_zoom_pipeline, JobContext

logger = logging.getLogger(__name__)

router = APIRouter()

# Store OAuth states temporarily (in production, use Redis)
_oauth_states: dict = {}


# ============ Schemas ============

class ZoomConnectionStatus(BaseModel):
    connected: bool
    user_email: Optional[str] = None
    connected_at: Optional[datetime] = None


class RecordingItem(BaseModel):
    id: str
    meeting_id: str
    topic: str
    start_time: Optional[datetime] = None
    duration: Optional[int] = None
    host_email: Optional[str] = None
    has_transcript: bool
    recording_count: int


class RecordingsResponse(BaseModel):
    recordings: List[RecordingItem]
    total: int


class ImportMeetingRequest(BaseModel):
    meeting_uuid: str
    canvas_id: Optional[int] = None


class MeetingImportResponse(BaseModel):
    id: int
    meeting_topic: Optional[str]
    status: str
    message: str


class ProcessingStatusResponse(BaseModel):
    id: int
    status: str
    meeting_topic: Optional[str]
    summary: Optional[str]
    action_items_count: int
    doc_node_id: Optional[int]
    error: Optional[str]


# ============ Helper Functions ============

async def get_user_org_id(session: AsyncSession, user_id: int) -> int:
    """Get org ID for user, or raise if not in org."""
    org_id = await SettingsService.get_user_organization_id(session, user_id)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Zoom integration requires organization membership"
        )
    return org_id


async def background_process_meeting(meeting_import_id: int, user_id: int):
    """Background task to process a meeting import."""
    async with async_session_maker() as session:
        try:
            await meeting_import_processor.process_meeting_import(
                session, meeting_import_id, user_id
            )
        except Exception as e:
            print(f"Failed to process meeting {meeting_import_id}: {e}")


# ============ OAuth Endpoints ============

@router.get("/connect")
async def connect_zoom(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Start Zoom OAuth flow.

    Returns the authorization URL to redirect the user to.
    """
    if not settings.ZOOM_CONFIGURED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zoom integration not configured"
        )

    org_id = await get_user_org_id(session, current_user.id)

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "user_id": current_user.id,
        "org_id": org_id,
        "created_at": datetime.utcnow(),
    }

    auth_url = zoom_service.get_authorization_url(state)
    return {"authorization_url": auth_url}


@router.get("/callback")
async def zoom_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """
    Handle Zoom OAuth callback.

    Exchanges code for tokens and stores integration.
    """
    # Verify state
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state"
        )

    try:
        # Exchange code for tokens
        tokens = await zoom_service.exchange_code_for_tokens(code)

        # Create/update integration
        await zoom_skill_service.create_skill(
            session,
            organization_id=state_data["org_id"],
            user_id=state_data["user_id"],
            tokens=tokens,
        )

        # Redirect to frontend success page
        frontend_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"
        return RedirectResponse(
            url=f"{frontend_url}/settings/skills?zoom=connected"
        )

    except ZoomError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth failed: {str(e)}"
        )


@router.get("/status", response_model=ZoomConnectionStatus)
async def get_zoom_status(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Check if Zoom is connected for the user's organization."""
    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    if not org_id:
        return ZoomConnectionStatus(connected=False)

    integration = await zoom_skill_service.get_integration(session, org_id)

    if not integration or not integration.is_connected:
        return ZoomConnectionStatus(connected=False)

    # Get user info
    try:
        access_token = await zoom_skill_service.get_or_refresh_token(session, integration)
        user_info = await zoom_service.get_current_user(access_token)

        return ZoomConnectionStatus(
            connected=True,
            user_email=user_info.get("email"),
            connected_at=integration.created_at,
        )
    except ZoomError:
        return ZoomConnectionStatus(connected=False)


@router.delete("/disconnect")
async def disconnect_zoom(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Disconnect Zoom integration."""
    org_id = await get_user_org_id(session, current_user.id)

    disconnected = await zoom_skill_service.disconnect(session, org_id)

    if disconnected:
        return {"status": "disconnected"}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Zoom integration found"
        )


# ============ Recordings Endpoints ============

@router.get("/recordings", response_model=RecordingsResponse)
async def list_recordings(
    days: int = Query(30, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    List available Zoom recordings.

    Returns recordings from the last N days that can be imported.
    """
    org_id = await get_user_org_id(session, current_user.id)

    try:
        from_date = datetime.utcnow().replace(hour=0, minute=0, second=0)
        from_date = from_date.replace(day=from_date.day - days) if from_date.day > days else from_date

        recordings = await zoom_skill_service.list_available_recordings(
            session, org_id, from_date=from_date
        )

        return RecordingsResponse(
            recordings=[RecordingItem(**r) for r in recordings],
            total=len(recordings),
        )

    except ZoomError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e)
        )


# ============ Import Endpoints ============

@router.post("/import", response_model=MeetingImportResponse)
async def import_meeting(
    request: ImportMeetingRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Import a meeting recording.

    Fetches the transcript and queues it for AI processing.
    If canvas_id is provided, creates nodes on that canvas.
    """
    org_id = await get_user_org_id(session, current_user.id)

    try:
        meeting_import = await zoom_skill_service.import_meeting(
            session,
            organization_id=org_id,
            meeting_uuid=request.meeting_uuid,
            canvas_id=request.canvas_id,
        )

        # Queue for processing if transcript available
        if meeting_import.transcript_raw:
            background_tasks.add_task(
                background_process_meeting,
                meeting_import.id,
                current_user.id,
            )
            message = "Meeting imported, processing transcript..."
        else:
            message = "Meeting imported but no transcript available"

        return MeetingImportResponse(
            id=meeting_import.id,
            meeting_topic=meeting_import.meeting_topic,
            status=meeting_import.status,
            message=message,
        )

    except ZoomError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e)
        )


@router.get("/import/{import_id}/status", response_model=ProcessingStatusResponse)
async def get_import_status(
    import_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Check the processing status of an imported meeting."""
    from sqlalchemy import select
    from app.models.skill import MeetingImport

    result = await session.execute(
        select(MeetingImport).where(MeetingImport.id == import_id)
    )
    meeting_import = result.scalar_one_or_none()

    if not meeting_import:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import not found"
        )

    return ProcessingStatusResponse(
        id=meeting_import.id,
        status=meeting_import.status,
        meeting_topic=meeting_import.meeting_topic,
        summary=meeting_import.summary,
        action_items_count=len(meeting_import.action_items or []),
        doc_node_id=meeting_import.doc_node_id,
        error=meeting_import.processing_error,
    )


@router.post("/import/{import_id}/process", response_model=ProcessingStatusResponse)
async def process_import(
    import_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Manually trigger processing for an imported meeting.

    Use this to retry failed imports or process meetings that weren't auto-processed.
    """
    from sqlalchemy import select
    from app.models.skill import MeetingImport

    result = await session.execute(
        select(MeetingImport).where(MeetingImport.id == import_id)
    )
    meeting_import = result.scalar_one_or_none()

    if not meeting_import:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import not found"
        )

    if not meeting_import.transcript_raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No transcript available to process"
        )

    # Queue for processing
    background_tasks.add_task(
        background_process_meeting,
        meeting_import.id,
        current_user.id,
    )

    return ProcessingStatusResponse(
        id=meeting_import.id,
        status="processing",
        meeting_topic=meeting_import.meeting_topic,
        summary=None,
        action_items_count=0,
        doc_node_id=None,
        error=None,
    )


# ============ Webhook Endpoints ============

class WebhookResponse(BaseModel):
    status: str
    message: str
    event_id: Optional[int] = None


def verify_zoom_webhook(payload: bytes, signature: str, timestamp: str) -> bool:
    """Verify Zoom webhook signature."""
    if not settings.ZOOM_WEBHOOK_SECRET_TOKEN:
        logger.warning("ZOOM_WEBHOOK_SECRET_TOKEN not configured, skipping verification")
        return True

    message = f"v0:{timestamp}:{payload.decode('utf-8')}"
    expected_signature = hmac.new(
        settings.ZOOM_WEBHOOK_SECRET_TOKEN.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    expected = f"v0={expected_signature}"
    return hmac.compare_digest(signature, expected)


async def process_zoom_webhook_event(event_id: int, org_id: int, user_id: int):
    """Background task to process a Zoom webhook event through the pipeline."""
    async with async_session_maker() as session:
        try:
            # Get the input event
            result = await session.execute(
                select(InputEvent).where(InputEvent.id == event_id)
            )
            input_event = result.scalar_one_or_none()

            if not input_event:
                logger.error(f"InputEvent {event_id} not found")
                return

            # Get integration for org
            integration = await zoom_skill_service.get_integration(session, org_id)

            # Build context
            payload = input_event.payload or {}
            meeting_data = payload.get("payload", {}).get("object", {})

            context = JobContext(
                session=session,
                user_id=user_id,
                organization_id=org_id,
                input_event=input_event,
                integration=integration,
                metadata={
                    "meeting_id": meeting_data.get("id"),
                    "meeting_uuid": meeting_data.get("uuid"),
                    "topic": meeting_data.get("topic", "Meeting"),
                    "start_time": meeting_data.get("start_time"),
                    "duration": meeting_data.get("duration"),
                    "host_id": meeting_data.get("host_id"),
                    "host_email": meeting_data.get("host_email"),
                    "participants": [],  # Could be fetched from Zoom API
                },
            )

            # Fetch transcript if meeting ended
            if input_event.event_type in ["meeting.ended", "recording.completed"]:
                try:
                    meeting_uuid = meeting_data.get("uuid")
                    if meeting_uuid and integration:
                        access_token = await zoom_skill_service.get_or_refresh_token(
                            session, integration
                        )
                        transcript = await zoom_service.get_meeting_transcript(
                            access_token, meeting_uuid
                        )
                        context.raw_content = transcript or ""
                except Exception as e:
                    logger.warning(f"Failed to fetch transcript: {e}")
                    context.raw_content = ""

            # Run the pipeline
            pipeline = create_zoom_pipeline()
            result = await pipeline.process(context)

            logger.info(f"Processed Zoom event {event_id}: {result}")

        except Exception as e:
            logger.exception(f"Failed to process Zoom webhook event {event_id}")
            # Update event status
            try:
                result = await session.execute(
                    select(InputEvent).where(InputEvent.id == event_id)
                )
                input_event = result.scalar_one_or_none()
                if input_event:
                    input_event.status = "failed"
                    input_event.processing_error = str(e)
                    await session.commit()
            except Exception:
                pass


@router.post("/webhook", response_model=WebhookResponse)
async def zoom_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    x_zm_signature: Optional[str] = Header(None, alias="x-zm-signature"),
    x_zm_request_timestamp: Optional[str] = Header(None, alias="x-zm-request-timestamp"),
):
    """
    Handle Zoom webhook events.

    Events supported:
    - meeting.ended: Triggers transcript processing
    - recording.completed: Triggers transcript processing for cloud recordings
    - endpoint.url_validation: Validates webhook URL with Zoom

    Zoom will send a challenge during webhook URL validation that we must echo back.
    """
    body = await request.body()
    payload: Dict[str, Any] = await request.json()

    # Handle URL validation (Zoom challenge-response)
    if payload.get("event") == "endpoint.url_validation":
        plain_token = payload.get("payload", {}).get("plainToken", "")

        if settings.ZOOM_WEBHOOK_SECRET_TOKEN:
            hash_obj = hmac.new(
                settings.ZOOM_WEBHOOK_SECRET_TOKEN.encode('utf-8'),
                plain_token.encode('utf-8'),
                hashlib.sha256
            )
            encrypted_token = hash_obj.hexdigest()
        else:
            encrypted_token = plain_token

        return {
            "plainToken": plain_token,
            "encryptedToken": encrypted_token,
        }

    # Verify webhook signature for other events
    if x_zm_signature and x_zm_request_timestamp:
        if not verify_zoom_webhook(body, x_zm_signature, x_zm_request_timestamp):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )

    event_type = payload.get("event", "unknown")
    account_id = payload.get("payload", {}).get("account_id")

    # Find organization by Zoom account
    from app.models.skill import Skill, SkillProvider

    result = await session.execute(
        select(Skill).where(
            Skill.provider == SkillProvider.ZOOM.value,
            Skill.is_active == True,
        )
    )
    skills = list(result.scalars().all())

    # Match by account_id in settings
    org_id = None
    user_id = None
    for skill in skills:
        if skill.settings and skill.settings.get("account_id") == account_id:
            org_id = skill.organization_id
            user_id = skill.user_id
            break

    if not org_id and skills:
        # Fall back to first active skill
        skill = skills[0]
        org_id = skill.organization_id
        user_id = skill.user_id

    # Only process meeting-related events
    supported_events = ["meeting.ended", "recording.completed"]

    if event_type not in supported_events:
        return WebhookResponse(
            status="ignored",
            message=f"Event type '{event_type}' not processed"
        )

    if not org_id:
        logger.warning(f"No organization found for Zoom account {account_id}")
        return WebhookResponse(
            status="ignored",
            message="No matching organization found"
        )

    # Create InputEvent record
    meeting_data = payload.get("payload", {}).get("object", {})

    input_event = InputEvent(
        integration_id=integration.id if integration else None,
        source_type="zoom",
        event_type=event_type,
        external_id=meeting_data.get("uuid"),
        payload=payload,
        status="pending",
        organization_id=org_id,
    )
    session.add(input_event)
    await session.flush()

    # Queue for background processing
    background_tasks.add_task(
        process_zoom_webhook_event,
        input_event.id,
        org_id,
        user_id,
    )

    return WebhookResponse(
        status="accepted",
        message=f"Event '{event_type}' queued for processing",
        event_id=input_event.id,
    )

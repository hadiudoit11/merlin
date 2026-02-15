"""API endpoints for Jira integration."""
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
from app.models.task import InputEvent, Task
from app.models.integration import Integration, IntegrationProvider
from app.services.jira import jira_service, jira_integration_service, JiraError
from app.services.jira_processor import (
    create_jira_webhook_pipeline,
    create_jira_import_pipeline,
    create_jira_push_pipeline,
)
from app.services.input_processor import JobContext
from app.services.settings_service import SettingsService

router = APIRouter()
logger = logging.getLogger(__name__)

# Store OAuth states temporarily (in production, use Redis)
_oauth_states: dict = {}


# ============ Schemas ============

class JiraConnectionInfo(BaseModel):
    connected: bool
    site_name: Optional[str] = None
    cloud_id: Optional[str] = None
    connected_at: Optional[str] = None
    connected_by_id: Optional[int] = None


class JiraConnectionStatus(BaseModel):
    """Hybrid connection status showing both org and personal connections."""
    connected: bool
    active_scope: Optional[str] = None  # "organization" or "personal"
    organization: Optional[JiraConnectionInfo] = None
    personal: Optional[JiraConnectionInfo] = None
    # Legacy fields for backwards compatibility
    site_name: Optional[str] = None
    cloud_id: Optional[str] = None
    connected_at: Optional[datetime] = None


class JiraSite(BaseModel):
    id: str
    name: str
    url: str


class SitesResponse(BaseModel):
    sites: List[JiraSite]


class ImportRequest(BaseModel):
    jql: str
    canvas_id: Optional[int] = None


class ImportResponse(BaseModel):
    status: str
    message: str
    imported: int = 0
    updated: int = 0


class PushToJiraRequest(BaseModel):
    task_id: int
    project_key: str
    issue_type: str = "Task"


class PushToJiraResponse(BaseModel):
    status: str
    message: str
    issue_key: Optional[str] = None
    issue_url: Optional[str] = None


class WebhookResponse(BaseModel):
    status: str
    message: str
    event_id: Optional[int] = None


# ============ Helper Functions ============

async def get_user_org_id(session: AsyncSession, user_id: int) -> int:
    """Get org ID for user, or raise if not in org."""
    org_id = await SettingsService.get_user_organization_id(session, user_id)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Jira integration requires organization membership"
        )
    return org_id


async def process_jira_webhook_event(event_id: int, org_id: int, user_id: int):
    """Background task to process a Jira webhook event."""
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

            # Get integration
            integration = await jira_integration_service.get_integration(session, org_id)

            # Build context
            payload = input_event.payload or {}
            issue = payload.get("issue", {})
            webhook_event = payload.get("webhookEvent", "")

            context = JobContext(
                session=session,
                user_id=user_id,
                organization_id=org_id,
                input_event=input_event,
                integration=integration,
                metadata={
                    "event_type": webhook_event,
                    "issue": issue,
                    "issue_key": issue.get("key"),
                    "cloud_id": integration.provider_data.get("cloud_id") if integration else None,
                },
            )

            # Run the pipeline
            pipeline = create_jira_webhook_pipeline()
            result = await pipeline.process(context)

            logger.info(f"Processed Jira event {event_id}: {result}")

        except Exception as e:
            logger.exception(f"Failed to process Jira webhook event {event_id}")
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


# ============ OAuth Endpoints ============

@router.get("/connect")
async def connect_jira(
    scope: str = Query("organization", description="Connection scope: 'organization' or 'personal'"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Start Jira OAuth flow.

    Args:
        scope: "organization" for shared org connection, "personal" for user-specific
    """
    if not settings.JIRA_CONFIGURED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Jira integration not configured"
        )

    if scope not in ("organization", "personal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope must be 'organization' or 'personal'"
        )

    org_id = await get_user_org_id(session, current_user.id)

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "user_id": current_user.id,
        "org_id": org_id,
        "scope": scope,
        "created_at": datetime.utcnow(),
    }

    auth_url = jira_service.get_authorization_url(state)
    return {"authorization_url": auth_url}


@router.get("/callback")
async def jira_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Handle Jira OAuth callback."""
    # Verify state
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state"
        )

    try:
        # Exchange code for tokens
        tokens = await jira_service.exchange_code_for_tokens(code)

        # Get accessible resources (Jira sites)
        resources = await jira_service.get_accessible_resources(tokens["access_token"])

        if not resources:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No Jira sites accessible"
            )

        # Use the first site (or could prompt user to choose)
        site = resources[0]

        # Create/update integration with the specified scope
        scope = state_data.get("scope", "organization")
        await jira_integration_service.create_integration(
            session,
            organization_id=state_data["org_id"],
            user_id=state_data["user_id"],
            tokens=tokens,
            cloud_id=site["id"],
            site_name=site["name"],
            scope=scope,
        )

        # Redirect to frontend success page
        frontend_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"
        return RedirectResponse(
            url=f"{frontend_url}/integrations?jira=connected&scope={scope}"
        )

    except JiraError as e:
        frontend_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"
        return RedirectResponse(
            url=f"{frontend_url}/integrations?jira=error&message={str(e)}"
        )


@router.get("/status", response_model=JiraConnectionStatus)
async def get_jira_status(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Check Jira connection status.

    Returns hybrid status showing both organization and personal connections,
    plus which one is currently active (personal takes priority).
    """
    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    if not org_id:
        return JiraConnectionStatus(connected=False)

    # Get detailed hybrid status
    status = await jira_integration_service.get_connection_status(
        session, org_id, current_user.id
    )

    # Build response with both connections
    org_info = None
    personal_info = None

    if status.get("organization"):
        org_info = JiraConnectionInfo(**status["organization"])

    if status.get("personal"):
        personal_info = JiraConnectionInfo(**status["personal"])

    # Get active integration for legacy fields
    active_integration = await jira_integration_service.get_integration(
        session, org_id, user_id=current_user.id
    )

    return JiraConnectionStatus(
        connected=status["connected"],
        active_scope=status["active_scope"],
        organization=org_info,
        personal=personal_info,
        # Legacy fields for backwards compatibility
        site_name=active_integration.provider_data.get("site_name") if active_integration else None,
        cloud_id=active_integration.provider_data.get("cloud_id") if active_integration else None,
        connected_at=active_integration.created_at if active_integration else None,
    )


@router.delete("/disconnect")
async def disconnect_jira(
    scope: str = Query("organization", description="Which connection to disconnect: 'organization' or 'personal'"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Disconnect Jira integration.

    Args:
        scope: "organization" to disconnect org connection, "personal" for user-specific
    """
    if scope not in ("organization", "personal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope must be 'organization' or 'personal'"
        )

    org_id = await get_user_org_id(session, current_user.id)

    disconnected = await jira_integration_service.disconnect(
        session,
        org_id,
        user_id=current_user.id,
        scope=scope,
    )

    if disconnected:
        return {"status": "disconnected", "scope": scope}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {scope} Jira integration found"
        )


# ============ Import/Sync Endpoints ============

@router.post("/import", response_model=ImportResponse)
async def import_from_jira(
    request: ImportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Import issues from Jira using a JQL query.

    Uses the active Jira connection (personal takes priority over org).

    Example JQL queries:
    - "project = PROJ AND status != Done"
    - "assignee = currentUser() AND updated >= -7d"
    - "labels = 'action-item'"
    """
    org_id = await get_user_org_id(session, current_user.id)

    # Use fallback chain: personal → org
    integration = await jira_integration_service.get_integration(
        session, org_id, user_id=current_user.id
    )
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Jira integration found"
        )

    # Create input event for tracking
    input_event = InputEvent(
        integration_id=integration.id,
        source_type="jira",
        event_type="bulk_import",
        payload={"jql": request.jql, "canvas_id": request.canvas_id},
        status="pending",
        organization_id=org_id,
    )
    session.add(input_event)
    await session.flush()

    # Build context and run pipeline
    context = JobContext(
        session=session,
        user_id=current_user.id,
        organization_id=org_id,
        input_event=input_event,
        integration=integration,
        canvas_id=request.canvas_id,
        metadata={
            "jql": request.jql,
        },
    )

    try:
        pipeline = create_jira_import_pipeline()
        result = await pipeline.process(context)

        return ImportResponse(
            status="completed",
            message=result.get("status", "Import completed"),
            imported=len([t for t in context.created_tasks if t.metadata.get("synced_at")]),
            updated=0,  # Would need to track this separately
        )

    except Exception as e:
        logger.error(f"Jira import failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/push", response_model=PushToJiraResponse)
async def push_task_to_jira(
    request: PushToJiraRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Push an internal task to Jira as a new issue.

    Uses the active Jira connection (personal takes priority over org).
    """
    org_id = await get_user_org_id(session, current_user.id)

    # Use fallback chain: personal → org
    integration = await jira_integration_service.get_integration(
        session, org_id, user_id=current_user.id
    )
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Jira integration found"
        )

    # Verify task belongs to org
    result = await session.execute(
        select(Task).where(
            Task.id == request.task_id,
            Task.organization_id == org_id,
        )
    )
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    # Create input event for tracking
    input_event = InputEvent(
        integration_id=integration.id,
        source_type="jira",
        event_type="push_to_jira",
        payload={"task_id": request.task_id, "project_key": request.project_key},
        status="pending",
        organization_id=org_id,
    )
    session.add(input_event)
    await session.flush()

    # Build context and run pipeline
    context = JobContext(
        session=session,
        user_id=current_user.id,
        organization_id=org_id,
        input_event=input_event,
        integration=integration,
        metadata={
            "task_id": request.task_id,
            "project_key": request.project_key,
            "issue_type": request.issue_type,
        },
    )

    try:
        pipeline = create_jira_push_pipeline()
        result = await pipeline.process(context)

        # Get the created issue key
        job_results = input_event.results or {}
        push_result = job_results.get("push_task_to_jira", {})

        cloud_id = integration.provider_data.get("cloud_id")
        issue_key = None
        issue_url = None

        # Find the issue key from created tasks metadata
        if context.created_tasks:
            task = context.created_tasks[0]
            issue_key = task.source_id
            if issue_key and cloud_id:
                site_name = integration.provider_data.get("site_name", "")
                issue_url = f"https://{site_name}.atlassian.net/browse/{issue_key}"

        return PushToJiraResponse(
            status="completed",
            message=f"Created Jira issue {issue_key}" if issue_key else "Push completed",
            issue_key=issue_key,
            issue_url=issue_url,
        )

    except Exception as e:
        logger.error(f"Push to Jira failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ============ Webhook Endpoint ============

@router.post("/webhook", response_model=WebhookResponse)
async def jira_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """
    Handle Jira webhook events.

    Supported events:
    - jira:issue_created
    - jira:issue_updated
    - jira:issue_deleted
    """
    payload: Dict[str, Any] = await request.json()

    webhook_event = payload.get("webhookEvent", "unknown")
    issue = payload.get("issue", {})
    issue_key = issue.get("key")

    # Supported events
    supported_events = [
        "jira:issue_created",
        "jira:issue_updated",
        "jira:issue_deleted",
    ]

    if webhook_event not in supported_events:
        return WebhookResponse(
            status="ignored",
            message=f"Event type '{webhook_event}' not processed"
        )

    # Try to find the organization from issue's project
    # This is tricky - we need to match by cloud_id or project key
    # For now, we'll look at all Jira integrations

    result = await session.execute(
        select(Integration).where(
            Integration.provider == IntegrationProvider.JIRA,
            Integration.status != "disconnected",
        )
    )
    integrations = list(result.scalars().all())

    if not integrations:
        return WebhookResponse(
            status="ignored",
            message="No Jira integrations configured"
        )

    # Use the first matching integration
    # In production, you'd match by cloud_id from the webhook
    integration = integrations[0]
    org_id = integration.organization_id
    user_id = integration.connected_by_id

    # Create InputEvent record
    input_event = InputEvent(
        integration_id=integration.id,
        source_type="jira",
        event_type=webhook_event,
        external_id=issue_key,
        payload=payload,
        status="pending",
        organization_id=org_id,
    )
    session.add(input_event)
    await session.flush()

    # Queue for background processing
    background_tasks.add_task(
        process_jira_webhook_event,
        input_event.id,
        org_id,
        user_id,
    )

    return WebhookResponse(
        status="accepted",
        message=f"Event '{webhook_event}' queued for processing",
        event_id=input_event.id,
    )

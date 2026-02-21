"""API endpoints for Jira integration."""
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, Request, Header
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict
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
from app.models.skill import Skill, SkillProvider
from app.services.jira import jira_service, jira_skill_service, JiraError
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
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=lambda field_name: ''.join(
            word.capitalize() if i > 0 else word
            for i, word in enumerate(field_name.split('_'))
        ),
    )

    connected: bool
    site_name: Optional[str] = None
    cloud_id: Optional[str] = None
    connected_at: Optional[str] = None
    connected_by_id: Optional[int] = None


class JiraConnectionStatus(BaseModel):
    """Hybrid connection status showing both org and personal connections."""
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=lambda field_name: ''.join(
            word.capitalize() if i > 0 else word
            for i, word in enumerate(field_name.split('_'))
        ),
    )

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


class JiraContextSearchRequest(BaseModel):
    query: str
    canvas_id: Optional[int] = None
    top_k: int = 5


class JiraContextSearchResponse(BaseModel):
    issues: List[Dict[str, Any]]
    formatted_context: str


# ============ Helper Functions ============

async def get_user_org_id(session: AsyncSession, user_id: int) -> Optional[int]:
    """Get org ID for user, or None if individual user."""
    return await SettingsService.get_user_organization_id(session, user_id)


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
            integration = await jira_skill_service.get_integration(session, org_id)

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
    scope: str = Query("individual", description="Connection scope: 'individual', 'organization', or 'personal'"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Start Jira OAuth flow.

    Args:
        scope:
          - "individual": Personal integration for users without org membership
          - "organization": Shared org-level connection (requires org membership)
          - "personal": User-specific override within org context (requires org membership)
    """
    if not settings.JIRA_CONFIGURED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Jira integration not configured"
        )

    if scope not in ("individual", "organization", "personal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope must be 'individual', 'organization', or 'personal'"
        )

    org_id = await get_user_org_id(session, current_user.id)

    # Validate scope based on org membership
    if scope in ("organization", "personal") and not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{scope}' scope requires organization membership. Use 'individual' for personal accounts."
        )

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "user_id": current_user.id,
        "org_id": org_id,  # Can be None for individual users
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
        scope = state_data.get("scope", "individual")
        await jira_skill_service.create_integration(
            session,
            user_id=state_data["user_id"],
            tokens=tokens,
            cloud_id=site["id"],
            site_name=site["name"],
            organization_id=state_data.get("org_id"),  # Can be None for individual
            scope=scope,
        )

        # Redirect to frontend success page
        frontend_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"
        return RedirectResponse(
            url=f"{frontend_url}/skills?jira=connected&scope={scope}"
        )

    except JiraError as e:
        frontend_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"
        return RedirectResponse(
            url=f"{frontend_url}/skills?jira=error&message={str(e)}"
        )


@router.get("/status", response_model=JiraConnectionStatus)
async def get_jira_status(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Check Jira connection status.

    For individual users: Returns individual connection status
    For org members: Returns both organization and personal connections,
    plus which one is currently active (personal takes priority).
    """
    org_id = await get_user_org_id(session, current_user.id)

    # Get detailed status (works for both individual and org members)
    connection_status = await jira_skill_service.get_connection_status(
        session, user_id=current_user.id, organization_id=org_id
    )

    # Build response
    org_info = None
    personal_info = None
    individual_info = None

    if connection_status.get("organization"):
        org_info = JiraConnectionInfo(**connection_status["organization"])

    if connection_status.get("personal"):
        personal_info = JiraConnectionInfo(**connection_status["personal"])

    if connection_status.get("individual"):
        individual_info = JiraConnectionInfo(**connection_status["individual"])

    # Get active integration for legacy fields
    active_integration = await jira_skill_service.get_integration(
        session, organization_id=org_id, user_id=current_user.id
    )

    return JiraConnectionStatus(
        connected=connection_status["connected"],
        active_scope=connection_status["active_scope"],
        organization=org_info,
        personal=personal_info if not individual_info else individual_info,  # Map individual to personal for backwards compat
        # Legacy fields for backwards compatibility
        site_name=active_integration.provider_data.get("site_name") if active_integration else None,
        cloud_id=active_integration.provider_data.get("cloud_id") if active_integration else None,
        connected_at=active_integration.created_at if active_integration else None,
    )


@router.delete("/disconnect")
async def disconnect_jira(
    scope: str = Query("individual", description="Which connection to disconnect: 'individual', 'organization', or 'personal'"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Disconnect Jira integration.

    Args:
        scope: "individual", "organization", or "personal"
    """
    if scope not in ("individual", "organization", "personal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope must be 'individual', 'organization', or 'personal'"
        )

    org_id = await get_user_org_id(session, current_user.id)

    # Validate scope based on org membership
    if scope in ("organization", "personal") and not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{scope}' scope requires organization membership"
        )

    disconnected = await jira_skill_service.disconnect(
        session,
        user_id=current_user.id,
        organization_id=org_id,
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

    Uses the active Jira connection (individual/personal takes priority over org).

    Example JQL queries:
    - "project = PROJ AND status != Done"
    - "assignee = currentUser() AND updated >= -7d"
    - "labels = 'action-item'"
    """
    org_id = await get_user_org_id(session, current_user.id)

    # Use fallback chain: individual/personal → org
    integration = await jira_skill_service.get_integration(
        session, organization_id=org_id, user_id=current_user.id
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

    Uses the active Jira connection (individual/personal takes priority over org).
    """
    org_id = await get_user_org_id(session, current_user.id)

    # Use fallback chain: individual/personal → org
    integration = await jira_skill_service.get_integration(
        session, organization_id=org_id, user_id=current_user.id
    )
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Jira integration found"
        )

    # Verify task belongs to user or org
    if org_id:
        # Org member - check org tasks
        result = await session.execute(
            select(Task).where(
                Task.id == request.task_id,
                Task.organization_id == org_id,
            )
        )
    else:
        # Individual user - check user's personal tasks
        result = await session.execute(
            select(Task).where(
                Task.id == request.task_id,
                Task.user_id == current_user.id,
                Task.organization_id == None,
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
    # For now, we'll look at all Jira skills

    result = await session.execute(
        select(Skill).where(
            Skill.provider == SkillProvider.JIRA,
            Skill.status != "disconnected",
        )
    )
    skills = list(result.scalars().all())

    if not skills:
        return WebhookResponse(
            status="ignored",
            message="No Jira skills configured"
        )

    # Use the first matching skill
    # In production, you'd match by cloud_id from the webhook
    skill = skills[0]
    org_id = skill.organization_id
    user_id = skill.connected_by_id

    # Create InputEvent record
    input_event = InputEvent(
        skill_id=skill.id,
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


# ============ AI Context Endpoints ============

@router.post("/index/{canvas_id}")
async def index_jira_issues_for_canvas(
    canvas_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Index all Jira issues on a canvas for AI context.

    This enables semantic search of Jira issues when generating canvas content.
    Call this after importing issues from Jira.

    Args:
        canvas_id: Canvas containing Jira issues to index

    Returns:
        Status and count of indexed issues
    """
    from app.services.jira_context_service import JiraContextService

    org_id = await get_user_org_id(session, current_user.id)

    try:
        result = await JiraContextService.index_jira_issues(
            session, canvas_id, current_user.id, org_id
        )

        return {
            "status": "success",
            "indexed": result["indexed"],
            "message": f"Indexed {result['indexed']} Jira issues for AI context"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to index Jira issues: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to index Jira issues"
        )


@router.post("/search-context", response_model=JiraContextSearchResponse)
async def search_jira_context(
    request: JiraContextSearchRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Search for relevant Jira issues to provide AI context.

    Uses semantic similarity to find issues related to the query text.
    Used when generating/refining canvas nodes to provide relevant context.

    Args:
        query: Text to search for (e.g., "authentication slow")
        canvas_id: Optional - filter to specific canvas
        top_k: Number of results to return (default 5)

    Returns:
        List of relevant issues with similarity scores and formatted context
    """
    from app.services.jira_context_service import JiraContextService

    org_id = await get_user_org_id(session, current_user.id)

    issues = await JiraContextService.search_relevant_jira_issues(
        session,
        query_text=request.query,
        canvas_id=request.canvas_id,
        user_id=current_user.id,
        organization_id=org_id,
        top_k=request.top_k
    )

    formatted_context = JiraContextService.format_jira_context_for_ai(issues)

    return JiraContextSearchResponse(
        issues=issues,
        formatted_context=formatted_context
    )


@router.post("/auto-link/{node_id}")
async def auto_link_jira_issues_to_node(
    node_id: int,
    canvas_id: int = Query(..., description="Canvas the node belongs to"),
    node_content: str = Query(..., description="Node content for similarity matching"),
    threshold: float = Query(0.75, ge=0.0, le=1.0, description="Minimum similarity score"),
    max_links: int = Query(3, ge=1, le=10, description="Maximum issues to link"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Automatically link relevant Jira issues to a node based on semantic similarity.

    Args:
        node_id: Node to link issues to
        canvas_id: Canvas the node belongs to
        node_content: Content of the node (for similarity search)
        threshold: Minimum similarity score (0-1, default 0.75)
        max_links: Maximum number of issues to link (default 3)

    Returns:
        List of linked task IDs
    """
    from app.services.jira_context_service import JiraContextService

    org_id = await get_user_org_id(session, current_user.id)

    linked_ids = await JiraContextService.auto_link_relevant_issues(
        session,
        node_id=node_id,
        node_content=node_content,
        canvas_id=canvas_id,
        user_id=current_user.id,
        organization_id=org_id,
        threshold=threshold,
        max_links=max_links
    )

    return {
        "status": "success",
        "linked_count": len(linked_ids),
        "linked_task_ids": linked_ids,
        "message": f"Auto-linked {len(linked_ids)} Jira issues to node {node_id}"
    }

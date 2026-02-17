"""
Project API Endpoints - Product Development Platform

Manages product development projects with workflow stages.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
from pydantic import BaseModel

from app.core.database import get_session
from app.models.project import Project, StageTransition, WorkflowStage
from app.models.task import InputEvent
from app.models.user import User
from app.models.organization import OrganizationMember
from app.schemas.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectWithArtifactsResponse,
    ProjectWithDetailsResponse,
    StageTransitionCreate,
    StageTransitionResponse,
)
from app.api.deps import get_current_user

router = APIRouter()


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(
    organization_id: Optional[int] = Query(None, description="Filter by organization ID"),
    canvas_id: Optional[int] = Query(None, description="Filter by canvas ID"),
    current_stage: Optional[WorkflowStage] = Query(None, description="Filter by current stage"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    List projects accessible to the current user.

    Filters:
    - organization_id: Show projects for specific organization
    - canvas_id: Show projects on specific canvas
    - current_stage: Show projects in specific workflow stage
    """
    # Build query
    query = select(Project)

    # Apply filters
    if organization_id is not None:
        # Check user is a member
        member_check = await session.execute(
            select(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == current_user.id
            )
        )
        if not member_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this organization"
            )
        query = query.where(Project.organization_id == organization_id)
    else:
        # Get all projects from orgs user belongs to
        member_orgs = await session.execute(
            select(OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == current_user.id)
        )
        org_ids = [row[0] for row in member_orgs.fetchall()]
        if not org_ids:
            return []
        query = query.where(Project.organization_id.in_(org_ids))

    if canvas_id is not None:
        query = query.where(Project.canvas_id == canvas_id)

    if current_stage is not None:
        query = query.where(Project.current_stage == current_stage.value)

    query = query.order_by(Project.updated_at.desc())
    result = await session.execute(query)
    return result.scalars().all()


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new product development project.

    Requires:
    - organization_id: User must be a member
    - name: Project name
    - Optional: canvas_id, current_stage, etc.
    """
    # Verify user is member of organization
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == project_data.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization"
        )

    # Create project
    project = Project(
        **project_data.model_dump(),
        created_by_id=current_user.id
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Get a single project by ID."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    # Check user has access (is member of organization)
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == project.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    return project


@router.get("/{project_id}/details", response_model=ProjectWithDetailsResponse)
async def get_project_details(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get project with full details including:
    - Artifacts
    - Pending change proposals
    - Recent stage transitions
    """
    query = (
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.artifacts),
            selectinload(Project.change_proposals),
            selectinload(Project.stage_transitions)
        )
    )
    result = await session.execute(query)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == project.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Filter pending proposals
    pending_proposals = [p for p in project.change_proposals if p.status == "pending"]

    # Get recent transitions (last 5)
    recent_transitions = sorted(
        project.stage_transitions,
        key=lambda t: t.created_at,
        reverse=True
    )[:5]

    return {
        **project.__dict__,
        "pending_proposals": pending_proposals,
        "recent_transitions": recent_transitions,
    }


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    project_data: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Update a project."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == project.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Update fields
    update_data = project_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Delete a project (and all related artifacts, proposals)."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == project.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    await session.delete(project)
    await session.commit()


# Stage Transition endpoints
@router.post("/{project_id}/transitions", response_model=StageTransitionResponse, status_code=status.HTTP_201_CREATED)
async def create_stage_transition(
    project_id: int,
    transition_data: StageTransitionCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Record a stage transition for a project.

    When moving from one workflow stage to another, create a transition record.
    """
    # Verify project exists and user has access
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == project.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Create transition
    transition = StageTransition(
        **transition_data.model_dump(),
        approved_by_id=current_user.id
    )
    session.add(transition)

    # Update project's current stage
    project.current_stage = transition_data.to_stage.value

    await session.commit()
    await session.refresh(transition)
    return transition


@router.get("/{project_id}/transitions", response_model=List[StageTransitionResponse])
async def list_stage_transitions(
    project_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Get all stage transitions for a project."""
    # Verify access
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == project.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Get transitions
    query = (
        select(StageTransition)
        .where(StageTransition.project_id == project_id)
        .order_by(StageTransition.created_at.desc())
    )
    result = await session.execute(query)
    return result.scalars().all()


# ======== Workflow Orchestration endpoints ========

class AnalyzeInputRequest(BaseModel):
    """Request to trigger impact analysis for a project."""
    trigger_content: str  # The content to analyze (Jira issue description, etc.)
    trigger_type: str = "manual"  # jira_issue, zoom_meeting, slack_message, manual
    trigger_id: Optional[str] = None  # External ID (PROJ-456, etc.)
    trigger_url: Optional[str] = None  # Link to source
    input_event_id: Optional[int] = None  # Existing InputEvent to link to


class AnalyzeInputResponse(BaseModel):
    """Response from impact analysis."""
    proposals_created: int
    impact_summary: dict
    proposal_ids: List[int]


@router.post("/{project_id}/analyze", response_model=AnalyzeInputResponse, status_code=status.HTTP_201_CREATED)
async def analyze_input_for_project(
    project_id: int,
    request: AnalyzeInputRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Analyze new information and generate change proposals for a project.

    This is the primary entry point for the AI workflow orchestration:

    1. Receives new information (Jira issue, meeting summary, manual input, etc.)
    2. AI analyzes which artifacts are affected
    3. Creates ChangeProposals for each affected artifact
    4. Assigns proposals to stakeholders for review

    Example:
    ```
    POST /api/v1/projects/1/analyze
    {
        "trigger_content": "PROJ-456: Add OAuth login to mobile app. Users should be able to login with Google and GitHub. This affects the authentication flow and requires a new third-party service.",
        "trigger_type": "jira_issue",
        "trigger_id": "PROJ-456",
        "trigger_url": "https://company.atlassian.net/browse/PROJ-456"
    }
    ```
    """
    from app.services.workflow_orchestrator import WorkflowOrchestrator

    # Verify access
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == project.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Get or create InputEvent
    if request.input_event_id:
        input_event = await session.get(InputEvent, request.input_event_id)
        if not input_event:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Input event not found"
            )
    else:
        # Create a new InputEvent for tracking
        input_event = InputEvent(
            source_type=request.trigger_type,
            event_type=f"{request.trigger_type}.analyzed",
            external_id=request.trigger_id,
            payload={
                "trigger_content": request.trigger_content,
                "trigger_url": request.trigger_url,
                "trigger_id": request.trigger_id,
            },
            status="processing",
            organization_id=project.organization_id
        )
        session.add(input_event)
        await session.flush()

    # Run WorkflowOrchestrator
    result = await WorkflowOrchestrator.process_input_event(
        session=session,
        input_event=input_event,
        project_id=project_id,
        user_id=current_user.id,
        organization_id=project.organization_id,
        trigger_content=request.trigger_content
    )

    # Mark InputEvent as completed
    input_event.status = "completed"

    await session.commit()

    return AnalyzeInputResponse(
        proposals_created=result["proposals_created"],
        impact_summary=result["impact_summary"],
        proposal_ids=[p.id for p in result["proposals"]]
    )

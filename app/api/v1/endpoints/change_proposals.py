"""
ChangeProposal API Endpoints - Product Development Platform

Manages change proposals for artifact updates with approval workflow.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime

from app.core.database import get_session
from app.models.change_proposal import (
    ChangeProposal,
    ImpactAnalysis,
    ChangeProposalStatus,
    ChangeSeverity,
)
from app.models.artifact import Artifact, ArtifactVersion
from app.models.project import Project
from app.models.user import User
from app.models.organization import OrganizationMember
from app.schemas.change_proposal import (
    ChangeProposalCreate,
    ChangeProposalUpdate,
    ChangeProposalResponse,
    ChangeProposalWithDetailsResponse,
    ChangeProposalApprove,
    ChangeProposalReject,
    ImpactAnalysisResponse,
)
from app.api.deps import get_current_user

router = APIRouter()


@router.get("/", response_model=List[ChangeProposalResponse])
async def list_change_proposals(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    artifact_id: Optional[int] = Query(None, description="Filter by artifact ID"),
    status: Optional[ChangeProposalStatus] = Query(None, description="Filter by status"),
    severity: Optional[ChangeSeverity] = Query(None, description="Filter by severity"),
    assigned_to_me: bool = Query(False, description="Show only proposals assigned to current user"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    List change proposals accessible to the current user.

    Filters:
    - project_id: Show proposals for specific project
    - artifact_id: Show proposals for specific artifact
    - status: Filter by status (pending, approved, rejected, etc.)
    - severity: Filter by severity (low, medium, high, critical)
    - assigned_to_me: Show only proposals assigned to you
    """
    # Build query
    query = select(ChangeProposal)

    # Apply filters
    if project_id is not None:
        # Verify access to project
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

        query = query.where(ChangeProposal.project_id == project_id)
    else:
        # Get all proposals from orgs user belongs to
        member_orgs = await session.execute(
            select(OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == current_user.id)
        )
        org_ids = [row[0] for row in member_orgs.fetchall()]
        if not org_ids:
            return []
        query = query.where(ChangeProposal.organization_id.in_(org_ids))

    if artifact_id is not None:
        query = query.where(ChangeProposal.artifact_id == artifact_id)

    if status is not None:
        query = query.where(ChangeProposal.status == status.value)

    if severity is not None:
        query = query.where(ChangeProposal.severity == severity.value)

    if assigned_to_me:
        query = query.where(ChangeProposal.assigned_to_id == current_user.id)

    query = query.order_by(ChangeProposal.created_at.desc())
    result = await session.execute(query)
    return result.scalars().all()


@router.post("/", response_model=ChangeProposalResponse, status_code=status.HTTP_201_CREATED)
async def create_change_proposal(
    proposal_data: ChangeProposalCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new change proposal.

    Usually called by the WorkflowOrchestrator service, but can be manual.
    """
    # Verify access to project
    project = await session.get(Project, proposal_data.project_id)
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

    # Verify artifact exists
    artifact = await session.get(Artifact, proposal_data.artifact_id)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found"
        )

    # Create proposal
    proposal = ChangeProposal(
        **proposal_data.model_dump(),
        organization_id=project.organization_id,
        status=ChangeProposalStatus.PENDING.value
    )
    session.add(proposal)
    await session.commit()
    await session.refresh(proposal)
    return proposal


@router.get("/{proposal_id}", response_model=ChangeProposalWithDetailsResponse)
async def get_change_proposal(
    proposal_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Get a single change proposal with full details."""
    query = (
        select(ChangeProposal)
        .where(ChangeProposal.id == proposal_id)
        .options(
            selectinload(ChangeProposal.artifact),
            selectinload(ChangeProposal.impact_analysis_detail)
        )
    )
    result = await session.execute(query)
    proposal = result.scalar_one_or_none()

    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change proposal not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == proposal.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    return {
        **proposal.__dict__,
        "artifact": proposal.artifact,
        "impact": proposal.impact_analysis_detail
    }


@router.put("/{proposal_id}", response_model=ChangeProposalResponse)
async def update_change_proposal(
    proposal_id: int,
    proposal_data: ChangeProposalUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Update a change proposal.

    Mainly used to assign reviewers or update status manually.
    """
    proposal = await session.get(ChangeProposal, proposal_id)
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change proposal not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == proposal.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Update fields
    update_data = proposal_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(proposal, field, value)

    await session.commit()
    await session.refresh(proposal)
    return proposal


@router.post("/{proposal_id}/approve", response_model=ChangeProposalResponse)
async def approve_change_proposal(
    proposal_id: int,
    approval_data: ChangeProposalApprove,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Approve a change proposal.

    This will:
    1. Update proposal status to 'approved'
    2. Apply changes to the artifact
    3. Create new ArtifactVersion
    4. Record approval metadata
    """
    proposal = await session.get(ChangeProposal, proposal_id)
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change proposal not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == proposal.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Check if already processed
    if proposal.status != ChangeProposalStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proposal already {proposal.status}"
        )

    # Get artifact
    artifact = await session.get(Artifact, proposal.artifact_id)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found"
        )

    # Apply changes to artifact
    # TODO: Implement smart merging of proposed_changes JSON into artifact.content
    # For now, we'll just track that it was approved

    # Increment version
    artifact.version_counter += 1
    new_version_number = f"{artifact.version_counter // 10}.{artifact.version_counter % 10}"
    artifact.version = new_version_number

    # Create new version
    version = ArtifactVersion(
        artifact_id=artifact.id,
        version=new_version_number,
        version_number=artifact.version_counter,
        content=artifact.content,
        content_format=artifact.content_format,
        status=artifact.status,
        change_summary=f"Applied change proposal: {proposal.title}",
        change_proposal_id=proposal.id,
        created_by_id=current_user.id,
        metadata_snapshot=artifact.settings
    )
    session.add(version)
    await session.flush()

    # Update proposal
    proposal.status = ChangeProposalStatus.APPROVED.value
    proposal.reviewed_by_id = current_user.id
    proposal.reviewed_at = datetime.utcnow()
    proposal.applied_at = datetime.utcnow()
    proposal.review_notes = approval_data.review_notes
    proposal.created_version_id = version.id

    await session.commit()
    await session.refresh(proposal)
    return proposal


@router.post("/{proposal_id}/reject", response_model=ChangeProposalResponse)
async def reject_change_proposal(
    proposal_id: int,
    rejection_data: ChangeProposalReject,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Reject a change proposal.

    Requires review notes explaining why.
    """
    proposal = await session.get(ChangeProposal, proposal_id)
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change proposal not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == proposal.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Check if already processed
    if proposal.status != ChangeProposalStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proposal already {proposal.status}"
        )

    # Update proposal
    proposal.status = ChangeProposalStatus.REJECTED.value
    proposal.reviewed_by_id = current_user.id
    proposal.reviewed_at = datetime.utcnow()
    proposal.review_notes = rejection_data.review_notes

    await session.commit()
    await session.refresh(proposal)
    return proposal


@router.delete("/{proposal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_change_proposal(
    proposal_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a change proposal.

    Only allowed for rejected or superseded proposals.
    """
    proposal = await session.get(ChangeProposal, proposal_id)
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change proposal not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == proposal.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Only allow deletion of rejected/superseded proposals
    if proposal.status not in [
        ChangeProposalStatus.REJECTED.value,
        ChangeProposalStatus.SUPERSEDED.value
    ]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only delete rejected or superseded proposals"
        )

    await session.delete(proposal)
    await session.commit()


@router.get("/{proposal_id}/impact", response_model=ImpactAnalysisResponse)
async def get_impact_analysis(
    proposal_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Get the impact analysis for a change proposal."""
    # Get proposal first to check access
    proposal = await session.get(ChangeProposal, proposal_id)
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change proposal not found"
        )

    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == proposal.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Get impact analysis
    query = select(ImpactAnalysis).where(ImpactAnalysis.change_proposal_id == proposal_id)
    result = await session.execute(query)
    impact = result.scalar_one_or_none()

    if not impact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Impact analysis not found for this proposal"
        )

    return impact

"""
Artifact API Endpoints - Product Development Platform

Manages product development artifacts (PRDs, Tech Specs, Designs, etc.) with version control.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime

from app.core.database import get_session
from app.models.artifact import Artifact, ArtifactVersion, ArtifactType, ArtifactStatus
from app.models.project import Project
from app.models.user import User
from app.models.organization import OrganizationMember
from app.schemas.artifact import (
    ArtifactCreate,
    ArtifactUpdate,
    ArtifactResponse,
    ArtifactWithVersionsResponse,
    ArtifactVersionResponse,
)
from app.api.deps import get_current_user

router = APIRouter()


@router.get("/", response_model=List[ArtifactResponse])
async def list_artifacts(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    canvas_id: Optional[int] = Query(None, description="Filter by canvas ID"),
    artifact_type: Optional[ArtifactType] = Query(None, description="Filter by type"),
    status: Optional[ArtifactStatus] = Query(None, description="Filter by status"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    List artifacts accessible to the current user.

    Filters:
    - project_id: Show artifacts for specific project
    - canvas_id: Show artifacts on specific canvas
    - artifact_type: Filter by type (prd, tech_spec, ux_design, etc.)
    - status: Filter by status (draft, review, approved, archived)
    """
    # Build query
    query = select(Artifact)

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

        query = query.where(Artifact.project_id == project_id)
    else:
        # Get all artifacts from orgs user belongs to
        member_orgs = await session.execute(
            select(OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == current_user.id)
        )
        org_ids = [row[0] for row in member_orgs.fetchall()]
        if not org_ids:
            return []
        query = query.where(Artifact.organization_id.in_(org_ids))

    if canvas_id is not None:
        query = query.where(Artifact.canvas_id == canvas_id)

    if artifact_type is not None:
        query = query.where(Artifact.artifact_type == artifact_type.value)

    if status is not None:
        query = query.where(Artifact.status == status.value)

    query = query.order_by(Artifact.updated_at.desc())
    result = await session.execute(query)
    return result.scalars().all()


@router.post("/", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    artifact_data: ArtifactCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new artifact (PRD, Tech Spec, etc.).

    Automatically creates version 1.0 in the version history.
    """
    # Verify access to project
    project = await session.get(Project, artifact_data.project_id)
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

    # Create artifact
    artifact = Artifact(
        **artifact_data.model_dump(exclude={'project_id'}),
        project_id=artifact_data.project_id,
        organization_id=project.organization_id,
        created_by_id=current_user.id,
        current_owner_id=current_user.id,
        version="1.0",
        version_counter=1
    )
    session.add(artifact)
    await session.flush()  # Get artifact.id

    # Create initial version
    version = ArtifactVersion(
        artifact_id=artifact.id,
        version="1.0",
        version_number=1,
        content=artifact.content,
        content_format=artifact.content_format,
        status=artifact.status,
        change_summary="Initial version",
        created_by_id=current_user.id,
        metadata_snapshot=artifact.settings
    )
    session.add(version)

    await session.commit()
    await session.refresh(artifact)
    return artifact


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Get a single artifact by ID."""
    artifact = await session.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == artifact.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    return artifact


@router.get("/{artifact_id}/versions", response_model=ArtifactWithVersionsResponse)
async def get_artifact_with_versions(
    artifact_id: int,
    limit: int = Query(50, description="Max versions to return", le=100),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Get artifact with full version history.

    Returns most recent versions first.
    """
    query = (
        select(Artifact)
        .where(Artifact.id == artifact_id)
        .options(selectinload(Artifact.versions))
    )
    result = await session.execute(query)
    artifact = result.scalar_one_or_none()

    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == artifact.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Sort versions by version_number desc and limit
    sorted_versions = sorted(
        artifact.versions,
        key=lambda v: v.version_number,
        reverse=True
    )[:limit]

    return {
        **artifact.__dict__,
        "versions": sorted_versions
    }


@router.put("/{artifact_id}", response_model=ArtifactResponse)
async def update_artifact(
    artifact_id: int,
    artifact_data: ArtifactUpdate,
    create_version: bool = Query(
        False,
        description="Create new version on update (recommended for content changes)"
    ),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Update an artifact.

    If create_version=true, creates a new ArtifactVersion and increments version counter.
    Use this when making significant content changes that should be tracked.
    """
    artifact = await session.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == artifact.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Store old content if creating version
    old_content = artifact.content if create_version else None

    # Update fields
    update_data = artifact_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(artifact, field, value)

    # Create new version if requested
    if create_version:
        artifact.version_counter += 1
        new_version_number = f"{artifact.version_counter // 10}.{artifact.version_counter % 10}"
        artifact.version = new_version_number

        version = ArtifactVersion(
            artifact_id=artifact.id,
            version=new_version_number,
            version_number=artifact.version_counter,
            content=artifact.content,
            content_format=artifact.content_format,
            status=artifact.status,
            change_summary=f"Manual update by {current_user.name or current_user.email}",
            created_by_id=current_user.id,
            metadata_snapshot=artifact.settings
        )
        session.add(version)

    await session.commit()
    await session.refresh(artifact)
    return artifact


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Delete an artifact (and all versions, change proposals).

    This is a cascading delete - use with caution!
    """
    artifact = await session.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == artifact.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    await session.delete(artifact)
    await session.commit()


@router.get("/{artifact_id}/versions/{version_number}", response_model=ArtifactVersionResponse)
async def get_artifact_version(
    artifact_id: int,
    version_number: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Get a specific version of an artifact."""
    # Verify artifact access first
    artifact = await session.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found"
        )

    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == artifact.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Get version
    query = select(ArtifactVersion).where(
        ArtifactVersion.artifact_id == artifact_id,
        ArtifactVersion.version_number == version_number
    )
    result = await session.execute(query)
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {version_number} not found"
        )

    return version


@router.post("/{artifact_id}/approve", response_model=ArtifactResponse)
async def approve_artifact(
    artifact_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Approve an artifact.

    Changes status to 'approved' and records approval timestamp.
    """
    artifact = await session.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found"
        )

    # Check access
    member_check = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == artifact.organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    artifact.status = ArtifactStatus.APPROVED.value
    artifact.approved_at = datetime.utcnow()

    await session.commit()
    await session.refresh(artifact)
    return artifact

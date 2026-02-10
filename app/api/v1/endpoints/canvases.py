"""
Canvas API Endpoints

Supports both personal and organization canvases with proper access control.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from typing import List, Optional

from app.core.database import get_session
from app.core.permissions import (
    can_access_canvas,
    can_edit_canvas,
    can_delete_canvas,
    check_org_permission,
    Permission,
)
from app.models.canvas import Canvas
from app.models.node import Node, NodeConnection
from app.models.user import User
from app.models.organization import OrganizationMember
from app.schemas.canvas import CanvasCreate, CanvasUpdate, CanvasResponse, CanvasWithNodesResponse
from app.api.deps import get_current_user

router = APIRouter()


@router.get("/", response_model=List[CanvasResponse])
async def list_canvases(
    organization_id: Optional[int] = Query(None, description="Filter by organization ID"),
    include_personal: bool = Query(True, description="Include personal canvases"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    List canvases accessible to the current user.

    - If organization_id is provided, returns canvases for that organization
    - If include_personal is True (default), includes personal canvases
    - Returns all personal + org canvases if no filters specified
    """
    conditions = []

    # Personal canvases (organization_id is NULL and owner is current user)
    if include_personal and organization_id is None:
        conditions.append(
            (Canvas.owner_id == current_user.id) & (Canvas.organization_id == None)
        )

    # Organization canvases
    if organization_id is not None:
        # Check user is a member of this organization
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
        conditions.append(Canvas.organization_id == organization_id)
    elif include_personal:
        # Also get canvases from all orgs user is member of
        member_orgs = await session.execute(
            select(OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == current_user.id)
        )
        org_ids = [row[0] for row in member_orgs.fetchall()]
        if org_ids:
            conditions.append(Canvas.organization_id.in_(org_ids))

    if not conditions:
        return []

    query = select(Canvas).where(or_(*conditions)).order_by(Canvas.updated_at.desc())
    result = await session.execute(query)
    return result.scalars().all()


@router.post("/", response_model=CanvasResponse, status_code=status.HTTP_201_CREATED)
async def create_canvas(
    canvas_data: CanvasCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new canvas.

    - If organization_id is provided, creates an organization canvas
    - Otherwise creates a personal canvas
    """
    # If creating org canvas, verify membership and permission
    if canvas_data.organization_id is not None:
        has_perm = await check_org_permission(
            session,
            current_user.id,
            canvas_data.organization_id,
            Permission.CANVAS_CREATE
        )
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to create canvases in this organization"
            )

        # Org canvas - set organization_id, owner_id is NULL
        canvas = Canvas(
            **canvas_data.model_dump(),
            owner_id=None  # Org canvases don't have an owner
        )
    else:
        # Personal canvas
        data = canvas_data.model_dump()
        data.pop('organization_id', None)  # Remove if present
        canvas = Canvas(
            **data,
            owner_id=current_user.id,
            organization_id=None
        )

    session.add(canvas)
    await session.commit()
    await session.refresh(canvas)
    return canvas


@router.get("/{canvas_id}", response_model=CanvasWithNodesResponse)
async def get_canvas(
    canvas_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Get canvas with all nodes and connections."""
    result = await session.execute(
        select(Canvas)
        .options(selectinload(Canvas.nodes))
        .where(Canvas.id == canvas_id)
    )
    canvas = result.scalar_one_or_none()

    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas not found")

    # Check access permission
    has_access = await can_access_canvas(
        session,
        current_user.id,
        canvas.owner_id,
        canvas.organization_id
    )

    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this canvas"
        )

    # Get node IDs for this canvas
    node_ids = [node.id for node in canvas.nodes]

    # Query connections where source or target is in this canvas
    connections = []
    if node_ids:
        conn_result = await session.execute(
            select(NodeConnection).where(
                NodeConnection.source_node_id.in_(node_ids)
            )
        )
        connections = conn_result.scalars().all()

    # Return canvas with connections - use Pydantic model for proper serialization
    return CanvasWithNodesResponse(
        id=canvas.id,
        name=canvas.name,
        description=canvas.description,
        viewport_x=canvas.viewport_x,
        viewport_y=canvas.viewport_y,
        zoom_level=canvas.zoom_level,
        grid_enabled=canvas.grid_enabled,
        snap_to_grid=canvas.snap_to_grid,
        grid_size=canvas.grid_size,
        settings=canvas.settings,
        owner_id=canvas.owner_id,
        organization_id=canvas.organization_id,
        created_at=canvas.created_at,
        updated_at=canvas.updated_at,
        nodes=canvas.nodes,
        connections=connections
    )


@router.put("/{canvas_id}", response_model=CanvasResponse)
async def update_canvas(
    canvas_id: int,
    canvas_data: CanvasUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Update canvas settings."""
    result = await session.execute(
        select(Canvas).where(Canvas.id == canvas_id)
    )
    canvas = result.scalar_one_or_none()

    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas not found")

    # Check edit permission
    has_edit = await can_edit_canvas(
        session,
        current_user.id,
        canvas.owner_id,
        canvas.organization_id
    )

    if not has_edit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this canvas"
        )

    update_data = canvas_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(canvas, field, value)

    await session.commit()
    await session.refresh(canvas)
    return canvas


@router.delete("/{canvas_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_canvas(
    canvas_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Delete a canvas."""
    result = await session.execute(
        select(Canvas).where(Canvas.id == canvas_id)
    )
    canvas = result.scalar_one_or_none()

    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas not found")

    # Check delete permission
    has_delete = await can_delete_canvas(
        session,
        current_user.id,
        canvas.owner_id,
        canvas.organization_id
    )

    if not has_delete:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this canvas"
        )

    await session.delete(canvas)
    await session.commit()

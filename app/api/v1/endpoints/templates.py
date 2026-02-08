"""API endpoints for node template contexts."""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.core.database import get_session
from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User
from app.models.template import NodeTemplateContext, TemplateScope
from app.schemas.template import (
    TemplateCreate, TemplateUpdate, TemplateResponse,
    TemplateListResponse, TemplateResolved,
)
from app.services import template_service

router = APIRouter()


@router.get("/", response_model=List[TemplateResponse])
async def list_templates(
    node_type: Optional[str] = Query(None, description="Filter by node type"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    List all templates available to the current user.
    Includes system defaults, organization templates (if in an org), and user templates.
    """
    # Get user's organization if any
    org_id = await template_service.get_user_organization_id(session, current_user.id)

    templates = await template_service.list_templates_for_user(
        session,
        user_id=current_user.id,
        organization_id=org_id,
        node_type=node_type,
    )

    return templates


@router.get("/resolve", response_model=TemplateResolved)
async def resolve_template(
    node_type: str = Query(..., description="Node type to resolve template for"),
    subtype: Optional[str] = Query(None, description="Node subtype (e.g., 'prd', 'tech_spec')"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Resolve the best matching template for a node type.
    Uses cascade: User → Organization → System
    """
    org_id = await template_service.get_user_organization_id(session, current_user.id)

    template = await template_service.resolve_template(
        session,
        node_type=node_type,
        subtype=subtype,
        user_id=current_user.id,
        organization_id=org_id,
    )

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No template found for node_type={node_type}, subtype={subtype}"
        )

    # Determine if this is overriding a lower-level template
    is_overridden = template.scope != TemplateScope.SYSTEM.value

    return TemplateResolved(
        template=TemplateResponse.model_validate(template),
        source_scope=template.scope,
        is_overridden=is_overridden,
    )


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get a specific template by ID."""
    org_id = await template_service.get_user_organization_id(session, current_user.id)

    template = await template_service.get_template_by_id(
        session,
        template_id=template_id,
        user_id=current_user.id,
        organization_id=org_id,
    )

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found or access denied"
        )

    return template


@router.post("/", response_model=TemplateResponse)
async def create_user_template(
    template_data: TemplateCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Create a user-level template override.
    This template will take priority over org and system templates.
    """
    template = await template_service.create_user_template(
        session,
        user_id=current_user.id,
        template_data=template_data.model_dump(),
    )
    return template


@router.post("/organization/{organization_id}", response_model=TemplateResponse)
async def create_org_template(
    organization_id: int,
    template_data: TemplateCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Create an organization-level template.
    Requires the user to be a member of the organization.
    """
    # Verify user is in the org
    user_org_id = await template_service.get_user_organization_id(session, current_user.id)
    if user_org_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a member of the organization to create org templates"
        )

    template = await template_service.create_org_template(
        session,
        organization_id=organization_id,
        template_data=template_data.model_dump(),
    )
    return template


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: int,
    update_data: TemplateUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Update a template.
    Users can only update their own templates or org templates (if member).
    System templates cannot be modified.
    """
    org_id = await template_service.get_user_organization_id(session, current_user.id)

    template = await template_service.get_template_by_id(
        session,
        template_id=template_id,
        user_id=current_user.id,
        organization_id=org_id,
    )

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found or access denied"
        )

    if template.scope == TemplateScope.SYSTEM.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System templates cannot be modified. Create a user or org override instead."
        )

    # Check ownership
    if template.scope == TemplateScope.USER.value and template.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own templates"
        )

    updated = await template_service.update_template(
        session,
        template=template,
        update_data=update_data.model_dump(exclude_unset=True),
    )
    return updated


@router.delete("/{template_id}")
async def delete_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Delete a template.
    Users can only delete their own templates or org templates (if member).
    System templates cannot be deleted.
    """
    org_id = await template_service.get_user_organization_id(session, current_user.id)

    template = await template_service.get_template_by_id(
        session,
        template_id=template_id,
        user_id=current_user.id,
        organization_id=org_id,
    )

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found or access denied"
        )

    if template.scope == TemplateScope.SYSTEM.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System templates cannot be deleted"
        )

    if template.scope == TemplateScope.USER.value and template.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own templates"
        )

    await template_service.delete_template(session, template)
    return {"status": "deleted", "template_id": template_id}


@router.post("/seed-defaults")
async def seed_default_templates(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Seed system default templates.
    This is idempotent - existing templates won't be duplicated.
    Typically run once on initial setup.
    """
    # In production, you might want to restrict this to admins
    count = await template_service.seed_system_templates(session)
    return {"status": "success", "templates_created": count}

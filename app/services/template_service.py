"""Template resolution and management service."""
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.models.template import NodeTemplateContext, TemplateScope, SYSTEM_DEFAULT_TEMPLATES
from app.models.user import User
from app.models.organization import OrganizationMember


async def get_user_organization_id(session: AsyncSession, user_id: int) -> Optional[int]:
    """Get the primary organization ID for a user (if any)."""
    result = await session.execute(
        select(OrganizationMember.organization_id)
        .where(OrganizationMember.user_id == user_id)
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row


async def resolve_template(
    session: AsyncSession,
    node_type: str,
    subtype: Optional[str] = None,
    user_id: Optional[int] = None,
    organization_id: Optional[int] = None,
) -> Optional[NodeTemplateContext]:
    """
    Resolve the appropriate template using cascade:
    1. User override (if user_id provided)
    2. Organization override (if org_id provided or user is in an org)
    3. System default

    Returns the highest priority matching template.
    """
    # Build conditions for each scope level
    conditions = []

    # 1. User-level template
    if user_id:
        conditions.append(
            and_(
                NodeTemplateContext.scope == TemplateScope.USER.value,
                NodeTemplateContext.user_id == user_id,
                NodeTemplateContext.node_type == node_type,
                NodeTemplateContext.subtype == subtype if subtype else NodeTemplateContext.subtype.is_(None),
                NodeTemplateContext.is_active == True,
            )
        )

    # 2. Organization-level template
    if organization_id:
        conditions.append(
            and_(
                NodeTemplateContext.scope == TemplateScope.ORGANIZATION.value,
                NodeTemplateContext.organization_id == organization_id,
                NodeTemplateContext.node_type == node_type,
                NodeTemplateContext.subtype == subtype if subtype else NodeTemplateContext.subtype.is_(None),
                NodeTemplateContext.is_active == True,
            )
        )

    # 3. System default
    conditions.append(
        and_(
            NodeTemplateContext.scope == TemplateScope.SYSTEM.value,
            NodeTemplateContext.node_type == node_type,
            NodeTemplateContext.subtype == subtype if subtype else NodeTemplateContext.subtype.is_(None),
            NodeTemplateContext.is_active == True,
        )
    )

    # Try each condition in priority order
    for condition in conditions:
        result = await session.execute(
            select(NodeTemplateContext)
            .where(condition)
            .order_by(NodeTemplateContext.priority.desc())
            .limit(1)
        )
        template = result.scalar_one_or_none()
        if template:
            return template

    return None


async def list_templates_for_user(
    session: AsyncSession,
    user_id: int,
    organization_id: Optional[int] = None,
    node_type: Optional[str] = None,
) -> List[NodeTemplateContext]:
    """
    List all templates available to a user.
    Includes system, org (if applicable), and user templates.
    """
    conditions = [
        # System templates
        NodeTemplateContext.scope == TemplateScope.SYSTEM.value,
        # User's own templates
        and_(
            NodeTemplateContext.scope == TemplateScope.USER.value,
            NodeTemplateContext.user_id == user_id,
        ),
    ]

    # Add org templates if user is in an org
    if organization_id:
        conditions.append(
            and_(
                NodeTemplateContext.scope == TemplateScope.ORGANIZATION.value,
                NodeTemplateContext.organization_id == organization_id,
            )
        )

    query = select(NodeTemplateContext).where(
        and_(
            or_(*conditions),
            NodeTemplateContext.is_active == True,
        )
    )

    if node_type:
        query = query.where(NodeTemplateContext.node_type == node_type)

    query = query.order_by(
        NodeTemplateContext.node_type,
        NodeTemplateContext.priority.desc(),
    )

    result = await session.execute(query)
    return list(result.scalars().all())


async def get_template_by_id(
    session: AsyncSession,
    template_id: int,
    user_id: int,
    organization_id: Optional[int] = None,
) -> Optional[NodeTemplateContext]:
    """
    Get a template by ID, ensuring the user has access to it.
    """
    result = await session.execute(
        select(NodeTemplateContext).where(NodeTemplateContext.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        return None

    # Check access
    if template.scope == TemplateScope.SYSTEM.value:
        return template  # Everyone can access system templates
    elif template.scope == TemplateScope.ORGANIZATION.value:
        if organization_id and template.organization_id == organization_id:
            return template
    elif template.scope == TemplateScope.USER.value:
        if template.user_id == user_id:
            return template

    return None  # No access


async def seed_system_templates(session: AsyncSession) -> int:
    """
    Seed the default system templates if they don't exist.
    Returns the number of templates created.
    """
    created = 0

    for template_data in SYSTEM_DEFAULT_TEMPLATES:
        # Check if template already exists
        result = await session.execute(
            select(NodeTemplateContext).where(
                and_(
                    NodeTemplateContext.scope == TemplateScope.SYSTEM.value,
                    NodeTemplateContext.node_type == template_data["node_type"],
                    NodeTemplateContext.subtype == template_data.get("subtype"),
                )
            )
        )
        existing = result.scalar_one_or_none()

        if not existing:
            template = NodeTemplateContext(
                scope=TemplateScope.SYSTEM.value,
                **template_data
            )
            session.add(template)
            created += 1

    if created > 0:
        await session.commit()

    return created


async def create_user_template(
    session: AsyncSession,
    user_id: int,
    template_data: dict,
) -> NodeTemplateContext:
    """Create a user-level template."""
    template = NodeTemplateContext(
        scope=TemplateScope.USER.value,
        user_id=user_id,
        **template_data
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


async def create_org_template(
    session: AsyncSession,
    organization_id: int,
    template_data: dict,
) -> NodeTemplateContext:
    """Create an organization-level template."""
    template = NodeTemplateContext(
        scope=TemplateScope.ORGANIZATION.value,
        organization_id=organization_id,
        **template_data
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


async def update_template(
    session: AsyncSession,
    template: NodeTemplateContext,
    update_data: dict,
) -> NodeTemplateContext:
    """Update a template."""
    for key, value in update_data.items():
        if value is not None:
            setattr(template, key, value)

    await session.commit()
    await session.refresh(template)
    return template


async def delete_template(
    session: AsyncSession,
    template: NodeTemplateContext,
) -> bool:
    """Delete a template (only user/org templates, not system)."""
    if template.scope == TemplateScope.SYSTEM.value:
        return False  # Cannot delete system templates

    await session.delete(template)
    await session.commit()
    return True

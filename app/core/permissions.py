"""
Role-Based Access Control (RBAC) for Organizations

Defines permissions for each role and provides utilities
for checking access to resources.
"""

from enum import Enum
from typing import Optional, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.organization import OrganizationMember, OrganizationRole


class Permission(str, Enum):
    """Available permissions in the system."""

    # Canvas permissions
    CANVAS_VIEW = "canvas:view"
    CANVAS_CREATE = "canvas:create"
    CANVAS_EDIT = "canvas:edit"
    CANVAS_DELETE = "canvas:delete"

    # Node permissions (follow canvas permissions)
    NODE_CREATE = "node:create"
    NODE_EDIT = "node:edit"
    NODE_DELETE = "node:delete"

    # Organization management
    ORG_VIEW = "org:view"
    ORG_EDIT = "org:edit"
    ORG_DELETE = "org:delete"
    ORG_INVITE = "org:invite"
    ORG_REMOVE_MEMBER = "org:remove_member"
    ORG_CHANGE_ROLES = "org:change_roles"


# Define permissions for each role
ROLE_PERMISSIONS: dict[OrganizationRole, Set[Permission]] = {
    OrganizationRole.OWNER: {
        # Full access to everything
        Permission.CANVAS_VIEW,
        Permission.CANVAS_CREATE,
        Permission.CANVAS_EDIT,
        Permission.CANVAS_DELETE,
        Permission.NODE_CREATE,
        Permission.NODE_EDIT,
        Permission.NODE_DELETE,
        Permission.ORG_VIEW,
        Permission.ORG_EDIT,
        Permission.ORG_DELETE,
        Permission.ORG_INVITE,
        Permission.ORG_REMOVE_MEMBER,
        Permission.ORG_CHANGE_ROLES,
    },
    OrganizationRole.ADMIN: {
        # Canvas operations
        Permission.CANVAS_VIEW,
        Permission.CANVAS_CREATE,
        Permission.CANVAS_EDIT,
        Permission.CANVAS_DELETE,
        Permission.NODE_CREATE,
        Permission.NODE_EDIT,
        Permission.NODE_DELETE,
        # Org operations (limited)
        Permission.ORG_VIEW,
        Permission.ORG_EDIT,
        Permission.ORG_INVITE,
        Permission.ORG_REMOVE_MEMBER,  # Can remove members (not owners/admins)
    },
    OrganizationRole.MEMBER: {
        # Canvas operations (view + create own + edit own)
        Permission.CANVAS_VIEW,
        Permission.CANVAS_CREATE,
        Permission.CANVAS_EDIT,  # Can edit canvases they have access to
        Permission.NODE_CREATE,
        Permission.NODE_EDIT,
        Permission.NODE_DELETE,
        # Org operations (view only)
        Permission.ORG_VIEW,
    },
}


def get_role_permissions(role: OrganizationRole) -> Set[Permission]:
    """Get all permissions for a role."""
    return ROLE_PERMISSIONS.get(role, set())


def has_permission(role: OrganizationRole, permission: Permission) -> bool:
    """Check if a role has a specific permission."""
    return permission in get_role_permissions(role)


async def get_user_org_role(
    session: AsyncSession,
    user_id: int,
    organization_id: int
) -> Optional[OrganizationRole]:
    """Get user's role in an organization."""
    result = await session.execute(
        select(OrganizationMember.role)
        .where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id
        )
    )
    return result.scalar_one_or_none()


async def check_org_permission(
    session: AsyncSession,
    user_id: int,
    organization_id: int,
    permission: Permission
) -> bool:
    """Check if a user has a specific permission in an organization."""
    role = await get_user_org_role(session, user_id, organization_id)
    if not role:
        return False
    return has_permission(role, permission)


async def can_access_canvas(
    session: AsyncSession,
    user_id: int,
    canvas_owner_id: Optional[int],
    canvas_organization_id: Optional[int]
) -> bool:
    """
    Check if a user can access a canvas.

    Access rules:
    - Personal canvases: Only the owner can access
    - Org canvases: Any member of the org can access
    """
    # Personal canvas - owner only
    if canvas_organization_id is None:
        return canvas_owner_id == user_id

    # Org canvas - check membership
    role = await get_user_org_role(session, user_id, canvas_organization_id)
    return role is not None


async def can_edit_canvas(
    session: AsyncSession,
    user_id: int,
    canvas_owner_id: Optional[int],
    canvas_organization_id: Optional[int]
) -> bool:
    """
    Check if a user can edit a canvas.

    Edit rules:
    - Personal canvases: Only the owner can edit
    - Org canvases: Members with CANVAS_EDIT permission can edit
    """
    # Personal canvas - owner only
    if canvas_organization_id is None:
        return canvas_owner_id == user_id

    # Org canvas - check permission
    return await check_org_permission(
        session, user_id, canvas_organization_id, Permission.CANVAS_EDIT
    )


async def can_delete_canvas(
    session: AsyncSession,
    user_id: int,
    canvas_owner_id: Optional[int],
    canvas_organization_id: Optional[int]
) -> bool:
    """
    Check if a user can delete a canvas.

    Delete rules:
    - Personal canvases: Only the owner can delete
    - Org canvases: Only admins+ can delete
    """
    # Personal canvas - owner only
    if canvas_organization_id is None:
        return canvas_owner_id == user_id

    # Org canvas - check permission
    return await check_org_permission(
        session, user_id, canvas_organization_id, Permission.CANVAS_DELETE
    )

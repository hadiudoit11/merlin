"""
Organization API Endpoints

Provides CRUD operations for organizations, member management,
and invitation handling.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload
import re

from app.core.database import get_session
from app.api.deps import get_current_user
from app.models.user import User
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationInvitation,
    OrganizationRole as ModelRole,
    InvitationStatus as ModelInvitationStatus,
)
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationResponse,
    OrganizationBrief,
    MemberUpdate,
    MemberResponse,
    MyMembership,
    InvitationCreate,
    InvitationResponse,
    AcceptInvitationRequest,
    AcceptInvitationResponse,
    OrganizationRole,
)


router = APIRouter()


# ============ Helper Functions ============

def slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text[:255]


async def get_user_role(
    session: AsyncSession,
    organization_id: int,
    user_id: int
) -> Optional[ModelRole]:
    """Get user's role in an organization."""
    result = await session.execute(
        select(OrganizationMember.role)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id
        )
    )
    role = result.scalar_one_or_none()
    return role


async def require_org_permission(
    session: AsyncSession,
    organization_id: int,
    user_id: int,
    min_role: ModelRole = ModelRole.MEMBER
) -> ModelRole:
    """Check if user has at least the required role."""
    role = await get_user_role(session, organization_id, user_id)

    if not role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization"
        )

    role_hierarchy = {ModelRole.OWNER: 3, ModelRole.ADMIN: 2, ModelRole.MEMBER: 1}

    if role_hierarchy.get(role, 0) < role_hierarchy.get(min_role, 0):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This action requires {min_role.value} role or higher"
        )

    return role


# ============ Organization CRUD ============

@router.get("/", response_model=List[OrganizationBrief])
async def list_organizations(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> List[OrganizationBrief]:
    """List all organizations the current user is a member of."""
    result = await session.execute(
        select(Organization)
        .join(OrganizationMember)
        .where(
            OrganizationMember.user_id == current_user.id,
            Organization.is_active == True
        )
        .order_by(Organization.name)
    )
    organizations = result.scalars().all()
    return organizations


@router.post("/", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    data: OrganizationCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> OrganizationResponse:
    """Create a new organization. Creator becomes the owner."""
    # Generate slug if not provided
    slug = data.slug or slugify(data.name)

    # Check slug uniqueness
    existing = await session.execute(
        select(Organization).where(Organization.slug == slug)
    )
    if existing.scalar_one_or_none():
        # Append user ID to make unique
        slug = f"{slug}-{current_user.id}"

    # Create organization
    org = Organization(
        name=data.name,
        slug=slug,
        description=data.description,
        created_by_id=current_user.id,
    )
    session.add(org)
    await session.flush()

    # Add creator as owner
    membership = OrganizationMember(
        organization_id=org.id,
        user_id=current_user.id,
        role=ModelRole.OWNER,
    )
    session.add(membership)
    await session.commit()
    await session.refresh(org)

    # Add member count
    org.member_count = 1

    return org


@router.get("/{organization_id}", response_model=OrganizationResponse)
async def get_organization(
    organization_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> OrganizationResponse:
    """Get organization details."""
    await require_org_permission(session, organization_id, current_user.id)

    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Get member count
    count_result = await session.execute(
        select(func.count(OrganizationMember.id))
        .where(OrganizationMember.organization_id == organization_id)
    )
    org.member_count = count_result.scalar()

    return org


@router.put("/{organization_id}", response_model=OrganizationResponse)
async def update_organization(
    organization_id: int,
    data: OrganizationUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> OrganizationResponse:
    """Update organization. Requires admin or owner role."""
    await require_org_permission(session, organization_id, current_user.id, ModelRole.ADMIN)

    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Update fields
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(org, field, value)

    await session.commit()
    await session.refresh(org)

    return org


@router.delete("/{organization_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    organization_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Delete organization. Requires owner role."""
    await require_org_permission(session, organization_id, current_user.id, ModelRole.OWNER)

    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await session.delete(org)
    await session.commit()


# ============ Member Management ============

@router.get("/{organization_id}/members", response_model=List[MemberResponse])
async def list_members(
    organization_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> List[MemberResponse]:
    """List all members of an organization."""
    await require_org_permission(session, organization_id, current_user.id)

    result = await session.execute(
        select(OrganizationMember)
        .options(selectinload(OrganizationMember.user))
        .where(OrganizationMember.organization_id == organization_id)
        .order_by(OrganizationMember.joined_at)
    )
    members = result.scalars().all()

    return [
        MemberResponse(
            id=m.id,
            user_id=m.user_id,
            role=m.role,
            joined_at=m.joined_at,
            user_email=m.user.email if m.user else None,
            user_name=m.user.full_name if m.user else None,
            user_picture=m.user.picture if m.user else None,
        )
        for m in members
    ]


@router.put("/{organization_id}/members/{user_id}", response_model=MemberResponse)
async def update_member_role(
    organization_id: int,
    user_id: int,
    data: MemberUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> MemberResponse:
    """Update a member's role. Requires admin or owner role."""
    current_role = await require_org_permission(
        session, organization_id, current_user.id, ModelRole.ADMIN
    )

    # Can't change your own role
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role"
        )

    # Get the member
    result = await session.execute(
        select(OrganizationMember)
        .options(selectinload(OrganizationMember.user))
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id
        )
    )
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Only owners can promote to owner or demote owners
    if member.role == ModelRole.OWNER or data.role == OrganizationRole.OWNER:
        if current_role != ModelRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only owners can modify owner roles"
            )

    # Update role
    member.role = ModelRole(data.role.value)
    await session.commit()
    await session.refresh(member)

    return MemberResponse(
        id=member.id,
        user_id=member.user_id,
        role=member.role,
        joined_at=member.joined_at,
        user_email=member.user.email if member.user else None,
        user_name=member.user.full_name if member.user else None,
        user_picture=member.user.picture if member.user else None,
    )


@router.delete("/{organization_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    organization_id: int,
    user_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Remove a member from organization. Admins+ can remove members."""
    current_role = await require_org_permission(
        session, organization_id, current_user.id, ModelRole.ADMIN
    )

    # Get the member
    result = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id
        )
    )
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Can't remove yourself
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use /leave endpoint to leave the organization"
        )

    # Only owners can remove admins/owners
    if member.role in [ModelRole.OWNER, ModelRole.ADMIN] and current_role != ModelRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners can remove admins or owners"
        )

    await session.delete(member)
    await session.commit()


@router.post("/{organization_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_organization(
    organization_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Leave an organization. Owners cannot leave (must transfer ownership first)."""
    role = await get_user_role(session, organization_id, current_user.id)

    if not role:
        raise HTTPException(status_code=404, detail="You are not a member of this organization")

    if role == ModelRole.OWNER:
        # Check if there are other owners
        result = await session.execute(
            select(func.count(OrganizationMember.id))
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role == ModelRole.OWNER,
                OrganizationMember.user_id != current_user.id
            )
        )
        other_owners = result.scalar()

        if not other_owners:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot leave as the only owner. Transfer ownership first or delete the organization."
            )

    # Remove membership
    result = await session.execute(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == current_user.id
        )
    )
    member = result.scalar_one()
    await session.delete(member)
    await session.commit()


# ============ Invitations ============

@router.get("/{organization_id}/invitations", response_model=List[InvitationResponse])
async def list_invitations(
    organization_id: int,
    status_filter: Optional[str] = Query(None, alias="status"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> List[InvitationResponse]:
    """List pending invitations for an organization."""
    await require_org_permission(session, organization_id, current_user.id, ModelRole.ADMIN)

    query = select(OrganizationInvitation).options(
        selectinload(OrganizationInvitation.invited_by),
        selectinload(OrganizationInvitation.organization)
    ).where(OrganizationInvitation.organization_id == organization_id)

    if status_filter:
        query = query.where(OrganizationInvitation.status == ModelInvitationStatus(status_filter))
    else:
        # Default to pending only
        query = query.where(OrganizationInvitation.status == ModelInvitationStatus.PENDING)

    query = query.order_by(OrganizationInvitation.created_at.desc())

    result = await session.execute(query)
    invitations = result.scalars().all()

    return [
        InvitationResponse(
            id=inv.id,
            email=inv.email,
            role=inv.role,
            status=inv.status,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
            invited_by_name=inv.invited_by.full_name if inv.invited_by else None,
            organization_name=inv.organization.name if inv.organization else None,
        )
        for inv in invitations
    ]


@router.post("/{organization_id}/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    organization_id: int,
    data: InvitationCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> InvitationResponse:
    """Create an invitation to join the organization."""
    await require_org_permission(session, organization_id, current_user.id, ModelRole.ADMIN)

    # Check if user is already a member
    result = await session.execute(
        select(User).where(User.email == data.email)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        existing_member = await get_user_role(session, organization_id, existing_user.id)
        if existing_member:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User is already a member of this organization"
            )

    # Check for existing pending invitation
    result = await session.execute(
        select(OrganizationInvitation)
        .where(
            OrganizationInvitation.organization_id == organization_id,
            OrganizationInvitation.email == data.email,
            OrganizationInvitation.status == ModelInvitationStatus.PENDING
        )
    )
    existing_invitation = result.scalar_one_or_none()

    if existing_invitation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An invitation has already been sent to this email"
        )

    # Get organization for response
    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one()

    # Create invitation
    invitation = OrganizationInvitation(
        organization_id=organization_id,
        invited_by_id=current_user.id,
        email=data.email,
        role=ModelRole(data.role.value),
        token=OrganizationInvitation.generate_token(),
        expires_at=OrganizationInvitation.default_expiry(),
    )
    session.add(invitation)
    await session.commit()
    await session.refresh(invitation)

    # TODO: Send invitation email

    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        invited_by_name=current_user.full_name,
        organization_name=org.name,
    )


@router.delete("/{organization_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    organization_id: int,
    invitation_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Revoke a pending invitation."""
    await require_org_permission(session, organization_id, current_user.id, ModelRole.ADMIN)

    result = await session.execute(
        select(OrganizationInvitation)
        .where(
            OrganizationInvitation.id == invitation_id,
            OrganizationInvitation.organization_id == organization_id
        )
    )
    invitation = result.scalar_one_or_none()

    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    invitation.status = ModelInvitationStatus.REVOKED
    await session.commit()


# ============ Accept Invitation (Public) ============

@router.post("/invitations/accept", response_model=AcceptInvitationResponse)
async def accept_invitation(
    data: AcceptInvitationRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> AcceptInvitationResponse:
    """Accept an organization invitation."""
    result = await session.execute(
        select(OrganizationInvitation)
        .options(selectinload(OrganizationInvitation.organization))
        .where(OrganizationInvitation.token == data.token)
    )
    invitation = result.scalar_one_or_none()

    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if not invitation.is_valid:
        if invitation.status != ModelInvitationStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invitation has been {invitation.status.value}"
            )
        if invitation.is_expired:
            invitation.status = ModelInvitationStatus.EXPIRED
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation has expired"
            )

    # Check email matches
    if invitation.email.lower() != current_user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invitation was sent to a different email address"
        )

    # Check if already a member
    existing = await get_user_role(session, invitation.organization_id, current_user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already a member of this organization"
        )

    # Create membership
    from datetime import datetime
    membership = OrganizationMember(
        organization_id=invitation.organization_id,
        user_id=current_user.id,
        role=invitation.role,
        invited_by_id=invitation.invited_by_id,
    )
    session.add(membership)

    # Update invitation status
    invitation.status = ModelInvitationStatus.ACCEPTED
    invitation.accepted_at = datetime.utcnow()

    await session.commit()

    org = invitation.organization

    return AcceptInvitationResponse(
        success=True,
        organization=OrganizationBrief(
            id=org.id,
            name=org.name,
            slug=org.slug,
            logo_url=org.logo_url,
        ),
        role=invitation.role,
        message=f"Welcome to {org.name}!"
    )


# ============ Integration Settings ============

from pydantic import BaseModel
from typing import Dict, Any
from app.models.integration import Integration, IntegrationProvider


class IntegrationSettingsUpdate(BaseModel):
    """Schema for updating organization integration settings."""
    allowed_integrations: Optional[List[str]] = None  # Empty = all allowed
    require_admin_approval: Optional[bool] = None
    preconfigured: Optional[Dict[str, Dict[str, Any]]] = None


class IntegrationSettingsResponse(BaseModel):
    """Response schema for integration settings."""
    allowed_integrations: List[str]
    require_admin_approval: bool
    preconfigured: Dict[str, Dict[str, Any]]
    available_providers: List[str]
    connected_integrations: List[str]


@router.get("/{organization_id}/integrations/settings", response_model=IntegrationSettingsResponse)
async def get_integration_settings(
    organization_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> IntegrationSettingsResponse:
    """Get organization integration settings. Requires admin role."""
    await require_org_permission(session, organization_id, current_user.id, ModelRole.ADMIN)

    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    settings = org.integration_settings or {}

    # Get connected integrations
    int_result = await session.execute(
        select(Integration.provider).where(
            Integration.organization_id == organization_id
        )
    )
    connected = [str(p) for p in int_result.scalars().all()]

    return IntegrationSettingsResponse(
        allowed_integrations=settings.get("allowed_integrations", []),
        require_admin_approval=settings.get("require_admin_approval", False),
        preconfigured=settings.get("preconfigured", {}),
        available_providers=[p.value for p in IntegrationProvider],
        connected_integrations=connected,
    )


@router.put("/{organization_id}/integrations/settings", response_model=IntegrationSettingsResponse)
async def update_integration_settings(
    organization_id: int,
    data: IntegrationSettingsUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> IntegrationSettingsResponse:
    """Update organization integration settings. Requires owner role."""
    await require_org_permission(session, organization_id, current_user.id, ModelRole.OWNER)

    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Validate allowed integrations
    valid_providers = [p.value for p in IntegrationProvider]
    if data.allowed_integrations is not None:
        invalid = [p for p in data.allowed_integrations if p.lower() not in valid_providers]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid integration providers: {invalid}. Valid: {valid_providers}"
            )

    # Update settings
    current_settings = org.integration_settings or {}

    if data.allowed_integrations is not None:
        current_settings["allowed_integrations"] = [p.lower() for p in data.allowed_integrations]
    if data.require_admin_approval is not None:
        current_settings["require_admin_approval"] = data.require_admin_approval
    if data.preconfigured is not None:
        current_settings["preconfigured"] = data.preconfigured

    org.integration_settings = current_settings
    await session.commit()
    await session.refresh(org)

    # Get connected integrations
    int_result = await session.execute(
        select(Integration.provider).where(
            Integration.organization_id == organization_id
        )
    )
    connected = [str(p) for p in int_result.scalars().all()]

    return IntegrationSettingsResponse(
        allowed_integrations=current_settings.get("allowed_integrations", []),
        require_admin_approval=current_settings.get("require_admin_approval", False),
        preconfigured=current_settings.get("preconfigured", {}),
        available_providers=valid_providers,
        connected_integrations=connected,
    )


@router.get("/{organization_id}/integrations/allowed")
async def check_integration_allowed(
    organization_id: int,
    provider: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """Check if a specific integration is allowed for the organization."""
    await require_org_permission(session, organization_id, current_user.id)

    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Get user's role
    role = await get_user_role(session, organization_id, current_user.id)

    is_allowed = org.is_integration_allowed(provider)
    requires_admin = org.requires_admin_for_integrations()
    config = org.get_integration_config(provider)

    can_connect = is_allowed and (
        not requires_admin or role in [ModelRole.ADMIN, ModelRole.OWNER]
    )

    return {
        "provider": provider,
        "allowed": is_allowed,
        "requires_admin_approval": requires_admin,
        "can_connect": can_connect,
        "preconfigured_settings": config,
    }


# ============ My Organizations ============

@router.get("/me/memberships", response_model=List[MyMembership])
async def get_my_memberships(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
) -> List[MyMembership]:
    """Get all organizations the current user is a member of with their roles."""
    result = await session.execute(
        select(OrganizationMember)
        .options(selectinload(OrganizationMember.organization))
        .where(
            OrganizationMember.user_id == current_user.id,
        )
        .order_by(OrganizationMember.joined_at.desc())
    )
    memberships = result.scalars().all()

    return [
        MyMembership(
            organization=OrganizationBrief(
                id=m.organization.id,
                name=m.organization.name,
                slug=m.organization.slug,
                logo_url=m.organization.logo_url,
            ),
            role=m.role,
            joined_at=m.joined_at,
        )
        for m in memberships
        if m.organization and m.organization.is_active
    ]

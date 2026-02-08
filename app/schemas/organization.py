"""
Pydantic schemas for Organization API.
"""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class OrganizationRole(str, Enum):
    """Roles within an organization."""
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class InvitationStatus(str, Enum):
    """Status of an organization invitation."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


# ============ Organization Schemas ============

class OrganizationBase(BaseModel):
    """Base organization fields."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class OrganizationCreate(OrganizationBase):
    """Create organization request."""
    slug: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r'^[a-z0-9-]+$')


class OrganizationUpdate(BaseModel):
    """Update organization request."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    allow_member_invites: Optional[bool] = None


class OrganizationResponse(OrganizationBase):
    """Organization response with all fields."""
    id: int
    slug: str
    logo_url: Optional[str] = None
    is_active: bool
    allow_member_invites: bool
    created_by_id: int
    created_at: datetime
    updated_at: datetime
    member_count: Optional[int] = None

    class Config:
        from_attributes = True


class OrganizationBrief(BaseModel):
    """Brief organization info for lists."""
    id: int
    name: str
    slug: str
    logo_url: Optional[str] = None

    class Config:
        from_attributes = True


# ============ Member Schemas ============

class MemberBase(BaseModel):
    """Base member fields."""
    role: OrganizationRole = OrganizationRole.MEMBER


class MemberCreate(MemberBase):
    """Add member directly (for internal use)."""
    user_id: int


class MemberUpdate(BaseModel):
    """Update member role."""
    role: OrganizationRole


class MemberResponse(BaseModel):
    """Organization member response."""
    id: int
    user_id: int
    role: OrganizationRole
    joined_at: datetime
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    user_picture: Optional[str] = None

    class Config:
        from_attributes = True


class MyMembership(BaseModel):
    """Current user's membership in an organization."""
    organization: OrganizationBrief
    role: OrganizationRole
    joined_at: datetime

    class Config:
        from_attributes = True


# ============ Invitation Schemas ============

class InvitationCreate(BaseModel):
    """Create invitation request."""
    email: EmailStr
    role: OrganizationRole = OrganizationRole.MEMBER


class InvitationResponse(BaseModel):
    """Invitation response."""
    id: int
    email: str
    role: OrganizationRole
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime
    invited_by_name: Optional[str] = None
    organization_name: Optional[str] = None

    class Config:
        from_attributes = True


class AcceptInvitationRequest(BaseModel):
    """Accept invitation request."""
    token: str


class AcceptInvitationResponse(BaseModel):
    """Accept invitation response."""
    success: bool
    organization: OrganizationBrief
    role: OrganizationRole
    message: str


# ============ Permission Check Response ============

class PermissionCheck(BaseModel):
    """Permission check response."""
    has_permission: bool
    role: Optional[OrganizationRole] = None
    reason: Optional[str] = None

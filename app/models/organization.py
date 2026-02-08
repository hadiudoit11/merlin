"""
Organization models for multi-tenancy support.

Organizations enable:
- Team workspaces with shared canvases
- Role-based access control (owner, admin, member)
- Invite-only membership via email
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Enum, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
import enum
import secrets

from app.core.database import Base


class OrganizationRole(str, enum.Enum):
    """Roles within an organization."""
    OWNER = "owner"       # Full control, can delete org, transfer ownership
    ADMIN = "admin"       # Can manage members, create/edit all canvases
    MEMBER = "member"     # Can view/create/edit own canvases


class InvitationStatus(str, enum.Enum):
    """Status of an organization invitation."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class Organization(Base):
    """
    Organization for multi-tenancy.

    An organization is a workspace where multiple users can collaborate
    on shared canvases and OKRs.
    """
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)

    # Organization info
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    logo_url = Column(String(500), nullable=True)

    # Creator
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Settings
    is_active = Column(Boolean, default=True)
    allow_member_invites = Column(Boolean, default=False)  # Allow admins to invite

    # Integration settings (JSON for flexibility)
    # Structure: {
    #   "allowed_integrations": ["zoom", "jira", "slack"],  # Empty = all allowed
    #   "require_admin_approval": true,  # Members need admin approval to connect
    #   "preconfigured": {
    #     "jira": {"default_project": "PROJ", "auto_sync": true},
    #     "zoom": {"auto_import_recordings": true}
    #   }
    # }
    integration_settings = Column(JSON, default=dict)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    created_by = relationship("User", back_populates="created_organizations", foreign_keys=[created_by_id])
    members = relationship("OrganizationMember", back_populates="organization", cascade="all, delete-orphan")
    invitations = relationship("OrganizationInvitation", back_populates="organization", cascade="all, delete-orphan")
    canvases = relationship("Canvas", back_populates="organization")

    def is_integration_allowed(self, provider: str) -> bool:
        """Check if a specific integration is allowed for this org."""
        if not self.integration_settings:
            return True  # All allowed by default
        allowed = self.integration_settings.get("allowed_integrations", [])
        if not allowed:  # Empty list = all allowed
            return True
        return provider.lower() in [p.lower() for p in allowed]

    def get_integration_config(self, provider: str) -> dict:
        """Get pre-configured settings for an integration."""
        if not self.integration_settings:
            return {}
        preconfigured = self.integration_settings.get("preconfigured", {})
        return preconfigured.get(provider.lower(), {})

    def requires_admin_for_integrations(self) -> bool:
        """Check if admin approval is needed to connect integrations."""
        if not self.integration_settings:
            return False
        return self.integration_settings.get("require_admin_approval", False)


class OrganizationMember(Base):
    """
    Membership linking users to organizations with roles.

    A user can be a member of multiple organizations with different roles.
    """
    __tablename__ = "organization_members"

    id = Column(Integer, primary_key=True, index=True)

    # Foreign keys
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Role
    role = Column(Enum(OrganizationRole), default=OrganizationRole.MEMBER, nullable=False)

    # Metadata
    invited_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Timestamps
    joined_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    organization = relationship("Organization", back_populates="members")
    user = relationship("User", back_populates="organization_memberships", foreign_keys=[user_id])
    invited_by = relationship("User", foreign_keys=[invited_by_id])


class OrganizationInvitation(Base):
    """
    Invitation to join an organization.

    Invitations are sent via email and contain a unique token
    that the invitee uses to accept the invitation.
    """
    __tablename__ = "organization_invitations"

    id = Column(Integer, primary_key=True, index=True)

    # Foreign keys
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    invited_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Invitation details
    email = Column(String(255), nullable=False, index=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    role = Column(Enum(OrganizationRole), default=OrganizationRole.MEMBER, nullable=False)

    # Status
    status = Column(Enum(InvitationStatus), default=InvitationStatus.PENDING, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)

    # Relationships
    organization = relationship("Organization", back_populates="invitations")
    invited_by = relationship("User", foreign_keys=[invited_by_id])

    @staticmethod
    def generate_token() -> str:
        """Generate a secure random invitation token."""
        return secrets.token_urlsafe(48)

    @staticmethod
    def default_expiry() -> datetime:
        """Default expiration is 7 days from now."""
        return datetime.utcnow() + timedelta(days=7)

    @property
    def is_expired(self) -> bool:
        """Check if invitation has expired."""
        return datetime.utcnow() > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if invitation can be accepted."""
        return self.status == InvitationStatus.PENDING and not self.is_expired

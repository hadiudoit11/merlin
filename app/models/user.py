from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    # Auth0 integration
    auth0_id = Column(String(255), unique=True, index=True, nullable=True)

    # User info
    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(255))
    picture = Column(String(500), nullable=True)  # Profile picture URL from Auth0
    email_verified = Column(Boolean, default=False)

    # Legacy password auth (nullable for Auth0 users)
    hashed_password = Column(String(255), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    canvases = relationship("Canvas", back_populates="owner")
    created_organizations = relationship(
        "Organization",
        back_populates="created_by",
        foreign_keys="Organization.created_by_id"
    )
    organization_memberships = relationship(
        "OrganizationMember",
        back_populates="user",
        foreign_keys="OrganizationMember.user_id"
    )

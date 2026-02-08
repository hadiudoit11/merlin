from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, JSON, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime

from app.core.database import Base


class Canvas(Base):
    """
    Canvas represents a workspace containing nodes and connections.

    A canvas can be:
    - Personal: owner_id is set, organization_id is NULL
    - Organizational: organization_id is set, visible to org members

    For organizational canvases, access is determined by organization membership.
    """
    __tablename__ = "canvases"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(String(1000))

    # Canvas viewport state
    viewport_x = Column(Float, default=0.0)
    viewport_y = Column(Float, default=0.0)
    zoom_level = Column(Float, default=1.0)

    # Canvas settings
    grid_enabled = Column(Boolean, default=True)
    snap_to_grid = Column(Boolean, default=True)
    grid_size = Column(Integer, default=20)

    # Metadata
    settings = Column(JSON, default=dict)

    # Ownership - personal canvases have owner_id, org canvases have organization_id
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Organization context - if set, this is an organization canvas
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    owner = relationship("User", back_populates="canvases")
    organization = relationship("Organization", back_populates="canvases")
    nodes = relationship("Node", back_populates="canvas", cascade="all, delete-orphan")

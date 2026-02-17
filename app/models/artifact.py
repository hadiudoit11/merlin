"""
Artifact model for Product Development Platform.

An Artifact represents a product development document that goes through versions:
- PRD (Product Requirements Document)
- Tech Spec (Technical Specification)
- UX Design (Design documents/mockups)
- Timeline (Project timeline/roadmap)
- Test Plan
- Launch Plan
- Retro Notes

Each artifact:
- Belongs to a Project
- Has multiple versions (with full version history)
- Can have pending ChangeProposals
- May be linked to a Node on the canvas for visualization
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Boolean, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.core.database import Base


class ArtifactType(str, enum.Enum):
    """Types of artifacts in product development."""
    PRD = "prd"  # Product Requirements Document
    TECH_SPEC = "tech_spec"  # Technical Specification
    UX_DESIGN = "ux_design"  # UX/Design documents
    TIMELINE = "timeline"  # Project timeline/roadmap
    TEST_PLAN = "test_plan"  # QA test plan
    LAUNCH_PLAN = "launch_plan"  # Go-to-market plan
    RETRO_NOTES = "retro_notes"  # Retrospective notes
    CUSTOM = "custom"  # Custom document type


class ArtifactStatus(str, enum.Enum):
    """Artifact approval status."""
    DRAFT = "draft"  # Work in progress
    REVIEW = "review"  # Under review
    APPROVED = "approved"  # Approved by stakeholders
    ARCHIVED = "archived"  # No longer active


class Artifact(Base):
    """
    Represents a product development document with version history.

    Examples:
    - PRD v2.3 for "Mobile App Redesign" project
    - Tech Spec v1.0 for "API v2" project
    - UX Designs v1.2 for "Mobile App Redesign" project
    """
    __tablename__ = "artifacts"

    id = Column(Integer, primary_key=True, index=True)

    # Project association
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Canvas association (optional - for non-project artifacts on canvas)
    canvas_id = Column(
        Integer,
        ForeignKey("canvases.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Node association (if artifact is visualized as a node on canvas)
    node_id = Column(
        Integer,
        ForeignKey("nodes.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,  # One node per artifact
        index=True
    )

    # Artifact info
    name = Column(String(255), nullable=False)
    artifact_type = Column(
        String(50),
        nullable=False,
        index=True
    )

    # Current content (latest version)
    content = Column(Text, nullable=True)  # Rich text / markdown content
    content_format = Column(String(20), default="markdown")  # markdown, html, json

    # Status
    status = Column(
        String(20),
        default=ArtifactStatus.DRAFT.value,
        nullable=False,
        index=True
    )

    # Version tracking
    version = Column(String(20), default="1.0")  # Current version number
    version_counter = Column(Integer, default=1)  # Auto-incrementing counter

    # Ownership
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    created_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )
    current_owner_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    # Metadata
    settings = Column(JSON, default=dict)  # Artifact-specific settings
    tags = Column(JSON, default=list)  # List of tag strings

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)

    # Relationships
    project = relationship("Project", backref="artifacts")
    canvas = relationship("Canvas", backref="artifacts")
    node = relationship("Node", backref="artifact", uselist=False)
    organization = relationship("Organization", backref="artifacts")
    created_by = relationship("User", foreign_keys=[created_by_id], backref="created_artifacts")
    current_owner = relationship("User", foreign_keys=[current_owner_id], backref="owned_artifacts")

    # Forward declarations for relationships defined in other models
    # versions = relationship("ArtifactVersion", back_populates="artifact")
    # change_proposals = relationship("ChangeProposal", back_populates="artifact")

    def __repr__(self):
        return f"<Artifact {self.id}: {self.name} v{self.version} ({self.artifact_type})>"

    @property
    def is_approved(self) -> bool:
        """Check if artifact is approved."""
        return self.status == ArtifactStatus.APPROVED.value

    @property
    def is_draft(self) -> bool:
        """Check if artifact is still in draft."""
        return self.status == ArtifactStatus.DRAFT.value


class ArtifactVersion(Base):
    """
    Tracks version history for artifacts.

    Every time an artifact is updated (via ChangeProposal approval),
    a new version is created with full snapshot.
    """
    __tablename__ = "artifact_versions"

    id = Column(Integer, primary_key=True, index=True)

    # Artifact reference
    artifact_id = Column(
        Integer,
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Version info
    version = Column(String(20), nullable=False)  # e.g., "2.3", "1.0"
    version_number = Column(Integer, nullable=False)  # Auto-incrementing: 1, 2, 3...

    # Content snapshot at this version
    content = Column(Text, nullable=True)
    content_format = Column(String(20), default="markdown")

    # Status at time of version
    status = Column(String(20), nullable=False)

    # Change tracking
    change_summary = Column(Text, nullable=True)  # What changed in this version
    change_proposal_id = Column(
        Integer,
        ForeignKey("change_proposals.id", ondelete="SET NULL"),
        nullable=True
    )  # Which proposal created this version

    # Who made the change
    created_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    # Metadata snapshot
    metadata_snapshot = Column(JSON, default=dict)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    artifact = relationship("Artifact", backref="versions")
    created_by = relationship("User", backref="artifact_versions")
    # change_proposal = relationship("ChangeProposal", backref="created_version")

    def __repr__(self):
        return f"<ArtifactVersion {self.artifact_id} v{self.version}>"

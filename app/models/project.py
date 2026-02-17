"""
Project model for Product Development Lifecycle Platform.

A Project represents a product development initiative that goes through
multiple workflow stages (Research → PRD → UX → Tech Spec → etc.)

Each project contains:
- Multiple artifacts (PRD, Tech Spec, Designs, etc.)
- Change proposals waiting for approval
- Stage transitions with exit criteria
- Associated canvas for visualization
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Boolean, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.core.database import Base


class WorkflowStage(str, enum.Enum):
    """Product development workflow stages."""
    RESEARCH = "research"
    PRD_REVIEW = "prd_review"
    UX_REVIEW = "ux_review"
    TECH_SPEC = "tech_spec"
    PROJECT_KICKOFF = "project_kickoff"
    DEVELOPMENT = "development"
    QA = "qa"
    LAUNCH = "launch"
    RETROSPECTIVE = "retrospective"


class ProjectStatus(str, enum.Enum):
    """Overall project status."""
    PLANNING = "planning"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Project(Base):
    """
    Represents a product development project going through workflow stages.

    A project ties together:
    - Canvas (visualization)
    - Artifacts (PRD, Tech Spec, etc.)
    - Change proposals (staged changes)
    - Stage transitions (workflow progression)
    """
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)

    # Basic info
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Workflow state
    current_stage = Column(
        String(50),
        default=WorkflowStage.RESEARCH.value,
        nullable=False,
        index=True
    )
    status = Column(
        String(20),
        default=ProjectStatus.PLANNING.value,
        nullable=False,
        index=True
    )

    # Canvas association (multiple projects can share same canvas)
    canvas_id = Column(
        Integer,
        ForeignKey("canvases.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

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

    # Timeline
    planned_start_date = Column(DateTime, nullable=True)
    planned_launch_date = Column(DateTime, nullable=True)
    actual_launch_date = Column(DateTime, nullable=True)

    # Settings
    settings = Column(JSON, default=dict)  # Project-specific settings

    # Exit criteria for current stage (checklist)
    exit_criteria = Column(JSON, default=list)  # List of checklist items

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    organization = relationship("Organization", backref="projects")
    created_by = relationship("User", backref="created_projects")
    canvas = relationship("Canvas", backref="projects")

    # Forward declarations for relationships defined in other models
    # artifacts = relationship("Artifact", back_populates="project")
    # change_proposals = relationship("ChangeProposal", back_populates="project")
    # stage_transitions = relationship("StageTransition", back_populates="project")

    def __repr__(self):
        return f"<Project {self.id}: {self.name} ({self.current_stage})>"

    @property
    def is_active(self) -> bool:
        """Check if project is currently active."""
        return self.status == ProjectStatus.ACTIVE.value

    @property
    def is_on_track(self) -> bool:
        """Check if project is on track based on timeline."""
        if not self.planned_launch_date:
            return True  # No deadline = no tracking
        if self.actual_launch_date:
            return self.actual_launch_date <= self.planned_launch_date
        return datetime.utcnow() <= self.planned_launch_date


class StageTransition(Base):
    """
    Tracks when a project moves from one workflow stage to another.

    Includes exit criteria checklist and stakeholder approval.
    """
    __tablename__ = "stage_transitions"

    id = Column(Integer, primary_key=True, index=True)

    # Project reference
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Transition details
    from_stage = Column(String(50), nullable=False)
    to_stage = Column(String(50), nullable=False)

    # Approval
    approved_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )
    approved_at = Column(DateTime, nullable=True)

    # Exit criteria at time of transition
    exit_criteria_snapshot = Column(JSON, default=list)  # Checklist state
    all_criteria_met = Column(Boolean, default=False)

    # Notes
    notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    project = relationship("Project", backref="stage_transitions")
    approved_by = relationship("User", backref="approved_transitions")

    def __repr__(self):
        return f"<StageTransition {self.from_stage} → {self.to_stage}>"

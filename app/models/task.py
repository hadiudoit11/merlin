"""
Task model for extracted action items.

Tasks can be extracted from:
- Zoom meetings (via transcript processing)
- Slack messages
- Manual creation
- Other skills

Tasks can be linked to nodes on the canvas (e.g., related to a Key Result or Problem).
"""
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    Text, JSON, Boolean, Enum, Table
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.core.database import Base


class TaskStatus(str, enum.Enum):
    """Task status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(str, enum.Enum):
    """Task priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskSource(str, enum.Enum):
    """Where the task originated from."""
    MANUAL = "manual"
    ZOOM = "zoom"
    SLACK = "slack"
    CALENDAR = "calendar"
    EMAIL = "email"
    JIRA = "jira"
    AI_EXTRACTED = "ai_extracted"


# Many-to-many relationship between tasks and nodes
task_node_links = Table(
    'task_node_links',
    Base.metadata,
    Column('task_id', Integer, ForeignKey('tasks.id', ondelete='CASCADE'), primary_key=True),
    Column('node_id', Integer, ForeignKey('nodes.id', ondelete='CASCADE'), primary_key=True),
    Column('link_type', String(50), default='related'),  # related, blocks, blocked_by, parent, child
    Column('created_at', DateTime, default=datetime.utcnow),
)


class Task(Base):
    """
    Represents an action item or task.

    Can be extracted from meetings, messages, or created manually.
    Links to nodes on the canvas to show relationships.
    """
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)

    # Organization/User ownership
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Task content
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)

    # Assignment
    assignee_name = Column(String(255), nullable=True)  # Name from transcript
    assignee_email = Column(String(255), nullable=True)  # Email if available
    assignee_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )  # Linked user if matched

    # Timing
    due_date = Column(DateTime, nullable=True)
    due_date_text = Column(String(100), nullable=True)  # Original text like "next Friday"
    completed_at = Column(DateTime, nullable=True)

    # Status and priority
    status = Column(
        String(20),
        default=TaskStatus.PENDING.value,
        nullable=False,
        index=True
    )
    priority = Column(
        String(20),
        default=TaskPriority.MEDIUM.value,
        nullable=False
    )

    # Source tracking
    source = Column(
        String(20),
        default=TaskSource.MANUAL.value,
        nullable=False,
        index=True
    )
    source_id = Column(String(255), nullable=True)  # External ID (meeting ID, message ID, etc.)
    source_url = Column(String(1000), nullable=True)  # Link back to source

    # Context from extraction
    context = Column(Text, nullable=True)  # Surrounding context from transcript
    extraction_confidence = Column(Integer, nullable=True)  # 0-100 confidence score

    # Canvas association
    canvas_id = Column(
        Integer,
        ForeignKey("canvases.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Extra data
    extra_data = Column(JSON, default=dict)  # Renamed from metadata (reserved by SQLAlchemy)
    tags = Column(JSON, default=list)  # List of tag strings

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    organization = relationship("Organization", backref="tasks")
    creator = relationship("User", foreign_keys=[user_id], backref="created_tasks")
    assignee = relationship("User", foreign_keys=[assignee_user_id], backref="assigned_tasks")
    canvas = relationship("Canvas", backref="tasks")

    # Many-to-many with nodes
    linked_nodes = relationship(
        "Node",
        secondary=task_node_links,
        backref="linked_tasks"
    )

    def __repr__(self):
        return f"<Task {self.id}: {self.title[:30]}...>"

    @property
    def is_overdue(self) -> bool:
        """Check if task is past due date."""
        if not self.due_date or self.status == TaskStatus.COMPLETED.value:
            return False
        return datetime.utcnow() > self.due_date


class InputEvent(Base):
    """
    Tracks incoming events from integrations.

    Used by the InputProcessor to manage processing pipeline.
    """
    __tablename__ = "input_events"

    id = Column(Integer, primary_key=True, index=True)

    # Source skill
    skill_id = Column(
        Integer,
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    source_type = Column(String(50), nullable=False, index=True)  # zoom, slack, calendar, etc.

    # Event identification
    event_type = Column(String(100), nullable=False)  # meeting.ended, message.created, etc.
    external_id = Column(String(255), nullable=True, index=True)  # External event ID

    # Payload
    payload = Column(JSON, default=dict)  # Raw event data

    # Processing state
    status = Column(String(20), default="pending", index=True)  # pending, processing, completed, failed
    processing_started_at = Column(DateTime, nullable=True)
    processing_completed_at = Column(DateTime, nullable=True)
    processing_error = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    # Results
    results = Column(JSON, default=dict)  # Processing results
    created_task_ids = Column(JSON, default=list)
    created_node_ids = Column(JSON, default=list)

    # Organization context
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    skill = relationship("Skill", backref="input_events")
    organization = relationship("Organization", backref="input_events")

    def __repr__(self):
        return f"<InputEvent {self.source_type}:{self.event_type} status={self.status}>"

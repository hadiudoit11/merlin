"""
Integration models for external service connections.

Supports OAuth-based integrations like Confluence, Notion, etc.
Tokens are encrypted at rest using Fernet symmetric encryption.
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Enum, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.core.database import Base


class IntegrationProvider(str, enum.Enum):
    """Supported integration providers."""
    CONFLUENCE = "confluence"
    SLACK = "slack"
    NOTION = "notion"
    GOOGLE_DOCS = "google_docs"
    GITHUB = "github"
    ZOOM = "zoom"
    JIRA = "jira"
    GOOGLE_CALENDAR = "google_calendar"


class SyncDirection(str, enum.Enum):
    """Direction of sync between Merlin and external service."""
    IMPORT = "import"          # External -> Merlin only
    EXPORT = "export"          # Merlin -> External only
    BIDIRECTIONAL = "bidirectional"  # Both ways


class SyncStatus(str, enum.Enum):
    """Current sync status."""
    IDLE = "idle"
    SYNCING = "syncing"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class Integration(Base):
    """
    Integration connection supporting individual, organization, and hybrid user-level connections.

    Three modes:
    - Individual user: organization_id is NULL, user_id is SET (personal account, no org)
    - Organization-level: organization_id is SET, user_id is NULL (shared by all org members)
    - Personal override: organization_id is SET, user_id is SET (user's personal within org context)

    Fallback chain: Personal override → Org connection → Individual → Not connected
    """
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True, index=True)

    # Organization this integration belongs to (NULL for individual users)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)

    # User for personal/individual connections
    # - If org_id is NULL: individual user's integration (no org membership)
    # - If org_id is SET: personal override within org context
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    # Provider info
    provider = Column(Enum(IntegrationProvider), nullable=False)

    # OAuth tokens (encrypted in production)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)

    # Provider-specific data
    # For Confluence: { "cloud_id": "xxx", "site_url": "xxx.atlassian.net" }
    provider_data = Column(JSON, default=dict)

    # Pre-configured settings from org admin
    # Merged with org-level integration_settings when connecting
    config = Column(JSON, default=dict)

    # Whether this uses org-level credentials (vs user OAuth)
    uses_org_credentials = Column(Boolean, default=False)

    # Status
    status = Column(Enum(SyncStatus), default=SyncStatus.IDLE)
    last_error = Column(Text, nullable=True)

    # Who connected this integration
    connected_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    organization = relationship("Organization", backref="integrations")
    user = relationship("User", foreign_keys=[user_id], backref="personal_integrations")
    connected_by = relationship("User", foreign_keys=[connected_by_id])
    space_integrations = relationship("SpaceIntegration", back_populates="integration", cascade="all, delete-orphan")

    @property
    def is_individual(self) -> bool:
        """Check if this is an individual user's integration (no org)."""
        return self.organization_id is None and self.user_id is not None

    @property
    def is_personal(self) -> bool:
        """Check if this is a user-level (personal) integration within an org."""
        return self.organization_id is not None and self.user_id is not None

    @property
    def is_org_level(self) -> bool:
        """Check if this is an org-level shared integration."""
        return self.organization_id is not None and self.user_id is None

    @property
    def scope_label(self) -> str:
        """Human-readable scope label."""
        if self.is_individual:
            return "Individual"
        elif self.is_personal:
            return "Personal"
        else:
            return "Organization"

    @property
    def is_token_expired(self) -> bool:
        """Check if the access token has expired."""
        if not self.token_expires_at:
            return False
        return datetime.utcnow() > self.token_expires_at

    @property
    def is_connected(self) -> bool:
        """Check if integration has valid credentials."""
        return self.access_token is not None and self.status != SyncStatus.DISCONNECTED


class SpaceIntegration(Base):
    """
    Links a Merlin document space to an external space (e.g., Confluence space).

    Allows per-space sync configuration and tracking.
    """
    __tablename__ = "space_integrations"

    id = Column(Integer, primary_key=True, index=True)

    # Link to organization integration
    integration_id = Column(Integer, ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False)

    # Merlin space (could be a canvas or doc space)
    # For now using string ID to be flexible
    space_id = Column(String(255), nullable=False, index=True)
    space_type = Column(String(50), default="document")  # "document" or "canvas"

    # External space info
    # For Confluence: key="ENG", external_id="12345", name="Engineering"
    external_space_key = Column(String(255), nullable=True)
    external_space_id = Column(String(255), nullable=True)
    external_space_name = Column(String(500), nullable=True)

    # Sync settings
    sync_enabled = Column(Boolean, default=True)
    sync_direction = Column(Enum(SyncDirection), default=SyncDirection.BIDIRECTIONAL)
    auto_sync = Column(Boolean, default=False)  # Auto-sync on changes

    # Sync status
    sync_status = Column(Enum(SyncStatus), default=SyncStatus.IDLE)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_error = Column(Text, nullable=True)

    # Page mappings: { "merlin_page_id": "confluence_page_id", ... }
    page_mappings = Column(JSON, default=dict)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    integration = relationship("Integration", back_populates="space_integrations")
    page_syncs = relationship("PageSync", back_populates="space_integration", cascade="all, delete-orphan")


class PageSync(Base):
    """
    Tracks sync status for individual pages/documents.
    """
    __tablename__ = "page_syncs"

    id = Column(Integer, primary_key=True, index=True)

    # Link to space integration
    space_integration_id = Column(Integer, ForeignKey("space_integrations.id", ondelete="CASCADE"), nullable=False)

    # Merlin page
    page_id = Column(String(255), nullable=False, index=True)

    # External page
    external_page_id = Column(String(255), nullable=False)
    external_page_url = Column(String(1000), nullable=True)

    # Version tracking for conflict detection
    local_version = Column(Integer, default=1)
    remote_version = Column(Integer, default=1)

    # Sync status
    sync_status = Column(Enum(SyncStatus), default=SyncStatus.IDLE)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_direction = Column(String(20), nullable=True)  # "import" or "export"

    # Conflict handling
    has_conflict = Column(Boolean, default=False)
    conflict_data = Column(JSON, nullable=True)  # Store both versions if conflict

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    space_integration = relationship("SpaceIntegration", back_populates="page_syncs")


class MeetingImport(Base):
    """
    Tracks imported meetings from Zoom (or other video conferencing).

    Stores the transcript and extracted notes/action items.
    """
    __tablename__ = "meeting_imports"

    id = Column(Integer, primary_key=True, index=True)

    # Link to integration
    integration_id = Column(Integer, ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False)

    # Link to canvas where notes are created
    canvas_id = Column(Integer, ForeignKey("canvases.id", ondelete="SET NULL"), nullable=True)

    # Meeting info from provider
    external_meeting_id = Column(String(255), nullable=False, index=True)
    meeting_topic = Column(String(500), nullable=True)
    meeting_start_time = Column(DateTime, nullable=True)
    meeting_duration_minutes = Column(Integer, nullable=True)
    meeting_host = Column(String(255), nullable=True)
    meeting_participants = Column(JSON, default=list)  # List of participant names/emails

    # Recording info
    recording_id = Column(String(255), nullable=True)
    recording_url = Column(String(1000), nullable=True)

    # Transcript
    transcript_raw = Column(Text, nullable=True)  # Raw transcript text
    transcript_segments = Column(JSON, default=list)  # [{speaker, text, start_time, end_time}, ...]

    # AI-processed content
    summary = Column(Text, nullable=True)
    key_points = Column(JSON, default=list)  # List of key discussion points
    action_items = Column(JSON, default=list)  # [{task, assignee, due_date}, ...]
    decisions = Column(JSON, default=list)  # List of decisions made

    # Created nodes
    doc_node_id = Column(Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)
    created_node_ids = Column(JSON, default=list)  # IDs of all nodes created from this meeting

    # Processing status
    status = Column(String(50), default="pending")  # pending, processing, completed, error
    processing_error = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

    # Relationships
    integration = relationship("Integration", backref="meeting_imports")
    canvas = relationship("Canvas", backref="meeting_imports")

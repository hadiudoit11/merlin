"""
Pydantic schemas for Skills API.

Handles external service connections like Confluence, Notion, etc.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class SkillProvider(str, Enum):
    """Supported skill providers."""
    CONFLUENCE = "confluence"
    SLACK = "slack"
    NOTION = "notion"
    GOOGLE_DOCS = "google_docs"
    GITHUB = "github"
    ZOOM = "zoom"
    JIRA = "jira"
    GOOGLE_CALENDAR = "google_calendar"


class SyncDirection(str, Enum):
    """Direction of sync."""
    IMPORT = "import"
    EXPORT = "export"
    BIDIRECTIONAL = "bidirectional"


class SyncStatus(str, Enum):
    """Current sync status."""
    IDLE = "idle"
    SYNCING = "syncing"
    ERROR = "error"
    DISCONNECTED = "disconnected"


# ============ Skill (Org-level connection) ============

class SkillBase(BaseModel):
    """Base skill fields."""
    provider: SkillProvider


class SkillResponse(BaseModel):
    """Skill response (no sensitive tokens)."""
    id: int
    provider: SkillProvider
    status: SyncStatus
    is_connected: bool
    provider_data: Dict[str, Any] = {}
    connected_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    # Confluence-specific fields (from provider_data)
    site_url: Optional[str] = None
    cloud_id: Optional[str] = None

    class Config:
        from_attributes = True


class SkillBrief(BaseModel):
    """Brief skill info for lists."""
    id: int
    provider: SkillProvider
    status: SyncStatus
    is_connected: bool
    site_url: Optional[str] = None

    class Config:
        from_attributes = True


class OAuthInitResponse(BaseModel):
    """Response when initiating OAuth flow."""
    auth_url: str
    state: str


class OAuthCallbackRequest(BaseModel):
    """OAuth callback data."""
    code: str
    state: str


# ============ Confluence-specific ============

class ConfluenceSpace(BaseModel):
    """Confluence space from API."""
    id: str
    key: str
    name: str
    type: str = "global"
    icon: Optional[str] = None
    description: Optional[str] = None


class ConfluencePage(BaseModel):
    """Confluence page from API."""
    id: str
    title: str
    space_key: str
    version: int
    web_url: Optional[str] = None
    body_html: Optional[str] = None


class ConfluencePageList(BaseModel):
    """Paginated list of Confluence pages."""
    pages: List[ConfluencePage]
    total: int
    start: int
    limit: int


# ============ Space Skill ============

class SpaceSkillCreate(BaseModel):
    """Create space skill request."""
    space_id: str
    external_space_key: str
    sync_direction: SyncDirection = SyncDirection.BIDIRECTIONAL
    auto_sync: bool = False


class SpaceSkillUpdate(BaseModel):
    """Update space skill settings."""
    sync_enabled: Optional[bool] = None
    sync_direction: Optional[SyncDirection] = None
    auto_sync: Optional[bool] = None


class SpaceSkillResponse(BaseModel):
    """Space skill response."""
    id: int
    skill_id: int
    space_id: str
    space_type: str

    # External space info
    external_space_key: Optional[str] = None
    external_space_id: Optional[str] = None
    external_space_name: Optional[str] = None

    # Sync settings
    sync_enabled: bool
    sync_direction: SyncDirection
    auto_sync: bool

    # Status
    sync_status: SyncStatus
    last_sync_at: Optional[datetime] = None
    last_sync_error: Optional[str] = None

    # Timestamps
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ Import/Export ============

class ImportRequest(BaseModel):
    """Import pages from external service."""
    page_ids: List[str]
    folder_id: Optional[str] = None
    preserve_hierarchy: bool = False


class ExportRequest(BaseModel):
    """Export pages to external service."""
    page_ids: List[str]
    target_parent_id: Optional[str] = None  # Parent page in external service


class SyncResult(BaseModel):
    """Result of import/export operation."""
    success: bool
    imported: int = 0
    exported: int = 0
    conflicts: int = 0
    errors: List[str] = []


class SyncNowRequest(BaseModel):
    """Trigger manual sync."""
    direction: Optional[SyncDirection] = None  # None = use space config


# ============ Page Sync ============

class PageSyncResponse(BaseModel):
    """Individual page sync status."""
    id: int
    page_id: str
    external_page_id: str
    external_page_url: Optional[str] = None
    local_version: int
    remote_version: int
    sync_status: SyncStatus
    has_conflict: bool
    last_sync_at: Optional[datetime] = None
    last_sync_direction: Optional[str] = None

    class Config:
        from_attributes = True


class ConflictResolution(BaseModel):
    """Resolve a sync conflict."""
    keep: str = Field(..., pattern="^(local|remote)$")  # "local" or "remote"


# ============ Provider Info ============

class ProviderInfo(BaseModel):
    """Information about a skill provider."""
    id: SkillProvider
    name: str
    description: str
    icon: str
    is_configured: bool
    auth_type: str = "oauth"
    scopes: List[str] = []


# ============ Slack-specific ============

class SlackTeam(BaseModel):
    """Slack workspace/team info."""
    id: str
    name: str
    domain: str
    icon_url: Optional[str] = None


class SlackChannel(BaseModel):
    """Slack channel from API."""
    id: str
    name: str
    is_private: bool = False
    is_archived: bool = False
    topic: Optional[str] = None
    purpose: Optional[str] = None
    num_members: int = 0


class SlackChannelList(BaseModel):
    """Paginated list of Slack channels."""
    channels: List[SlackChannel]
    cursor: Optional[str] = None


class SlackUser(BaseModel):
    """Slack user from API."""
    id: str
    name: str
    real_name: Optional[str] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    is_bot: bool = False
    is_admin: bool = False


class SlackUserList(BaseModel):
    """Paginated list of Slack users."""
    users: List[SlackUser]
    cursor: Optional[str] = None


class SlackMessage(BaseModel):
    """Slack message from API."""
    ts: str
    text: str
    user_id: Optional[str] = None
    channel_id: Optional[str] = None
    thread_ts: Optional[str] = None
    reply_count: int = 0
    timestamp: Optional[datetime] = None


class SlackMessageList(BaseModel):
    """Paginated list of Slack messages."""
    messages: List[SlackMessage]
    has_more: bool = False
    cursor: Optional[str] = None


class SlackPostMessageRequest(BaseModel):
    """Request to post a message to Slack."""
    channel_id: str
    text: str
    thread_ts: Optional[str] = None
    unfurl_links: bool = True


class SlackSearchRequest(BaseModel):
    """Request to search Slack messages."""
    query: str
    sort: str = "timestamp"
    sort_dir: str = "desc"
    count: int = 20
    page: int = 1


class SlackSearchResult(BaseModel):
    """Slack search results."""
    messages: List[SlackMessage]
    total: int
    page: int
    pages: int


# ============ Skill Prompts ============

class SkillPromptCreate(BaseModel):
    """Create a skill prompt."""
    canvas_id: int
    skill_name: str
    action: str = "*"
    prompt_template: str
    created_by: Optional[str] = None


class SkillPromptUpdate(BaseModel):
    """Update a skill prompt."""
    prompt_template: Optional[str] = None
    is_active: Optional[bool] = None


class SkillPromptBrief(BaseModel):
    """Brief skill prompt info for lists."""
    id: int
    canvas_id: int
    skill_name: str
    action: str
    is_active: bool
    last_used_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SkillPromptResponse(BaseModel):
    """Full skill prompt response."""
    id: int
    canvas_id: int
    skill_name: str
    action: str
    prompt_template: str
    is_active: bool
    created_by: Optional[str] = None
    last_edited_by: Optional[str] = None
    last_used_at: Optional[datetime] = None
    history: List[Dict[str, Any]] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SkillPromptGenerateRequest(BaseModel):
    """Request to generate content using a skill prompt."""
    prompt_id: int
    context: Dict[str, Any] = {}


class SkillPromptGenerateResponse(BaseModel):
    """Response from generating content with a skill prompt."""
    content: str
    prompt_used: str

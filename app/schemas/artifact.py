"""Pydantic schemas for Product Development Platform - Artifacts."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.models.artifact import ArtifactType, ArtifactStatus


class ArtifactBase(BaseModel):
    """Base artifact schema."""
    name: str = Field(..., max_length=255)
    artifact_type: ArtifactType
    content: Optional[str] = None
    content_format: str = "markdown"
    status: ArtifactStatus = ArtifactStatus.DRAFT
    version: str = "1.0"
    settings: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class ArtifactCreate(ArtifactBase):
    """Create a new artifact."""
    project_id: int
    canvas_id: Optional[int] = None
    node_id: Optional[int] = None


class ArtifactUpdate(BaseModel):
    """Update artifact fields."""
    name: Optional[str] = Field(None, max_length=255)
    content: Optional[str] = None
    content_format: Optional[str] = None
    status: Optional[ArtifactStatus] = None
    current_owner_id: Optional[int] = None
    settings: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    node_id: Optional[int] = None


class ArtifactResponse(ArtifactBase):
    """Artifact response with all fields."""
    id: int
    project_id: int
    canvas_id: Optional[int] = None
    node_id: Optional[int] = None
    organization_id: int
    created_by_id: Optional[int] = None
    current_owner_id: Optional[int] = None
    version_counter: int
    created_at: datetime
    updated_at: datetime
    approved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ArtifactWithVersionsResponse(ArtifactResponse):
    """Artifact with version history."""
    versions: List["ArtifactVersionResponse"] = Field(default_factory=list)

    class Config:
        from_attributes = True


# Artifact Version schemas
class ArtifactVersionBase(BaseModel):
    """Base artifact version schema."""
    version: str
    content: Optional[str] = None
    content_format: str = "markdown"
    status: str
    change_summary: Optional[str] = None
    metadata_snapshot: Dict[str, Any] = Field(default_factory=dict)


class ArtifactVersionCreate(ArtifactVersionBase):
    """Create artifact version (usually system-generated)."""
    artifact_id: int
    version_number: int
    change_proposal_id: Optional[int] = None


class ArtifactVersionResponse(ArtifactVersionBase):
    """Artifact version response."""
    id: int
    artifact_id: int
    version_number: int
    change_proposal_id: Optional[int] = None
    created_by_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


# Forward declarations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass

"""Pydantic schemas for Product Development Platform - Projects."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.models.project import WorkflowStage, ProjectStatus


class ProjectBase(BaseModel):
    """Base project schema."""
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    current_stage: WorkflowStage = WorkflowStage.RESEARCH
    status: ProjectStatus = ProjectStatus.PLANNING
    canvas_id: Optional[int] = None
    planned_start_date: Optional[datetime] = None
    planned_launch_date: Optional[datetime] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    exit_criteria: List[Dict[str, Any]] = Field(default_factory=list)


class ProjectCreate(ProjectBase):
    """Create a new project."""
    organization_id: int  # Required


class ProjectUpdate(BaseModel):
    """Update project fields."""
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    current_stage: Optional[WorkflowStage] = None
    status: Optional[ProjectStatus] = None
    canvas_id: Optional[int] = None
    planned_start_date: Optional[datetime] = None
    planned_launch_date: Optional[datetime] = None
    actual_launch_date: Optional[datetime] = None
    settings: Optional[Dict[str, Any]] = None
    exit_criteria: Optional[List[Dict[str, Any]]] = None


class ProjectResponse(ProjectBase):
    """Project response with all fields."""
    id: int
    organization_id: int
    created_by_id: Optional[int] = None
    actual_launch_date: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectWithArtifactsResponse(ProjectResponse):
    """Project with related artifacts."""
    artifacts: List["ArtifactResponse"] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ProjectWithDetailsResponse(ProjectResponse):
    """Project with full details."""
    artifacts: List["ArtifactResponse"] = Field(default_factory=list)
    pending_proposals: List["ChangeProposalResponse"] = Field(default_factory=list)
    recent_transitions: List["StageTransitionResponse"] = Field(default_factory=list)

    class Config:
        from_attributes = True


# Stage Transition schemas
class StageTransitionBase(BaseModel):
    """Base stage transition schema."""
    from_stage: WorkflowStage
    to_stage: WorkflowStage
    notes: Optional[str] = None
    exit_criteria_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    all_criteria_met: bool = False


class StageTransitionCreate(StageTransitionBase):
    """Create a stage transition."""
    project_id: int


class StageTransitionResponse(StageTransitionBase):
    """Stage transition response."""
    id: int
    project_id: int
    approved_by_id: Optional[int] = None
    approved_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# Forward declarations - will be imported at end
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.schemas.artifact import ArtifactResponse
    from app.schemas.change_proposal import ChangeProposalResponse

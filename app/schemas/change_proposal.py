"""Pydantic schemas for Product Development Platform - Change Proposals."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.models.change_proposal import (
    ChangeProposalStatus,
    ChangeSeverity,
    ChangeType,
)


class ChangeProposalBase(BaseModel):
    """Base change proposal schema."""
    artifact_id: int
    project_id: int
    triggered_by_type: str = Field(..., max_length=50)
    triggered_by_id: Optional[str] = Field(None, max_length=255)
    triggered_by_url: Optional[str] = Field(None, max_length=1000)
    change_type: ChangeType
    severity: ChangeSeverity = ChangeSeverity.MEDIUM
    title: str = Field(..., max_length=500)
    description: Optional[str] = None
    proposed_changes: Dict[str, Any]
    ai_rationale: Optional[str] = None
    ai_confidence_score: Optional[int] = Field(None, ge=0, le=100)
    impact_analysis: Dict[str, Any] = Field(default_factory=dict)


class ChangeProposalCreate(ChangeProposalBase):
    """Create a new change proposal."""
    input_event_id: Optional[int] = None
    assigned_to_id: Optional[int] = None
    expires_at: Optional[datetime] = None


class ChangeProposalUpdate(BaseModel):
    """Update change proposal (mainly for approval workflow)."""
    status: Optional[ChangeProposalStatus] = None
    assigned_to_id: Optional[int] = None
    review_notes: Optional[str] = None


class ChangeProposalApprove(BaseModel):
    """Approve a change proposal."""
    review_notes: Optional[str] = None


class ChangeProposalReject(BaseModel):
    """Reject a change proposal."""
    review_notes: str  # Required for rejection


class ChangeProposalResponse(ChangeProposalBase):
    """Change proposal response with all fields."""
    id: int
    input_event_id: Optional[int] = None
    status: ChangeProposalStatus
    assigned_to_id: Optional[int] = None
    reviewed_by_id: Optional[int] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    applied_at: Optional[datetime] = None
    created_version_id: Optional[int] = None
    organization_id: int
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChangeProposalWithDetailsResponse(ChangeProposalResponse):
    """Change proposal with artifact and impact analysis."""
    artifact: Optional["ArtifactResponse"] = None
    impact: Optional["ImpactAnalysisResponse"] = None

    class Config:
        from_attributes = True


# Impact Analysis schemas
class ImpactAnalysisBase(BaseModel):
    """Base impact analysis schema."""
    affected_artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    timeline_impact: Dict[str, Any] = Field(default_factory=dict)
    dependency_changes: List[Dict[str, Any]] = Field(default_factory=list)
    risk_assessment: Dict[str, Any] = Field(default_factory=dict)
    ai_model_used: Optional[str] = Field(None, max_length=100)
    ai_confidence: Optional[int] = Field(None, ge=0, le=100)
    analysis_prompt: Optional[str] = None


class ImpactAnalysisCreate(ImpactAnalysisBase):
    """Create impact analysis (usually system-generated with proposal)."""
    change_proposal_id: int


class ImpactAnalysisResponse(ImpactAnalysisBase):
    """Impact analysis response."""
    id: int
    change_proposal_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Forward declarations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.schemas.artifact import ArtifactResponse

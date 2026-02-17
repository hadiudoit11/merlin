"""
ChangeProposal model for Product Development Platform.

A ChangeProposal represents a proposed change to an artifact that is waiting for approval.

Flow:
1. InputEvent arrives (Jira issue, Zoom meeting, etc.)
2. AI analyzes impact on artifacts
3. ChangeProposal created for each affected artifact
4. Assigned to stakeholder for review
5. Stakeholder approves/rejects
6. If approved → new ArtifactVersion created
7. If rejected → ChangeProposal archived

Example:
- Jira issue "PROJ-456: Add OAuth" comes in
- AI detects it affects PRD, Tech Spec, Timeline
- 3 ChangeProposals created (one per artifact)
- Product Owner reviews PRD change → approves
- Tech Lead reviews Tech Spec change → approves
- PM reviews Timeline change → approves
- All changes applied, artifacts versioned
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Boolean, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.core.database import Base


class ChangeProposalStatus(str, enum.Enum):
    """Status of change proposal."""
    PENDING = "pending"  # Waiting for review
    UNDER_REVIEW = "under_review"  # Being reviewed
    APPROVED = "approved"  # Approved and applied
    REJECTED = "rejected"  # Rejected by stakeholder
    SUPERSEDED = "superseded"  # Newer proposal created
    EXPIRED = "expired"  # Auto-expired after timeout


class ChangeSeverity(str, enum.Enum):
    """Impact severity of proposed change."""
    LOW = "low"  # Minor change, low impact
    MEDIUM = "medium"  # Moderate change
    HIGH = "high"  # Significant change
    CRITICAL = "critical"  # Major architectural/scope change


class ChangeType(str, enum.Enum):
    """Type of change being proposed."""
    NEW_REQUIREMENT = "new_requirement"  # Adding new feature/requirement
    UPDATE_REQUIREMENT = "update_requirement"  # Modifying existing requirement
    REMOVE_REQUIREMENT = "remove_requirement"  # Removing requirement
    TIMELINE_CHANGE = "timeline_change"  # Changing timeline/dates
    SCOPE_CHANGE = "scope_change"  # Changing project scope
    TECHNICAL_CHANGE = "technical_change"  # Technical architecture change
    DESIGN_CHANGE = "design_change"  # UX/design change
    CONTENT_UPDATE = "content_update"  # General content update
    CLARIFICATION = "clarification"  # Adding clarification


class ChangeProposal(Base):
    """
    Represents a proposed change to an artifact waiting for approval.

    Contains:
    - What triggered the change (source event)
    - What artifact is affected
    - Proposed changes (diff)
    - AI rationale
    - Impact analysis
    - Approval workflow
    """
    __tablename__ = "change_proposals"

    id = Column(Integer, primary_key=True, index=True)

    # What is being changed
    artifact_id = Column(
        Integer,
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # What triggered this change
    triggered_by_type = Column(String(50), nullable=False)  # jira_issue, zoom_meeting, slack_message
    triggered_by_id = Column(String(255), nullable=True)  # PROJ-456, meeting UUID, etc.
    triggered_by_url = Column(String(1000), nullable=True)  # Link to source

    # Input event that triggered this
    input_event_id = Column(
        Integer,
        ForeignKey("input_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Proposed change details
    change_type = Column(
        String(50),
        nullable=False,
        index=True
    )
    severity = Column(
        String(20),
        default=ChangeSeverity.MEDIUM.value,
        nullable=False,
        index=True
    )

    # Change content
    title = Column(String(500), nullable=False)  # "Add OAuth Login to Features"
    description = Column(Text, nullable=True)  # Detailed description

    # Diff (before/after)
    proposed_changes = Column(JSON, nullable=False)  # Structured diff
    """
    Example proposed_changes:
    {
        "sections": [
            {
                "section": "Features",
                "action": "add",
                "content": "OAuth Login - Allow users to login with Google/GitHub",
                "position": "after:Social Login"
            }
        ],
        "metadata": {
            "success_metrics_updated": true
        }
    }
    """

    # AI rationale
    ai_rationale = Column(Text, nullable=True)  # Why AI thinks this change is needed
    ai_confidence_score = Column(Integer, nullable=True)  # 0-100

    # Impact analysis (cross-artifact)
    impact_analysis = Column(JSON, default=dict)
    """
    Example impact_analysis:
    {
        "affected_artifacts": [
            {
                "artifact_id": 123,
                "artifact_name": "Tech Spec",
                "impact_type": "requires_update",
                "reason": "OAuth requires new authentication architecture"
            }
        ],
        "timeline_impact": {
            "estimated_delay": "2 weeks",
            "affected_milestones": ["Sprint 5 Launch"]
        }
    }
    """

    # Approval workflow
    status = Column(
        String(20),
        default=ChangeProposalStatus.PENDING.value,
        nullable=False,
        index=True
    )
    assigned_to_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    reviewed_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )
    reviewed_at = Column(DateTime, nullable=True)
    review_notes = Column(Text, nullable=True)

    # Applied?
    applied_at = Column(DateTime, nullable=True)
    created_version_id = Column(
        Integer,
        ForeignKey("artifact_versions.id", ondelete="SET NULL"),
        nullable=True
    )  # ArtifactVersion created when approved

    # Organization context
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)  # Auto-expire after N days

    # Relationships
    artifact = relationship("Artifact", backref="change_proposals")
    project = relationship("Project", backref="change_proposals")
    input_event = relationship("InputEvent", backref="change_proposals")
    assigned_to = relationship("User", foreign_keys=[assigned_to_id], backref="assigned_proposals")
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id], backref="reviewed_proposals")
    created_version = relationship("ArtifactVersion", backref="source_proposal", foreign_keys=[created_version_id])
    organization = relationship("Organization", backref="change_proposals")
    impact_analysis_detail = relationship("ImpactAnalysis", back_populates="change_proposal", uselist=False)

    def __repr__(self):
        return f"<ChangeProposal {self.id}: {self.title[:30]}... ({self.status})>"

    @property
    def is_pending(self) -> bool:
        """Check if proposal is pending review."""
        return self.status == ChangeProposalStatus.PENDING.value

    @property
    def is_approved(self) -> bool:
        """Check if proposal was approved."""
        return self.status == ChangeProposalStatus.APPROVED.value

    @property
    def is_rejected(self) -> bool:
        """Check if proposal was rejected."""
        return self.status == ChangeProposalStatus.REJECTED.value


class ImpactAnalysis(Base):
    """
    Tracks detailed impact analysis for a change proposal.

    When a change is proposed to one artifact, AI analyzes:
    - Which other artifacts are affected
    - How timeline is impacted
    - Which dependencies are broken/created
    - Risk assessment
    """
    __tablename__ = "impact_analyses"

    id = Column(Integer, primary_key=True, index=True)

    # Change proposal this analysis is for
    change_proposal_id = Column(
        Integer,
        ForeignKey("change_proposals.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # One analysis per proposal
        index=True
    )

    # Affected artifacts
    affected_artifacts = Column(JSON, default=list)
    """
    Example:
    [
        {
            "artifact_id": 123,
            "artifact_name": "Tech Spec v1.0",
            "artifact_type": "tech_spec",
            "impact_type": "requires_update",
            "severity": "high",
            "reason": "OAuth requires new authentication service architecture",
            "suggested_changes": [...]
        }
    ]
    """

    # Timeline impact
    timeline_impact = Column(JSON, default=dict)
    """
    Example:
    {
        "estimated_delay": "2 weeks",
        "delay_reason": "OAuth implementation + security review",
        "affected_milestones": ["Sprint 5 Launch", "Beta Release"],
        "critical_path_affected": true
    }
    """

    # Dependency impact
    dependency_changes = Column(JSON, default=list)
    """
    Example:
    [
        {
            "type": "new_dependency",
            "dependency": "OAuth provider integration",
            "affects": ["Frontend", "Backend", "Mobile"]
        }
    ]
    """

    # Risk assessment
    risk_assessment = Column(JSON, default=dict)
    """
    Example:
    {
        "overall_risk": "medium",
        "risks": [
            {
                "type": "technical",
                "description": "OAuth token management complexity",
                "likelihood": "high",
                "impact": "medium",
                "mitigation": "Use established OAuth library"
            }
        ]
    }
    """

    # AI metadata
    ai_model_used = Column(String(100), nullable=True)  # claude-3-opus, gpt-4, etc.
    ai_confidence = Column(Integer, nullable=True)  # 0-100
    analysis_prompt = Column(Text, nullable=True)  # Prompt used for analysis

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    change_proposal = relationship("ChangeProposal", back_populates="impact_analysis_detail")

    def __repr__(self):
        return f"<ImpactAnalysis for ChangeProposal {self.change_proposal_id}>"

    @property
    def has_high_impact(self) -> bool:
        """Check if any affected artifact has high severity."""
        for artifact in self.affected_artifacts or []:
            if artifact.get("severity") in ("high", "critical"):
                return True
        return False

    @property
    def affects_timeline(self) -> bool:
        """Check if this change affects the project timeline."""
        return bool(self.timeline_impact and self.timeline_impact.get("estimated_delay"))

"""
Workflow Orchestrator - Manages product development workflow automation.

This service is the heart of the Product Development Platform. It:
1. Processes new information from integrations (Jira, Zoom, Slack, etc.)
2. Analyzes impact on existing artifacts using AI
3. Generates change proposals for affected artifacts
4. Assigns proposals to appropriate stakeholders
5. Tracks approval workflow

Flow:
    InputEvent (Jira issue created)
    → WorkflowOrchestrator.process_input_event()
    → ImpactAnalyzer.analyze_impact()
    → Create ChangeProposals for each affected artifact
    → Assign to stakeholders
    → Notify stakeholders
    → Wait for approval
    → Apply changes to artifacts (when approved)
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.task import InputEvent
from app.models.project import Project
from app.models.artifact import Artifact
from app.models.change_proposal import (
    ChangeProposal,
    ImpactAnalysis,
    ChangeProposalStatus,
    ChangeSeverity,
    ChangeType
)
from app.models.user import User
from app.services.impact_analyzer import ImpactAnalyzer

logger = logging.getLogger(__name__)


class WorkflowOrchestrator:
    """Orchestrates product development workflow automation."""

    @staticmethod
    async def process_input_event(
        session: AsyncSession,
        input_event: InputEvent,
        project_id: int,
        user_id: int,
        organization_id: int,
        trigger_content: str
    ) -> Dict[str, Any]:
        """
        Process an input event and generate change proposals.

        Args:
            session: Database session
            input_event: The input event to process
            project_id: Project to analyze
            user_id: User making the request
            organization_id: Organization context
            trigger_content: Content to analyze

        Returns:
            Dict with:
            {
                "proposals_created": 5,
                "proposals": [ChangeProposal, ...],
                "impact_summary": {...}
            }
        """
        # Get project
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        logger.info(f"Processing input event {input_event.id} for project {project.name}")

        # Analyze impact using AI
        impact_analysis = await ImpactAnalyzer.analyze_impact(
            session=session,
            project=project,
            trigger_type=input_event.source_type,
            trigger_id=input_event.external_id or str(input_event.id),
            trigger_content=trigger_content,
            user_id=user_id,
            organization_id=organization_id
        )

        # Create change proposals for each affected artifact
        proposals = []
        for affected_artifact in impact_analysis.get("affected_artifacts", []):
            if "artifact_id" not in affected_artifact:
                logger.warning(f"Skipping affected artifact without ID: {affected_artifact}")
                continue

            proposal = await WorkflowOrchestrator._create_change_proposal(
                session=session,
                input_event=input_event,
                project=project,
                affected_artifact=affected_artifact,
                timeline_impact=impact_analysis.get("timeline_impact", {}),
                organization_id=organization_id
            )

            # Create impact analysis record
            await WorkflowOrchestrator._create_impact_analysis_record(
                session=session,
                proposal=proposal,
                full_impact=impact_analysis
            )

            proposals.append(proposal)

        await session.commit()

        logger.info(f"Created {len(proposals)} change proposals for input event {input_event.id}")

        return {
            "proposals_created": len(proposals),
            "proposals": proposals,
            "impact_summary": {
                "overall_severity": impact_analysis.get("overall_severity", "low"),
                "affected_artifacts_count": len(proposals),
                "has_timeline_impact": bool(impact_analysis.get("timeline_impact"))
            }
        }

    @staticmethod
    async def _create_change_proposal(
        session: AsyncSession,
        input_event: InputEvent,
        project: Project,
        affected_artifact: Dict[str, Any],
        timeline_impact: Dict[str, Any],
        organization_id: int
    ) -> ChangeProposal:
        """Create a change proposal for an affected artifact."""
        # Get artifact
        artifact = await session.get(Artifact, affected_artifact["artifact_id"])
        if not artifact:
            raise ValueError(f"Artifact {affected_artifact['artifact_id']} not found")

        # Determine severity and change type
        severity = ImpactAnalyzer.determine_change_severity(
            affected_artifact.get("severity", "medium")
        )
        change_type = ImpactAnalyzer.determine_change_type(
            affected_artifact.get("change_type", "content_update")
        )

        # Build proposed changes JSON
        proposed_changes = {
            "sections": affected_artifact.get("proposed_sections", []),
            "summary": affected_artifact.get("rationale", ""),
            "ai_suggested": True
        }

        # Generate title
        title = f"Update {artifact.name}: {change_type.value.replace('_', ' ').title()}"
        if len(title) > 100:
            title = f"Update {artifact.name}"

        # Auto-assign based on artifact type
        assigned_to_id = await WorkflowOrchestrator._determine_assignee(
            session=session,
            project=project,
            artifact=artifact,
            severity=severity
        )

        # Create proposal
        proposal = ChangeProposal(
            artifact_id=artifact.id,
            project_id=project.id,
            triggered_by_type=input_event.source_type,
            triggered_by_id=input_event.external_id or str(input_event.id),
            triggered_by_url=input_event.payload.get("url") if input_event.payload else None,
            input_event_id=input_event.id,
            change_type=change_type.value,
            severity=severity.value,
            title=title[:500],  # Truncate if needed
            description=affected_artifact.get("rationale"),
            proposed_changes=proposed_changes,
            ai_rationale=affected_artifact.get("rationale"),
            ai_confidence_score=affected_artifact.get("confidence_score", 75),
            impact_analysis={
                "timeline": timeline_impact,
                "other_artifacts": [
                    a for a in affected_artifact.get("related_artifacts", [])
                ]
            },
            status=ChangeProposalStatus.PENDING.value,
            assigned_to_id=assigned_to_id,
            organization_id=organization_id,
            expires_at=datetime.utcnow() + timedelta(days=30)  # Auto-expire after 30 days
        )

        session.add(proposal)
        await session.flush()  # Get proposal.id

        return proposal

    @staticmethod
    async def _create_impact_analysis_record(
        session: AsyncSession,
        proposal: ChangeProposal,
        full_impact: Dict[str, Any]
    ) -> ImpactAnalysis:
        """Create detailed impact analysis record."""
        impact = ImpactAnalysis(
            change_proposal_id=proposal.id,
            affected_artifacts=[
                a for a in full_impact.get("affected_artifacts", [])
                if a.get("artifact_id") != proposal.artifact_id  # Exclude self
            ],
            timeline_impact=full_impact.get("timeline_impact", {}),
            dependency_changes=[],  # TODO: Extract from AI response
            risk_assessment={
                "overall_risk": full_impact.get("overall_severity", "medium"),
                "risks": []
            },
            ai_model_used=full_impact.get("ai_model_used", "claude-3-5-sonnet-20241022"),
            ai_confidence=full_impact.get("ai_confidence", 75)
        )

        session.add(impact)
        return impact

    @staticmethod
    async def _determine_assignee(
        session: AsyncSession,
        project: Project,
        artifact: Artifact,
        severity: ChangeSeverity
    ) -> Optional[int]:
        """
        Determine who should review this change proposal.

        Logic:
        - PRD → Product Owner (project creator)
        - Tech Spec → Tech Lead (TODO: add role-based assignment)
        - UX Design → Design Lead
        - Timeline → Project Manager
        - Critical severity → Project creator
        - Default → Artifact owner or project creator
        """
        # For now, assign to artifact owner or project creator
        if artifact.current_owner_id:
            return artifact.current_owner_id

        if project.created_by_id:
            return project.created_by_id

        # TODO: Implement role-based assignment
        # - Get organization members with specific roles
        # - Assign based on artifact type + role
        # Example:
        # if artifact.artifact_type == "prd":
        #     return get_user_with_role(session, organization_id, "product_owner")

        return None

    @staticmethod
    async def apply_approved_proposal(
        session: AsyncSession,
        proposal: ChangeProposal,
        approved_by_id: int
    ) -> Dict[str, Any]:
        """
        Apply an approved change proposal to the artifact.

        This is called after a stakeholder approves a proposal.

        Steps:
        1. Apply proposed changes to artifact
        2. Create new ArtifactVersion
        3. Update proposal status
        4. Check for dependent proposals

        Returns:
            Dict with:
            {
                "success": True,
                "new_version": "2.1",
                "dependent_proposals_updated": 0
            }
        """
        # Get artifact
        artifact = await session.get(Artifact, proposal.artifact_id)
        if not artifact:
            raise ValueError(f"Artifact {proposal.artifact_id} not found")

        # TODO: Implement smart merging of proposed_changes into artifact.content
        # For now, we just increment version and record the proposal was applied

        # This will be called from the approve endpoint we already created
        # The endpoint handles version creation

        return {
            "success": True,
            "new_version": artifact.version,
            "dependent_proposals_updated": 0
        }

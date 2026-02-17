"""
AI Impact Analyzer - Analyzes how new information affects product artifacts.

This service uses AI to determine:
1. Which artifacts are affected by new information (Jira issue, meeting, etc.)
2. What kind of changes are needed
3. Severity and impact of changes
4. Cross-artifact dependencies

Example:
    Input: "PROJ-456: Add OAuth login to mobile app"

    Analysis:
    - PRD: High impact (new feature requirement)
    - Tech Spec: High impact (new authentication architecture)
    - UX Designs: Medium impact (new login screen)
    - Timeline: Medium impact (+2 weeks to Sprint 5)
    - Test Plan: Low impact (new test cases)
"""
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.artifact import Artifact, ArtifactType
from app.models.project import Project
from app.models.change_proposal import ChangeSeverity, ChangeType
from app.services.settings_service import AIProviderSettingsService
from app.core.config import settings as app_settings

logger = logging.getLogger(__name__)


class ImpactAnalyzer:
    """AI-powered impact analysis for product development changes."""

    @staticmethod
    async def analyze_impact(
        session: AsyncSession,
        project: Project,
        trigger_type: str,
        trigger_id: str,
        trigger_content: str,
        user_id: int,
        organization_id: int
    ) -> Dict[str, Any]:
        """
        Analyze impact of new information on project artifacts.

        Args:
            session: Database session
            project: Project to analyze
            trigger_type: Type of trigger (jira_issue, zoom_meeting, etc.)
            trigger_id: External ID of trigger
            trigger_content: Content to analyze (issue description, transcript, etc.)
            user_id: User making the request
            organization_id: Organization context

        Returns:
            Dict with:
            {
                "affected_artifacts": [
                    {
                        "artifact_id": 123,
                        "artifact_name": "Mobile App PRD",
                        "artifact_type": "prd",
                        "severity": "high",
                        "change_type": "new_requirement",
                        "proposed_changes": {...},
                        "rationale": "OAuth is a new authentication method..."
                    }
                ],
                "timeline_impact": {
                    "estimated_delay": "2 weeks",
                    "affected_milestones": ["Sprint 5 Launch"]
                },
                "overall_severity": "high"
            }
        """
        # Get all artifacts for project
        query = select(Artifact).where(
            Artifact.project_id == project.id,
            Artifact.status != "archived"
        )
        result = await session.execute(query)
        artifacts = result.scalars().all()

        if not artifacts:
            return {
                "affected_artifacts": [],
                "timeline_impact": {},
                "overall_severity": "low"
            }

        # Get AI provider settings
        provider_settings = await AIProviderSettingsService.get_settings(
            session, user_id, organization_id
        )

        # Build AI prompt
        prompt = ImpactAnalyzer._build_impact_analysis_prompt(
            project=project,
            artifacts=artifacts,
            trigger_type=trigger_type,
            trigger_content=trigger_content
        )

        # Call AI to analyze impact
        ai_response = await ImpactAnalyzer._call_ai_for_analysis(
            prompt=prompt,
            provider_settings=provider_settings
        )

        # Parse AI response
        impact_analysis = ImpactAnalyzer._parse_ai_response(ai_response, artifacts)

        return impact_analysis

    @staticmethod
    def _build_impact_analysis_prompt(
        project: Project,
        artifacts: List[Artifact],
        trigger_type: str,
        trigger_content: str
    ) -> str:
        """Build prompt for AI impact analysis."""
        artifact_list = "\n".join([
            f"- {a.name} ({a.artifact_type}): v{a.version}, status={a.status}"
            for a in artifacts
        ])

        prompt = f"""You are analyzing the impact of new information on a product development project.

**Project:** {project.name}
**Current Stage:** {project.current_stage}
**Status:** {project.status}

**Existing Artifacts:**
{artifact_list}

**New Information Source:** {trigger_type}
**Content:**
{trigger_content}

**Task:** Analyze how this new information affects each artifact. For each affected artifact, determine:

1. **Severity** (low, medium, high, critical):
   - low: Minor clarification or non-functional change
   - medium: Moderate change that affects some sections
   - high: Significant change affecting core functionality/scope
   - critical: Major architectural or scope change

2. **Change Type**:
   - new_requirement: Adding new feature/requirement
   - update_requirement: Modifying existing requirement
   - remove_requirement: Removing requirement
   - timeline_change: Changing timeline/dates
   - scope_change: Changing project scope
   - technical_change: Technical architecture change
   - design_change: UX/design change
   - content_update: General content update
   - clarification: Adding clarification

3. **Proposed Changes:** What specifically needs to be updated

4. **Rationale:** Why this artifact is affected

**Output Format (JSON):**
```json
{{
  "affected_artifacts": [
    {{
      "artifact_name": "PRD",
      "artifact_type": "prd",
      "severity": "high",
      "change_type": "new_requirement",
      "rationale": "OAuth is a new authentication method that needs to be documented in the PRD",
      "proposed_sections": [
        {{
          "section": "Features",
          "action": "add",
          "content": "OAuth Login - Allow users to login with Google/GitHub",
          "position": "after:Social Login"
        }}
      ]
    }}
  ],
  "timeline_impact": {{
    "estimated_delay": "2 weeks",
    "delay_reason": "OAuth implementation + security review",
    "affected_milestones": ["Sprint 5 Launch"]
  }},
  "overall_severity": "high"
}}
```

Analyze and respond with JSON only."""

        return prompt

    @staticmethod
    async def _call_ai_for_analysis(
        prompt: str,
        provider_settings: Any
    ) -> str:
        """Call AI provider to analyze impact."""
        # Get API key based on provider
        if provider_settings.llm_provider == "anthropic":
            import anthropic
            api_key = provider_settings.anthropic_api_key or app_settings.DEFAULT_ANTHROPIC_API_KEY

            if not api_key:
                logger.warning("No Anthropic API key available, using fallback analysis")
                return ImpactAnalyzer._fallback_analysis()

            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4000,
                temperature=0.3,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            return response.content[0].text

        elif provider_settings.llm_provider == "openai":
            from openai import OpenAI
            api_key = provider_settings.openai_api_key or app_settings.DEFAULT_OPENAI_API_KEY

            if not api_key:
                logger.warning("No OpenAI API key available, using fallback analysis")
                return ImpactAnalyzer._fallback_analysis()

            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model="gpt-4o",
                temperature=0.3,
                max_tokens=4000,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            return response.choices[0].message.content

        else:
            logger.warning(f"Unknown LLM provider: {provider_settings.llm_provider}, using fallback")
            return ImpactAnalyzer._fallback_analysis()

    @staticmethod
    def _fallback_analysis() -> str:
        """Fallback analysis when AI is not available."""
        return """{
  "affected_artifacts": [],
  "timeline_impact": {},
  "overall_severity": "low",
  "note": "AI analysis not available - manual review required"
}"""

    @staticmethod
    def _parse_ai_response(ai_response: str, artifacts: List[Artifact]) -> Dict[str, Any]:
        """Parse AI response into structured impact analysis."""
        import json
        import re

        # Extract JSON from response (AI might wrap it in markdown)
        json_match = re.search(r'```json\s*(.*?)\s*```', ai_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find JSON object directly
            json_match = re.search(r'\{.*\}', ai_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                logger.error("Could not extract JSON from AI response")
                return {
                    "affected_artifacts": [],
                    "timeline_impact": {},
                    "overall_severity": "low"
                }

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response JSON: {e}")
            return {
                "affected_artifacts": [],
                "timeline_impact": {},
                "overall_severity": "low"
            }

        # Match artifact names to actual artifact IDs
        artifact_map = {a.name.lower(): a for a in artifacts}
        artifact_type_map = {a.artifact_type: a for a in artifacts}

        for affected in parsed.get("affected_artifacts", []):
            # Try to match by name first
            artifact_name = affected.get("artifact_name", "").lower()
            artifact = artifact_map.get(artifact_name)

            # If not found by name, try by type
            if not artifact:
                artifact_type = affected.get("artifact_type")
                artifact = artifact_type_map.get(artifact_type)

            if artifact:
                affected["artifact_id"] = artifact.id
                affected["artifact_name"] = artifact.name
            else:
                logger.warning(f"Could not find artifact: {affected.get('artifact_name')}")

        return parsed

    @staticmethod
    def determine_change_severity(severity_str: str) -> ChangeSeverity:
        """Convert severity string to enum."""
        severity_map = {
            "low": ChangeSeverity.LOW,
            "medium": ChangeSeverity.MEDIUM,
            "high": ChangeSeverity.HIGH,
            "critical": ChangeSeverity.CRITICAL
        }
        return severity_map.get(severity_str.lower(), ChangeSeverity.MEDIUM)

    @staticmethod
    def determine_change_type(type_str: str) -> ChangeType:
        """Convert change type string to enum."""
        type_map = {
            "new_requirement": ChangeType.NEW_REQUIREMENT,
            "update_requirement": ChangeType.UPDATE_REQUIREMENT,
            "remove_requirement": ChangeType.REMOVE_REQUIREMENT,
            "timeline_change": ChangeType.TIMELINE_CHANGE,
            "scope_change": ChangeType.SCOPE_CHANGE,
            "technical_change": ChangeType.TECHNICAL_CHANGE,
            "design_change": ChangeType.DESIGN_CHANGE,
            "content_update": ChangeType.CONTENT_UPDATE,
            "clarification": ChangeType.CLARIFICATION
        }
        return type_map.get(type_str.lower(), ChangeType.CONTENT_UPDATE)

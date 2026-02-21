"""
Transcript processing service.

Uses AI to extract structured information from meeting transcripts:
- Summary
- Key discussion points
- Action items with assignees
- Decisions made
"""
import json
import httpx
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.skill import MeetingImport
from app.models.node import Node
from app.models.canvas import Canvas
from app.services.settings_service import SettingsService


class TranscriptProcessingError(Exception):
    """Raised when transcript processing fails."""
    pass


EXTRACTION_PROMPT = """You are analyzing a meeting transcript. Extract the following information:

1. **Summary**: A 2-3 sentence overview of what was discussed.

2. **Key Points**: The main topics and important points discussed (list of strings).

3. **Action Items**: Tasks that were assigned or need to be done. For each:
   - task: What needs to be done
   - assignee: Who is responsible (or "unassigned" if unclear)
   - due_date: When it's due (or null if not specified)
   - priority: high/medium/low based on urgency discussed

4. **Decisions**: Important decisions that were made (list of strings).

Return your response as valid JSON in this exact format:
{
  "summary": "string",
  "key_points": ["point 1", "point 2", ...],
  "action_items": [
    {"task": "string", "assignee": "string", "due_date": "string or null", "priority": "string"}
  ],
  "decisions": ["decision 1", "decision 2", ...]
}

Meeting Topic: {topic}
Participants: {participants}

Transcript:
{transcript}
"""

MEETING_NOTES_TEMPLATE = """# {topic}

**Date:** {date}
**Duration:** {duration} minutes
**Participants:** {participants}

## Summary
{summary}

## Key Discussion Points
{key_points}

## Action Items
{action_items}

## Decisions Made
{decisions}

---
*Notes automatically generated from Zoom recording*
"""


class TranscriptProcessor:
    """Process meeting transcripts using AI."""

    async def _call_anthropic(
        self,
        api_key: str,
        model: str,
        prompt: str,
    ) -> str:
        """Call Anthropic API."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )

            if response.status_code != 200:
                raise TranscriptProcessingError(
                    f"Anthropic API error: {response.status_code} - {response.text}"
                )

            result = response.json()
            return result["content"][0]["text"]

    async def _call_openai(
        self,
        api_key: str,
        model: str,
        prompt: str,
    ) -> str:
        """Call OpenAI API."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4096,
                    "response_format": {"type": "json_object"},
                },
            )

            if response.status_code != 200:
                raise TranscriptProcessingError(
                    f"OpenAI API error: {response.status_code} - {response.text}"
                )

            result = response.json()
            return result["choices"][0]["message"]["content"]

    async def extract_from_transcript(
        self,
        session: AsyncSession,
        user_id: int,
        transcript: str,
        topic: str = "Meeting",
        participants: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Extract structured information from a transcript using AI.

        Returns:
            Dict with summary, key_points, action_items, decisions
        """
        if not transcript:
            return {
                "summary": "No transcript available",
                "key_points": [],
                "action_items": [],
                "decisions": [],
            }

        # Get AI settings
        settings = await SettingsService.get_effective_settings(session, user_id)

        provider = settings.get("preferred_llm_provider", "anthropic")
        model = settings.get("preferred_llm_model", "claude-sonnet-4-20250514")

        # Build prompt
        prompt = EXTRACTION_PROMPT.format(
            topic=topic,
            participants=", ".join(participants or ["Unknown"]),
            transcript=transcript[:50000],  # Limit transcript length
        )

        # Call appropriate API
        if provider == "anthropic":
            api_key = settings.get("anthropic_api_key")
            if not api_key:
                raise TranscriptProcessingError("Anthropic API key not configured")
            response_text = await self._call_anthropic(api_key, model, prompt)
        else:
            api_key = settings.get("openai_api_key")
            if not api_key:
                raise TranscriptProcessingError("OpenAI API key not configured")
            response_text = await self._call_openai(api_key, model, prompt)

        # Parse JSON response
        try:
            # Find JSON in response (handle markdown code blocks)
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end]
            elif "```" in response_text:
                json_start = response_text.find("```") + 3
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end]

            extracted = json.loads(response_text.strip())
            return extracted

        except json.JSONDecodeError as e:
            raise TranscriptProcessingError(f"Failed to parse AI response: {e}")

    def format_meeting_notes(
        self,
        topic: str,
        date: datetime,
        duration: int,
        participants: List[str],
        extraction: Dict[str, Any],
    ) -> str:
        """Format extracted information as markdown meeting notes."""
        # Format key points
        key_points_md = "\n".join(
            f"- {point}" for point in extraction.get("key_points", [])
        ) or "- No key points identified"

        # Format action items
        action_items = extraction.get("action_items", [])
        if action_items:
            action_items_md = "\n".join(
                f"- [ ] **{item.get('task')}** - @{item.get('assignee', 'unassigned')}"
                + (f" (Due: {item.get('due_date')})" if item.get("due_date") else "")
                for item in action_items
            )
        else:
            action_items_md = "- No action items identified"

        # Format decisions
        decisions_md = "\n".join(
            f"- {decision}" for decision in extraction.get("decisions", [])
        ) or "- No decisions recorded"

        return MEETING_NOTES_TEMPLATE.format(
            topic=topic,
            date=date.strftime("%B %d, %Y") if date else "Unknown",
            duration=duration or "Unknown",
            participants=", ".join(participants) if participants else "Unknown",
            summary=extraction.get("summary", "No summary available"),
            key_points=key_points_md,
            action_items=action_items_md,
            decisions=decisions_md,
        )


class MeetingImportProcessor:
    """Process imported meetings end-to-end."""

    def __init__(self):
        self.transcript_processor = TranscriptProcessor()

    async def process_meeting_import(
        self,
        session: AsyncSession,
        meeting_import_id: int,
        user_id: int,
    ) -> MeetingImport:
        """
        Process a meeting import: extract info and create nodes.

        Args:
            session: Database session
            meeting_import_id: ID of MeetingImport record
            user_id: User processing the import (for AI settings)

        Returns:
            Updated MeetingImport with processed data
        """
        # Get the import record
        result = await session.execute(
            select(MeetingImport).where(MeetingImport.id == meeting_import_id)
        )
        meeting_import = result.scalar_one_or_none()

        if not meeting_import:
            raise TranscriptProcessingError(f"Meeting import {meeting_import_id} not found")

        if not meeting_import.transcript_raw:
            meeting_import.status = "error"
            meeting_import.processing_error = "No transcript available"
            await session.commit()
            return meeting_import

        try:
            meeting_import.status = "processing"
            await session.commit()

            # Extract information from transcript
            extraction = await self.transcript_processor.extract_from_transcript(
                session,
                user_id,
                meeting_import.transcript_raw,
                topic=meeting_import.meeting_topic or "Meeting",
                participants=meeting_import.meeting_participants,
            )

            # Update import record with extracted data
            meeting_import.summary = extraction.get("summary")
            meeting_import.key_points = extraction.get("key_points", [])
            meeting_import.action_items = extraction.get("action_items", [])
            meeting_import.decisions = extraction.get("decisions", [])

            # Create doc node with meeting notes if canvas specified
            if meeting_import.canvas_id:
                notes_content = self.transcript_processor.format_meeting_notes(
                    topic=meeting_import.meeting_topic or "Meeting",
                    date=meeting_import.meeting_start_time,
                    duration=meeting_import.meeting_duration_minutes,
                    participants=meeting_import.meeting_participants,
                    extraction=extraction,
                )

                doc_node = Node(
                    canvas_id=meeting_import.canvas_id,
                    name=f"📝 {meeting_import.meeting_topic or 'Meeting Notes'}",
                    node_type="doc",
                    content=notes_content,
                    position_x=100,
                    position_y=100,
                    node_metadata={
                        "source": "zoom",
                        "meeting_id": meeting_import.external_meeting_id,
                        "meeting_date": meeting_import.meeting_start_time.isoformat()
                        if meeting_import.meeting_start_time else None,
                    },
                )
                session.add(doc_node)
                await session.flush()

                meeting_import.doc_node_id = doc_node.id
                created_node_ids = [doc_node.id]

                # Optionally create problem nodes for action items
                y_offset = 350
                for i, action in enumerate(extraction.get("action_items", [])[:5]):  # Limit to 5
                    action_node = Node(
                        canvas_id=meeting_import.canvas_id,
                        name=f"📋 {action.get('task', 'Action Item')[:50]}",
                        node_type="problem",
                        content=f"**Assignee:** {action.get('assignee', 'Unassigned')}\n\n"
                                f"**Due:** {action.get('due_date', 'Not set')}\n\n"
                                f"**Priority:** {action.get('priority', 'medium')}\n\n"
                                f"**Task:** {action.get('task')}",
                        position_x=100 + (i % 2) * 350,
                        position_y=y_offset + (i // 2) * 250,
                        node_metadata={
                            "source": "zoom_action_item",
                            "assignee": action.get("assignee"),
                            "due_date": action.get("due_date"),
                            "priority": action.get("priority"),
                        },
                    )
                    session.add(action_node)
                    await session.flush()
                    created_node_ids.append(action_node.id)

                meeting_import.created_node_ids = created_node_ids

            meeting_import.status = "completed"
            meeting_import.processed_at = datetime.utcnow()
            meeting_import.processing_error = None

            await session.commit()
            await session.refresh(meeting_import)

            return meeting_import

        except Exception as e:
            meeting_import.status = "error"
            meeting_import.processing_error = str(e)
            await session.commit()
            raise TranscriptProcessingError(f"Processing failed: {e}")


# Singleton instances
transcript_processor = TranscriptProcessor()
meeting_import_processor = MeetingImportProcessor()

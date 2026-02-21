"""
Input Processor - Pipeline for handling integration events.

Architecture:
- InputProcessor: Orchestrates the processing pipeline
- Jobs: Individual processing steps (transcript, notes, tasks, linking)
- IntegrationHandler: Source-specific handlers (Zoom, Slack, Calendar)

Flow:
1. Webhook/event received
2. InputEvent created
3. InputProcessor runs job pipeline
4. Results stored (tasks, nodes, etc.)
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Type
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.task import Task, InputEvent, TaskStatus, TaskPriority, TaskSource
from app.models.node import Node
from app.models.canvas import Canvas
from app.models.skill import Skill, MeetingImport

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class JobContext:
    """Shared context passed between jobs in the pipeline."""
    session: AsyncSession
    user_id: int
    organization_id: Optional[int]
    input_event: InputEvent
    integration: Optional[Skill] = None

    # Data passed between jobs
    raw_content: str = ""
    transcript: str = ""
    transcript_segments: List[Dict] = field(default_factory=list)
    summary: str = ""
    key_points: List[str] = field(default_factory=list)
    extracted_tasks: List[Dict] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)

    # Created entities
    created_nodes: List[Node] = field(default_factory=list)
    created_tasks: List[Task] = field(default_factory=list)
    doc_node: Optional[Node] = None

    # Target canvas
    canvas_id: Optional[int] = None
    canvas: Optional[Canvas] = None

    # Metadata from source
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JobResult:
    """Result from a job execution."""
    status: JobStatus
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class Job(ABC):
    """Base class for processing jobs."""

    name: str = "base_job"
    description: str = "Base job"

    @abstractmethod
    async def execute(self, context: JobContext) -> JobResult:
        """Execute the job and return result."""
        pass

    def should_run(self, context: JobContext) -> bool:
        """Check if this job should run given the context."""
        return True


class TranscriptExtractionJob(Job):
    """Extract and parse transcript from raw content."""

    name = "transcript_extraction"
    description = "Extract transcript from meeting recording"

    async def execute(self, context: JobContext) -> JobResult:
        if not context.raw_content:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="No raw content to process"
            )

        # Parse VTT or plain text transcript
        transcript = context.raw_content
        segments = []

        # Simple VTT parsing
        if "WEBVTT" in transcript[:50]:
            lines = transcript.split("\n")
            current_segment = {}

            for line in lines:
                line = line.strip()
                if "-->" in line:
                    # Timestamp line
                    times = line.split("-->")
                    current_segment["start"] = times[0].strip()
                    current_segment["end"] = times[1].strip().split()[0]
                elif line and not line.startswith("WEBVTT") and not line.isdigit():
                    # Speaker or text line
                    if ":" in line and len(line.split(":")[0]) < 30:
                        parts = line.split(":", 1)
                        current_segment["speaker"] = parts[0].strip()
                        current_segment["text"] = parts[1].strip() if len(parts) > 1 else ""
                    else:
                        current_segment["text"] = current_segment.get("text", "") + " " + line

                    if current_segment.get("text"):
                        segments.append(current_segment.copy())
                        current_segment = {}

        context.transcript = transcript
        context.transcript_segments = segments

        return JobResult(
            status=JobStatus.COMPLETED,
            message=f"Extracted {len(segments)} transcript segments",
            data={"segment_count": len(segments)}
        )


class MeetingNotesJob(Job):
    """Generate meeting notes from transcript using AI."""

    name = "meeting_notes"
    description = "Generate structured meeting notes from transcript"

    async def execute(self, context: JobContext) -> JobResult:
        if not context.transcript:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="No transcript available"
            )

        from app.services.transcript_processor import transcript_processor

        try:
            extraction = await transcript_processor.extract_from_transcript(
                context.session,
                context.user_id,
                context.transcript,
                topic=context.metadata.get("topic", "Meeting"),
                participants=context.metadata.get("participants", []),
            )

            context.summary = extraction.get("summary", "")
            context.key_points = extraction.get("key_points", [])
            context.extracted_tasks = extraction.get("action_items", [])
            context.decisions = extraction.get("decisions", [])

            return JobResult(
                status=JobStatus.COMPLETED,
                message="Generated meeting notes",
                data={
                    "summary_length": len(context.summary),
                    "key_points": len(context.key_points),
                    "tasks": len(context.extracted_tasks),
                    "decisions": len(context.decisions),
                }
            )

        except Exception as e:
            logger.error(f"Meeting notes generation failed: {e}")
            return JobResult(
                status=JobStatus.FAILED,
                error=str(e)
            )


class TaskExtractionJob(Job):
    """Create Task entities from extracted action items."""

    name = "task_extraction"
    description = "Create tasks from extracted action items"

    async def execute(self, context: JobContext) -> JobResult:
        if not context.extracted_tasks:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="No action items to process"
            )

        created_tasks = []

        for item in context.extracted_tasks:
            # Parse due date if present
            due_date = None
            due_date_text = item.get("due_date")
            if due_date_text:
                # TODO: Use dateparser for natural language dates
                pass

            # Determine priority
            priority = item.get("priority", "medium")
            if priority not in [p.value for p in TaskPriority]:
                priority = TaskPriority.MEDIUM.value

            task = Task(
                organization_id=context.organization_id,
                user_id=context.user_id,
                title=item.get("task", "Untitled Task")[:500],
                description=item.get("description"),
                assignee_name=item.get("assignee"),
                assignee_email=item.get("assignee_email"),
                due_date=due_date,
                due_date_text=due_date_text,
                status=TaskStatus.PENDING.value,
                priority=priority,
                source=TaskSource.ZOOM.value,
                source_id=context.metadata.get("meeting_id"),
                context=item.get("context"),
                canvas_id=context.canvas_id,
                metadata={
                    "extracted_from": context.input_event.event_type,
                    "extraction_date": datetime.utcnow().isoformat(),
                },
            )

            context.session.add(task)
            created_tasks.append(task)

        await context.session.flush()  # Get IDs

        context.created_tasks = created_tasks

        return JobResult(
            status=JobStatus.COMPLETED,
            message=f"Created {len(created_tasks)} tasks",
            data={"task_ids": [t.id for t in created_tasks]}
        )


class NodeCreationJob(Job):
    """Create nodes on canvas from processed content."""

    name = "node_creation"
    description = "Create canvas nodes for meeting notes and tasks"

    def should_run(self, context: JobContext) -> bool:
        return context.canvas_id is not None

    async def execute(self, context: JobContext) -> JobResult:
        if not context.canvas_id:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="No target canvas specified"
            )

        # Get canvas
        result = await context.session.execute(
            select(Canvas).where(Canvas.id == context.canvas_id)
        )
        context.canvas = result.scalar_one_or_none()

        if not context.canvas:
            return JobResult(
                status=JobStatus.FAILED,
                error=f"Canvas {context.canvas_id} not found"
            )

        from app.services.transcript_processor import transcript_processor

        created_nodes = []

        # Create doc node with meeting notes
        if context.summary:
            notes_content = transcript_processor.format_meeting_notes(
                topic=context.metadata.get("topic", "Meeting"),
                date=context.metadata.get("start_time"),
                duration=context.metadata.get("duration"),
                participants=context.metadata.get("participants", []),
                extraction={
                    "summary": context.summary,
                    "key_points": context.key_points,
                    "action_items": context.extracted_tasks,
                    "decisions": context.decisions,
                },
            )

            doc_node = Node(
                canvas_id=context.canvas_id,
                name=f"📝 {context.metadata.get('topic', 'Meeting Notes')}",
                node_type="doc",
                content=notes_content,
                position_x=100,
                position_y=100,
                node_metadata={
                    "source": "zoom",
                    "meeting_id": context.metadata.get("meeting_id"),
                    "input_event_id": context.input_event.id,
                },
            )
            context.session.add(doc_node)
            await context.session.flush()

            context.doc_node = doc_node
            created_nodes.append(doc_node)

        context.created_nodes = created_nodes

        return JobResult(
            status=JobStatus.COMPLETED,
            message=f"Created {len(created_nodes)} nodes",
            data={"node_ids": [n.id for n in created_nodes]}
        )


class NodeLinkingJob(Job):
    """Link tasks to relevant nodes on the canvas."""

    name = "node_linking"
    description = "Link extracted tasks to related canvas nodes"

    def should_run(self, context: JobContext) -> bool:
        return bool(context.created_tasks and context.canvas_id)

    async def execute(self, context: JobContext) -> JobResult:
        if not context.created_tasks:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="No tasks to link"
            )

        # Get existing nodes on canvas
        result = await context.session.execute(
            select(Node).where(Node.canvas_id == context.canvas_id)
        )
        canvas_nodes = list(result.scalars().all())

        if not canvas_nodes:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="No nodes on canvas to link to"
            )

        # Simple keyword matching for now
        # TODO: Use embeddings for semantic matching
        linked_count = 0

        for task in context.created_tasks:
            task_words = set(task.title.lower().split())

            for node in canvas_nodes:
                node_words = set(node.name.lower().split())
                if node.content:
                    node_words.update(node.content.lower().split()[:100])

                # Check for significant overlap
                overlap = task_words & node_words
                if len(overlap) >= 2:  # At least 2 common words
                    task.linked_nodes.append(node)
                    linked_count += 1

            # Always link to doc node if created
            if context.doc_node and context.doc_node not in task.linked_nodes:
                task.linked_nodes.append(context.doc_node)
                linked_count += 1

        return JobResult(
            status=JobStatus.COMPLETED,
            message=f"Created {linked_count} task-node links",
            data={"links_created": linked_count}
        )


class InputProcessor:
    """
    Orchestrates the processing pipeline for integration events.

    Usage:
        processor = InputProcessor()
        processor.register_jobs([
            TranscriptExtractionJob(),
            MeetingNotesJob(),
            TaskExtractionJob(),
            NodeCreationJob(),
            NodeLinkingJob(),
        ])
        await processor.process(context)
    """

    def __init__(self):
        self.jobs: List[Job] = []

    def register_jobs(self, jobs: List[Job]) -> None:
        """Register jobs to run in the pipeline."""
        self.jobs = jobs

    def add_job(self, job: Job) -> None:
        """Add a job to the pipeline."""
        self.jobs.append(job)

    async def process(self, context: JobContext) -> Dict[str, Any]:
        """
        Run the processing pipeline.

        Returns a summary of job results.
        """
        results = {}
        context.input_event.status = "processing"
        context.input_event.processing_started_at = datetime.utcnow()

        try:
            for job in self.jobs:
                if not job.should_run(context):
                    results[job.name] = JobResult(
                        status=JobStatus.SKIPPED,
                        message="Condition not met"
                    )
                    continue

                logger.info(f"Running job: {job.name}")

                try:
                    result = await job.execute(context)
                    results[job.name] = result

                    if result.status == JobStatus.FAILED:
                        logger.error(f"Job {job.name} failed: {result.error}")
                        # Continue with other jobs unless critical

                except Exception as e:
                    logger.exception(f"Job {job.name} raised exception")
                    results[job.name] = JobResult(
                        status=JobStatus.FAILED,
                        error=str(e)
                    )

            # Update input event
            context.input_event.status = "completed"
            context.input_event.processing_completed_at = datetime.utcnow()
            context.input_event.created_task_ids = [t.id for t in context.created_tasks]
            context.input_event.created_node_ids = [n.id for n in context.created_nodes]
            context.input_event.results = {
                name: {"status": r.status.value, "message": r.message}
                for name, r in results.items()
            }

            await context.session.commit()

        except Exception as e:
            logger.exception("Pipeline processing failed")
            context.input_event.status = "failed"
            context.input_event.processing_error = str(e)
            await context.session.commit()
            raise

        return {
            "status": "completed",
            "jobs": {name: r.status.value for name, r in results.items()},
            "tasks_created": len(context.created_tasks),
            "nodes_created": len(context.created_nodes),
        }


# Pre-configured pipelines
def create_zoom_pipeline() -> InputProcessor:
    """Create pipeline for processing Zoom meetings."""
    processor = InputProcessor()
    processor.register_jobs([
        TranscriptExtractionJob(),
        MeetingNotesJob(),
        TaskExtractionJob(),
        NodeCreationJob(),
        NodeLinkingJob(),
    ])
    return processor


def create_slack_pipeline() -> InputProcessor:
    """Create pipeline for processing Slack messages."""
    processor = InputProcessor()
    processor.register_jobs([
        TaskExtractionJob(),
        NodeLinkingJob(),
    ])
    return processor

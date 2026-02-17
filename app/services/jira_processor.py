"""
Jira Input Processor Jobs.

Handles bidirectional sync between Jira issues and internal Tasks.
Supports:
- Webhook events (issue created, updated, deleted)
- Bulk import from Jira
- Push internal tasks to Jira
"""
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.input_processor import (
    Job, JobContext, JobResult, JobStatus, InputProcessor
)
from app.models.task import Task, TaskStatus, TaskPriority, TaskSource
from app.models.project import Project
from app.services.jira import jira_service, jira_integration_service, JiraError

logger = logging.getLogger(__name__)


# Mapping between Jira and internal statuses
JIRA_STATUS_MAP = {
    "to do": TaskStatus.PENDING.value,
    "open": TaskStatus.PENDING.value,
    "in progress": TaskStatus.IN_PROGRESS.value,
    "in review": TaskStatus.IN_PROGRESS.value,
    "done": TaskStatus.COMPLETED.value,
    "closed": TaskStatus.COMPLETED.value,
    "resolved": TaskStatus.COMPLETED.value,
    "cancelled": TaskStatus.CANCELLED.value,
    "won't do": TaskStatus.CANCELLED.value,
}

JIRA_PRIORITY_MAP = {
    "highest": TaskPriority.URGENT.value,
    "high": TaskPriority.HIGH.value,
    "medium": TaskPriority.MEDIUM.value,
    "low": TaskPriority.LOW.value,
    "lowest": TaskPriority.LOW.value,
}

# Reverse mappings for pushing to Jira
TASK_TO_JIRA_PRIORITY = {
    TaskPriority.URGENT.value: "Highest",
    TaskPriority.HIGH.value: "High",
    TaskPriority.MEDIUM.value: "Medium",
    TaskPriority.LOW.value: "Low",
}


def extract_text_from_adf(adf_doc: Dict) -> str:
    """Extract plain text from Atlassian Document Format."""
    if not adf_doc or not isinstance(adf_doc, dict):
        return ""

    text_parts = []

    def extract_content(content: List) -> None:
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif "content" in item:
                    extract_content(item["content"])

    if "content" in adf_doc:
        extract_content(adf_doc["content"])

    return " ".join(text_parts).strip()


class JiraIssueSyncJob(Job):
    """Sync a Jira issue to an internal Task."""

    name = "jira_issue_sync"
    description = "Sync Jira issue to internal task"

    async def execute(self, context: JobContext) -> JobResult:
        issue_data = context.metadata.get("issue")
        if not issue_data:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="No issue data provided"
            )

        issue_key = issue_data.get("key")
        fields = issue_data.get("fields", {})

        # Extract issue details
        summary = fields.get("summary", "Untitled Issue")
        description_adf = fields.get("description")
        description = extract_text_from_adf(description_adf) if description_adf else ""

        # Map status
        jira_status = fields.get("status", {}).get("name", "").lower()
        status = JIRA_STATUS_MAP.get(jira_status, TaskStatus.PENDING.value)

        # Map priority
        jira_priority = fields.get("priority", {}).get("name", "").lower()
        priority = JIRA_PRIORITY_MAP.get(jira_priority, TaskPriority.MEDIUM.value)

        # Get assignee
        assignee = fields.get("assignee") or {}
        assignee_name = assignee.get("displayName")
        assignee_email = assignee.get("emailAddress")

        # Get due date
        due_date = None
        due_date_str = fields.get("duedate")
        if due_date_str:
            try:
                due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        # Get labels as tags
        tags = fields.get("labels", [])

        # Check if task already exists
        result = await context.session.execute(
            select(Task).where(
                Task.source == TaskSource.JIRA.value,
                Task.source_id == issue_key,
                Task.organization_id == context.organization_id,
            )
        )
        existing_task = result.scalar_one_or_none()

        if existing_task:
            # Update existing task
            existing_task.title = summary[:500]
            existing_task.description = description
            existing_task.status = status
            existing_task.priority = priority
            existing_task.assignee_name = assignee_name
            existing_task.assignee_email = assignee_email
            existing_task.due_date = due_date
            existing_task.tags = tags
            existing_task.metadata = {
                **existing_task.metadata,
                "jira_updated_at": datetime.utcnow().isoformat(),
                "jira_issue_type": fields.get("issuetype", {}).get("name"),
                "jira_project": fields.get("project", {}).get("key"),
            }
            context.created_tasks.append(existing_task)

            return JobResult(
                status=JobStatus.COMPLETED,
                message=f"Updated task for {issue_key}",
                data={"task_id": existing_task.id, "action": "updated"}
            )
        else:
            # Create new task
            task = Task(
                organization_id=context.organization_id,
                user_id=context.user_id,
                title=summary[:500],
                description=description,
                assignee_name=assignee_name,
                assignee_email=assignee_email,
                due_date=due_date,
                status=status,
                priority=priority,
                source=TaskSource.JIRA.value,
                source_id=issue_key,
                source_url=context.metadata.get("issue_url"),
                canvas_id=context.canvas_id,
                tags=tags,
                metadata={
                    "jira_issue_id": issue_data.get("id"),
                    "jira_issue_type": fields.get("issuetype", {}).get("name"),
                    "jira_project": fields.get("project", {}).get("key"),
                    "jira_created_at": fields.get("created"),
                    "jira_cloud_id": context.metadata.get("cloud_id"),
                    "synced_at": datetime.utcnow().isoformat(),
                },
            )

            context.session.add(task)
            await context.session.flush()
            context.created_tasks.append(task)

            return JobResult(
                status=JobStatus.COMPLETED,
                message=f"Created task for {issue_key}",
                data={"task_id": task.id, "action": "created"}
            )


class JiraIssueDeleteJob(Job):
    """Handle Jira issue deletion - mark internal task as cancelled."""

    name = "jira_issue_delete"
    description = "Handle Jira issue deletion"

    def should_run(self, context: JobContext) -> bool:
        return context.metadata.get("event_type") == "jira:issue_deleted"

    async def execute(self, context: JobContext) -> JobResult:
        issue_key = context.metadata.get("issue_key")
        if not issue_key:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="No issue key provided"
            )

        # Find and update existing task
        result = await context.session.execute(
            select(Task).where(
                Task.source == TaskSource.JIRA.value,
                Task.source_id == issue_key,
                Task.organization_id == context.organization_id,
            )
        )
        task = result.scalar_one_or_none()

        if task:
            task.status = TaskStatus.CANCELLED.value
            task.metadata = {
                **task.metadata,
                "jira_deleted_at": datetime.utcnow().isoformat(),
            }

            return JobResult(
                status=JobStatus.COMPLETED,
                message=f"Marked task for {issue_key} as cancelled",
                data={"task_id": task.id}
            )

        return JobResult(
            status=JobStatus.SKIPPED,
            message=f"No task found for {issue_key}"
        )


class JiraBulkImportJob(Job):
    """Import multiple issues from Jira using JQL query."""

    name = "jira_bulk_import"
    description = "Bulk import Jira issues using JQL"

    async def execute(self, context: JobContext) -> JobResult:
        jql = context.metadata.get("jql")
        if not jql:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="No JQL query provided"
            )

        if not context.integration:
            return JobResult(
                status=JobStatus.FAILED,
                error="No Jira integration found"
            )

        cloud_id = context.integration.provider_data.get("cloud_id")
        if not cloud_id:
            return JobResult(
                status=JobStatus.FAILED,
                error="No Jira cloud ID configured"
            )

        try:
            access_token = await jira_integration_service.get_or_refresh_token(
                context.session, context.integration
            )

            # Fetch issues
            start_at = 0
            max_results = 50
            total_imported = 0
            total_updated = 0

            while True:
                search_result = await jira_service.search_issues(
                    access_token, cloud_id, jql, start_at, max_results
                )

                issues = search_result.get("issues", [])
                if not issues:
                    break

                for issue in issues:
                    # Process each issue
                    context.metadata["issue"] = issue
                    context.metadata["cloud_id"] = cloud_id

                    sync_job = JiraIssueSyncJob()
                    result = await sync_job.execute(context)

                    if result.status == JobStatus.COMPLETED:
                        if result.data.get("action") == "created":
                            total_imported += 1
                        else:
                            total_updated += 1

                # Check if more results
                total = search_result.get("total", 0)
                start_at += len(issues)
                if start_at >= total:
                    break

            return JobResult(
                status=JobStatus.COMPLETED,
                message=f"Imported {total_imported} new, updated {total_updated} existing tasks",
                data={
                    "imported": total_imported,
                    "updated": total_updated,
                    "total_processed": total_imported + total_updated,
                }
            )

        except JiraError as e:
            logger.error(f"Jira bulk import failed: {e}")
            return JobResult(
                status=JobStatus.FAILED,
                error=str(e)
            )


class PushTaskToJiraJob(Job):
    """Push an internal task to Jira as a new issue."""

    name = "push_task_to_jira"
    description = "Create Jira issue from internal task"

    async def execute(self, context: JobContext) -> JobResult:
        task_id = context.metadata.get("task_id")
        project_key = context.metadata.get("project_key")
        issue_type = context.metadata.get("issue_type", "Task")

        if not task_id or not project_key:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="Missing task_id or project_key"
            )

        if not context.integration:
            return JobResult(
                status=JobStatus.FAILED,
                error="No Jira integration found"
            )

        cloud_id = context.integration.provider_data.get("cloud_id")
        if not cloud_id:
            return JobResult(
                status=JobStatus.FAILED,
                error="No Jira cloud ID configured"
            )

        # Get the task
        result = await context.session.execute(
            select(Task).where(Task.id == task_id)
        )
        task = result.scalar_one_or_none()

        if not task:
            return JobResult(
                status=JobStatus.FAILED,
                error=f"Task {task_id} not found"
            )

        # Don't push if already from Jira
        if task.source == TaskSource.JIRA.value:
            return JobResult(
                status=JobStatus.SKIPPED,
                message="Task is already from Jira"
            )

        try:
            access_token = await jira_integration_service.get_or_refresh_token(
                context.session, context.integration
            )

            # Map priority
            jira_priority = TASK_TO_JIRA_PRIORITY.get(task.priority, "Medium")

            # Format due date
            due_date = None
            if task.due_date:
                due_date = task.due_date.strftime("%Y-%m-%d")

            # Create the issue
            issue = await jira_service.create_issue(
                access_token=access_token,
                cloud_id=cloud_id,
                project_key=project_key,
                issue_type=issue_type,
                summary=task.title,
                description=task.description,
                priority=jira_priority,
                due_date=due_date,
                labels=task.tags if task.tags else None,
            )

            issue_key = issue.get("key")
            issue_id = issue.get("id")

            # Update task with Jira info
            task.source_id = issue_key
            task.source = TaskSource.JIRA.value
            task.metadata = {
                **task.metadata,
                "jira_issue_id": issue_id,
                "jira_project": project_key,
                "jira_cloud_id": cloud_id,
                "pushed_at": datetime.utcnow().isoformat(),
            }

            return JobResult(
                status=JobStatus.COMPLETED,
                message=f"Created Jira issue {issue_key}",
                data={
                    "task_id": task.id,
                    "issue_key": issue_key,
                    "issue_id": issue_id,
                }
            )

        except JiraError as e:
            logger.error(f"Failed to push task to Jira: {e}")
            return JobResult(
                status=JobStatus.FAILED,
                error=str(e)
            )


class WorkflowOrchestratorJob(Job):
    """
    Triggers AI impact analysis on all projects linked to an affected canvas.

    When a Jira issue arrives:
    1. Find the task created/updated by JiraIssueSyncJob
    2. Get canvas linked to that task
    3. Find all projects on that canvas
    4. Call WorkflowOrchestrator to analyze impact on each project
    """
    name = "workflow_orchestrator"
    description = "Analyze Jira issue impact on product artifacts"

    def should_run(self, context: JobContext) -> bool:
        """Only run for issue_created and issue_updated events."""
        event_type = context.metadata.get("event_type", "")
        return event_type in ("jira:issue_created", "jira:issue_updated")

    async def execute(self, context: JobContext) -> JobResult:
        try:
            from app.services.workflow_orchestrator import WorkflowOrchestrator

            # Get task created/updated by JiraIssueSyncJob
            if not context.created_tasks:
                return JobResult(
                    status=JobStatus.SKIPPED,
                    message="No tasks to analyze (no linked canvas)"
                )

            task = context.created_tasks[0]

            # Need a canvas to find projects
            if not task.canvas_id:
                return JobResult(
                    status=JobStatus.SKIPPED,
                    message=f"Task {task.source_id} has no canvas - skipping workflow analysis"
                )

            # Find all active projects on this canvas
            result = await context.session.execute(
                select(Project).where(
                    Project.canvas_id == task.canvas_id,
                    Project.status.in_(["planning", "active"])
                )
            )
            projects = result.scalars().all()

            if not projects:
                return JobResult(
                    status=JobStatus.SKIPPED,
                    message=f"No active projects on canvas {task.canvas_id}"
                )

            # Build trigger content from Jira issue
            issue = context.metadata.get("issue", {})
            fields = issue.get("fields", {})
            issue_key = issue.get("key", "")
            summary = fields.get("summary", "")
            description = extract_text_from_adf(fields.get("description")) if fields.get("description") else ""
            issue_type = fields.get("issuetype", {}).get("name", "Issue")
            priority = fields.get("priority", {}).get("name", "Medium")

            trigger_content = (
                f"Jira {issue_type}: {issue_key}\n"
                f"Summary: {summary}\n"
                f"Priority: {priority}\n"
                f"Description: {description}"
            ).strip()

            # Process each project
            total_proposals = 0
            for project in projects:
                result = await WorkflowOrchestrator.process_input_event(
                    session=context.session,
                    input_event=context.input_event,
                    project_id=project.id,
                    user_id=context.user_id,
                    organization_id=context.organization_id or project.organization_id,
                    trigger_content=trigger_content
                )
                total_proposals += result["proposals_created"]

            return JobResult(
                status=JobStatus.COMPLETED,
                message=f"Created {total_proposals} change proposals across {len(projects)} projects",
                data={"total_proposals": total_proposals, "projects_analyzed": len(projects)}
            )

        except Exception as e:
            logger.exception("WorkflowOrchestratorJob failed")
            return JobResult(
                status=JobStatus.FAILED,
                message="Failed to run workflow orchestration",
                error=str(e)
            )


# Pre-configured pipelines
def create_jira_webhook_pipeline() -> InputProcessor:
    """Create pipeline for processing Jira webhook events."""
    processor = InputProcessor()
    processor.register_jobs([
        JiraIssueSyncJob(),
        JiraIssueDeleteJob(),
        WorkflowOrchestratorJob(),  # Triggers AI impact analysis
    ])
    return processor


def create_jira_import_pipeline() -> InputProcessor:
    """Create pipeline for bulk importing from Jira."""
    processor = InputProcessor()
    processor.register_jobs([
        JiraBulkImportJob(),
    ])
    return processor


def create_jira_push_pipeline() -> InputProcessor:
    """Create pipeline for pushing tasks to Jira."""
    processor = InputProcessor()
    processor.register_jobs([
        PushTaskToJiraJob(),
    ])
    return processor

#!/usr/bin/env python3
"""
MCP Server for Merlin Canvas

Exposes canvas management, templates, tasks, and integrations via MCP protocol.
This allows Claude and other AI agents to:
- Manage canvas nodes and templates
- Create and manage tasks
- Sync with Jira and Zoom integrations
- Process meeting transcripts and action items

Run with: python mcp_server.py
"""

import asyncio
import json
from typing import Any, Optional, List
from datetime import datetime
import sys

# MCP SDK imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("MCP SDK not installed. Install with: pip install mcp")
    sys.exit(1)

# Database imports
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from app.core.database import async_session_maker
from app.models.template import NodeTemplateContext, TemplateScope, SYSTEM_DEFAULT_TEMPLATES
from app.models.task import Task, TaskStatus, TaskPriority, TaskSource, InputEvent
from app.models.integration import Integration, IntegrationProvider, MeetingImport
from app.models.node import Node
from app.services import template_service


# Create MCP server
server = Server("merlin-canvas")


# ============ Tool Definitions ============

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        # Template Tools
        Tool(
            name="list_templates",
            description="List all available node templates for the canvas",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string",
                        "description": "Filter by node type (objective, keyresult, metric, problem, doc)",
                    }
                },
            },
        ),
        Tool(
            name="get_template",
            description="Get a specific template by node type. Returns AI prompts and structure.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_type": {"type": "string", "description": "The node type"},
                    "subtype": {"type": "string", "description": "Optional subtype (e.g., 'prd')"},
                },
                "required": ["node_type"],
            },
        ),
        Tool(
            name="get_connection_rules",
            description="Get connection rules showing which node types can connect to which",
            inputSchema={"type": "object", "properties": {}},
        ),

        # Task Tools
        Tool(
            name="list_tasks",
            description="List tasks with optional filters. Returns tasks from meetings, Jira, or manual creation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                        "description": "Filter by task status",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                        "description": "Filter by priority",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["manual", "zoom", "slack", "jira", "ai_extracted"],
                        "description": "Filter by source",
                    },
                    "canvas_id": {
                        "type": "integer",
                        "description": "Filter by canvas ID",
                    },
                    "overdue": {
                        "type": "boolean",
                        "description": "Only show overdue tasks",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of tasks to return (default 50)",
                    },
                },
            },
        ),
        Tool(
            name="get_task",
            description="Get details of a specific task including linked nodes",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "The task ID"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="create_task",
            description="Create a new task. Can be linked to canvas nodes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "description": {"type": "string", "description": "Task description"},
                    "assignee_name": {"type": "string", "description": "Assignee name"},
                    "assignee_email": {"type": "string", "description": "Assignee email"},
                    "due_date": {"type": "string", "description": "Due date (ISO format)"},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                        "description": "Priority level",
                    },
                    "canvas_id": {"type": "integer", "description": "Canvas to associate with"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for the task",
                    },
                },
                "required": ["title"],
            },
        ),
        Tool(
            name="update_task",
            description="Update an existing task",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "The task ID"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                    "assignee_name": {"type": "string"},
                    "assignee_email": {"type": "string"},
                    "due_date": {"type": "string"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="link_task_to_node",
            description="Link a task to a canvas node",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "The task ID"},
                    "node_id": {"type": "integer", "description": "The node ID"},
                },
                "required": ["task_id", "node_id"],
            },
        ),
        Tool(
            name="get_task_stats",
            description="Get task statistics (total, by status, overdue count)",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {"type": "integer", "description": "Optional canvas filter"},
                },
            },
        ),

        # Jira Tools
        Tool(
            name="get_jira_status",
            description="Check Jira integration connection status for an organization",
            inputSchema={
                "type": "object",
                "properties": {
                    "organization_id": {"type": "integer", "description": "Organization ID"},
                },
                "required": ["organization_id"],
            },
        ),
        Tool(
            name="import_jira_issues",
            description="Import issues from Jira using a JQL query",
            inputSchema={
                "type": "object",
                "properties": {
                    "organization_id": {"type": "integer", "description": "Organization ID"},
                    "jql": {
                        "type": "string",
                        "description": "JQL query (e.g., 'project = PROJ AND status != Done')",
                    },
                    "canvas_id": {"type": "integer", "description": "Canvas to associate imported tasks"},
                },
                "required": ["organization_id", "jql"],
            },
        ),
        Tool(
            name="push_task_to_jira",
            description="Push an internal task to Jira as a new issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "The task ID to push"},
                    "project_key": {"type": "string", "description": "Jira project key (e.g., 'PROJ')"},
                    "issue_type": {
                        "type": "string",
                        "description": "Jira issue type (default: Task)",
                        "default": "Task",
                    },
                },
                "required": ["task_id", "project_key"],
            },
        ),

        # Zoom Tools
        Tool(
            name="get_zoom_status",
            description="Check Zoom integration connection status for an organization",
            inputSchema={
                "type": "object",
                "properties": {
                    "organization_id": {"type": "integer", "description": "Organization ID"},
                },
                "required": ["organization_id"],
            },
        ),
        Tool(
            name="list_zoom_recordings",
            description="List available Zoom recordings that can be imported",
            inputSchema={
                "type": "object",
                "properties": {
                    "organization_id": {"type": "integer", "description": "Organization ID"},
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look back (default 30)",
                        "default": 30,
                    },
                },
                "required": ["organization_id"],
            },
        ),
        Tool(
            name="list_meeting_imports",
            description="List imported meetings and their processing status",
            inputSchema={
                "type": "object",
                "properties": {
                    "organization_id": {"type": "integer", "description": "Organization ID"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "processing", "completed", "error"],
                        "description": "Filter by processing status",
                    },
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
                "required": ["organization_id"],
            },
        ),

        # Input Event Tools
        Tool(
            name="list_input_events",
            description="List webhook/integration events and their processing status",
            inputSchema={
                "type": "object",
                "properties": {
                    "organization_id": {"type": "integer", "description": "Organization ID"},
                    "source_type": {
                        "type": "string",
                        "enum": ["zoom", "jira", "slack"],
                        "description": "Filter by source",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "processing", "completed", "failed"],
                        "description": "Filter by status",
                    },
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
                "required": ["organization_id"],
            },
        ),
        Tool(
            name="get_input_event",
            description="Get details of a specific input event including results",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer", "description": "The input event ID"},
                },
                "required": ["event_id"],
            },
        ),
    ]


# ============ Tool Handler ============

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Route tool calls to handlers."""
    handlers = {
        # Templates
        "list_templates": handle_list_templates,
        "get_template": handle_get_template,
        "get_connection_rules": handle_get_connection_rules,
        # Tasks
        "list_tasks": handle_list_tasks,
        "get_task": handle_get_task,
        "create_task": handle_create_task,
        "update_task": handle_update_task,
        "link_task_to_node": handle_link_task_to_node,
        "get_task_stats": handle_get_task_stats,
        # Jira
        "get_jira_status": handle_get_jira_status,
        "import_jira_issues": handle_import_jira_issues,
        "push_task_to_jira": handle_push_task_to_jira,
        # Zoom
        "get_zoom_status": handle_get_zoom_status,
        "list_zoom_recordings": handle_list_zoom_recordings,
        "list_meeting_imports": handle_list_meeting_imports,
        # Input Events
        "list_input_events": handle_list_input_events,
        "get_input_event": handle_get_input_event,
    }

    handler = handlers.get(name)
    if handler:
        return await handler(arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ============ Template Handlers ============

async def handle_list_templates(args: dict) -> list[TextContent]:
    """List all templates."""
    node_type = args.get("node_type")

    async with async_session_maker() as session:
        query = select(NodeTemplateContext).where(
            NodeTemplateContext.scope == TemplateScope.SYSTEM.value,
            NodeTemplateContext.is_active == True,
        )

        if node_type:
            query = query.where(NodeTemplateContext.node_type == node_type)

        result = await session.execute(query)
        templates = result.scalars().all()

        if not templates:
            # Fall back to defaults
            templates_data = SYSTEM_DEFAULT_TEMPLATES
            if node_type:
                templates_data = [t for t in templates_data if t["node_type"] == node_type]
            output = [
                {
                    "node_type": t["node_type"],
                    "subtype": t.get("subtype"),
                    "name": t["name"],
                    "description": t.get("description"),
                }
                for t in templates_data
            ]
        else:
            output = [
                {
                    "id": t.id,
                    "node_type": t.node_type,
                    "subtype": t.subtype,
                    "name": t.name,
                    "description": t.description,
                }
                for t in templates
            ]

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_get_template(args: dict) -> list[TextContent]:
    """Get a specific template."""
    node_type = args["node_type"]
    subtype = args.get("subtype")

    async with async_session_maker() as session:
        template = await template_service.resolve_template(
            session, node_type=node_type, subtype=subtype
        )

        if not template:
            for t in SYSTEM_DEFAULT_TEMPLATES:
                if t["node_type"] == node_type and t.get("subtype") == subtype:
                    return [TextContent(type="text", text=json.dumps(t, indent=2))]
            return [TextContent(type="text", text=f"Template not found: {node_type}/{subtype}")]

        output = {
            "id": template.id,
            "node_type": template.node_type,
            "subtype": template.subtype,
            "name": template.name,
            "system_prompt": template.system_prompt,
            "generation_prompt": template.generation_prompt,
            "allowed_inputs": template.allowed_inputs or [],
            "allowed_outputs": template.allowed_outputs or [],
        }
        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_get_connection_rules(args: dict) -> list[TextContent]:
    """Get canvas connection rules."""
    rules = {
        "flow": "Objective → Key Result → Metric, Key Result → Problem → Doc",
        "rules": {
            "objective": {"can_connect_to": ["keyresult"]},
            "keyresult": {"can_connect_to": ["metric", "problem"]},
            "metric": {"can_connect_to": []},
            "problem": {"can_connect_to": ["doc"]},
            "doc": {"can_connect_to": ["doc", "agent", "integration"]},
        },
    }
    return [TextContent(type="text", text=json.dumps(rules, indent=2))]


# ============ Task Handlers ============

async def handle_list_tasks(args: dict) -> list[TextContent]:
    """List tasks with filters."""
    async with async_session_maker() as session:
        query = select(Task).options(selectinload(Task.linked_nodes))

        if args.get("status"):
            query = query.where(Task.status == args["status"])
        if args.get("priority"):
            query = query.where(Task.priority == args["priority"])
        if args.get("source"):
            query = query.where(Task.source == args["source"])
        if args.get("canvas_id"):
            query = query.where(Task.canvas_id == args["canvas_id"])
        if args.get("overdue"):
            query = query.where(
                Task.due_date < datetime.utcnow(),
                Task.status != TaskStatus.COMPLETED.value,
            )

        limit = args.get("limit", 50)
        query = query.order_by(Task.created_at.desc()).limit(limit)

        result = await session.execute(query)
        tasks = result.scalars().all()

        output = [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "source": t.source,
                "assignee_name": t.assignee_name,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "is_overdue": t.is_overdue,
                "canvas_id": t.canvas_id,
                "linked_node_count": len(t.linked_nodes),
                "created_at": t.created_at.isoformat(),
            }
            for t in tasks
        ]

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_get_task(args: dict) -> list[TextContent]:
    """Get task details."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Task)
            .options(selectinload(Task.linked_nodes))
            .where(Task.id == args["task_id"])
        )
        task = result.scalar_one_or_none()

        if not task:
            return [TextContent(type="text", text=f"Task {args['task_id']} not found")]

        output = {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "priority": task.priority,
            "source": task.source,
            "source_id": task.source_id,
            "source_url": task.source_url,
            "assignee_name": task.assignee_name,
            "assignee_email": task.assignee_email,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "due_date_text": task.due_date_text,
            "is_overdue": task.is_overdue,
            "context": task.context,
            "canvas_id": task.canvas_id,
            "tags": task.tags or [],
            "linked_nodes": [
                {"id": n.id, "name": n.name, "node_type": n.node_type}
                for n in task.linked_nodes
            ],
            "metadata": task.metadata,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_create_task(args: dict) -> list[TextContent]:
    """Create a new task."""
    async with async_session_maker() as session:
        due_date = None
        if args.get("due_date"):
            try:
                due_date = datetime.fromisoformat(args["due_date"].replace("Z", "+00:00"))
            except ValueError:
                pass

        task = Task(
            title=args["title"],
            description=args.get("description"),
            assignee_name=args.get("assignee_name"),
            assignee_email=args.get("assignee_email"),
            due_date=due_date,
            priority=args.get("priority", TaskPriority.MEDIUM.value),
            canvas_id=args.get("canvas_id"),
            tags=args.get("tags", []),
            source=TaskSource.MANUAL.value,
            status=TaskStatus.PENDING.value,
        )

        session.add(task)
        await session.commit()
        await session.refresh(task)

        output = {
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "created_at": task.created_at.isoformat(),
        }

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_update_task(args: dict) -> list[TextContent]:
    """Update an existing task."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Task).where(Task.id == args["task_id"])
        )
        task = result.scalar_one_or_none()

        if not task:
            return [TextContent(type="text", text=f"Task {args['task_id']} not found")]

        # Update fields
        for field in ["title", "description", "status", "priority", "assignee_name", "assignee_email"]:
            if field in args and args[field] is not None:
                setattr(task, field, args[field])

        if args.get("due_date"):
            try:
                task.due_date = datetime.fromisoformat(args["due_date"].replace("Z", "+00:00"))
            except ValueError:
                pass

        # Track completion
        if args.get("status") == TaskStatus.COMPLETED.value and not task.completed_at:
            task.completed_at = datetime.utcnow()

        await session.commit()

        return [TextContent(type="text", text=json.dumps({
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "updated": True,
        }, indent=2))]


async def handle_link_task_to_node(args: dict) -> list[TextContent]:
    """Link a task to a node."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Task)
            .options(selectinload(Task.linked_nodes))
            .where(Task.id == args["task_id"])
        )
        task = result.scalar_one_or_none()

        if not task:
            return [TextContent(type="text", text=f"Task {args['task_id']} not found")]

        node_result = await session.execute(
            select(Node).where(Node.id == args["node_id"])
        )
        node = node_result.scalar_one_or_none()

        if not node:
            return [TextContent(type="text", text=f"Node {args['node_id']} not found")]

        if node not in task.linked_nodes:
            task.linked_nodes.append(node)
            await session.commit()

        return [TextContent(type="text", text=json.dumps({
            "task_id": task.id,
            "node_id": node.id,
            "node_name": node.name,
            "linked": True,
        }, indent=2))]


async def handle_get_task_stats(args: dict) -> list[TextContent]:
    """Get task statistics."""
    async with async_session_maker() as session:
        base_filter = []
        if args.get("canvas_id"):
            base_filter.append(Task.canvas_id == args["canvas_id"])

        # Total
        total_result = await session.execute(
            select(func.count()).where(*base_filter) if base_filter else select(func.count(Task.id))
        )
        total = total_result.scalar() or 0

        # By status
        stats = {"total": total}
        for status in TaskStatus:
            status_result = await session.execute(
                select(func.count()).where(
                    *base_filter,
                    Task.status == status.value
                ) if base_filter else select(func.count()).where(Task.status == status.value)
            )
            stats[status.value] = status_result.scalar() or 0

        # Overdue
        overdue_result = await session.execute(
            select(func.count()).where(
                *base_filter,
                Task.due_date < datetime.utcnow(),
                Task.status != TaskStatus.COMPLETED.value,
            ) if base_filter else select(func.count()).where(
                Task.due_date < datetime.utcnow(),
                Task.status != TaskStatus.COMPLETED.value,
            )
        )
        stats["overdue"] = overdue_result.scalar() or 0

        return [TextContent(type="text", text=json.dumps(stats, indent=2))]


# ============ Jira Handlers ============

async def handle_get_jira_status(args: dict) -> list[TextContent]:
    """Get Jira integration status."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == args["organization_id"],
                Integration.provider == IntegrationProvider.JIRA.value,
            )
        )
        integration = result.scalar_one_or_none()

        if not integration:
            return [TextContent(type="text", text=json.dumps({
                "connected": False,
                "message": "No Jira integration found",
            }, indent=2))]

        output = {
            "connected": integration.is_connected,
            "site_name": integration.provider_data.get("site_name") if integration.provider_data else None,
            "cloud_id": integration.provider_data.get("cloud_id") if integration.provider_data else None,
            "status": integration.status.value if integration.status else None,
            "created_at": integration.created_at.isoformat() if integration.created_at else None,
        }

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_import_jira_issues(args: dict) -> list[TextContent]:
    """Import Jira issues - returns instructions for now."""
    return [TextContent(type="text", text=json.dumps({
        "message": "To import Jira issues, use the API endpoint:",
        "endpoint": "POST /api/v1/integrations/jira/import",
        "body": {
            "jql": args["jql"],
            "canvas_id": args.get("canvas_id"),
        },
        "note": "This endpoint requires authentication and runs asynchronously",
    }, indent=2))]


async def handle_push_task_to_jira(args: dict) -> list[TextContent]:
    """Push task to Jira - returns instructions."""
    return [TextContent(type="text", text=json.dumps({
        "message": "To push a task to Jira, use the API endpoint:",
        "endpoint": "POST /api/v1/integrations/jira/push",
        "body": {
            "task_id": args["task_id"],
            "project_key": args["project_key"],
            "issue_type": args.get("issue_type", "Task"),
        },
        "note": "This endpoint requires authentication",
    }, indent=2))]


# ============ Zoom Handlers ============

async def handle_get_zoom_status(args: dict) -> list[TextContent]:
    """Get Zoom integration status."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == args["organization_id"],
                Integration.provider == IntegrationProvider.ZOOM.value,
            )
        )
        integration = result.scalar_one_or_none()

        if not integration:
            return [TextContent(type="text", text=json.dumps({
                "connected": False,
                "message": "No Zoom integration found",
            }, indent=2))]

        output = {
            "connected": integration.is_connected,
            "status": integration.status.value if integration.status else None,
            "created_at": integration.created_at.isoformat() if integration.created_at else None,
        }

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_list_zoom_recordings(args: dict) -> list[TextContent]:
    """List Zoom recordings - returns instructions."""
    return [TextContent(type="text", text=json.dumps({
        "message": "To list Zoom recordings, use the API endpoint:",
        "endpoint": "GET /api/v1/integrations/zoom/recordings",
        "params": {"days": args.get("days", 30)},
        "note": "This endpoint requires authentication and fetches from Zoom API",
    }, indent=2))]


async def handle_list_meeting_imports(args: dict) -> list[TextContent]:
    """List imported meetings."""
    async with async_session_maker() as session:
        # Get integration first
        int_result = await session.execute(
            select(Integration).where(
                Integration.organization_id == args["organization_id"],
                Integration.provider == IntegrationProvider.ZOOM.value,
            )
        )
        integration = int_result.scalar_one_or_none()

        if not integration:
            return [TextContent(type="text", text=json.dumps({
                "meetings": [],
                "message": "No Zoom integration found",
            }, indent=2))]

        query = select(MeetingImport).where(
            MeetingImport.integration_id == integration.id
        )

        if args.get("status"):
            query = query.where(MeetingImport.status == args["status"])

        limit = args.get("limit", 20)
        query = query.order_by(MeetingImport.created_at.desc()).limit(limit)

        result = await session.execute(query)
        meetings = result.scalars().all()

        output = [
            {
                "id": m.id,
                "meeting_topic": m.meeting_topic,
                "meeting_start_time": m.meeting_start_time.isoformat() if m.meeting_start_time else None,
                "status": m.status,
                "summary": m.summary[:200] if m.summary else None,
                "action_items_count": len(m.action_items) if m.action_items else 0,
                "doc_node_id": m.doc_node_id,
                "created_at": m.created_at.isoformat(),
            }
            for m in meetings
        ]

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


# ============ Input Event Handlers ============

async def handle_list_input_events(args: dict) -> list[TextContent]:
    """List input events."""
    async with async_session_maker() as session:
        query = select(InputEvent).where(
            InputEvent.organization_id == args["organization_id"]
        )

        if args.get("source_type"):
            query = query.where(InputEvent.source_type == args["source_type"])
        if args.get("status"):
            query = query.where(InputEvent.status == args["status"])

        limit = args.get("limit", 20)
        query = query.order_by(InputEvent.created_at.desc()).limit(limit)

        result = await session.execute(query)
        events = result.scalars().all()

        output = [
            {
                "id": e.id,
                "source_type": e.source_type,
                "event_type": e.event_type,
                "external_id": e.external_id,
                "status": e.status,
                "created_task_count": len(e.created_task_ids) if e.created_task_ids else 0,
                "created_node_count": len(e.created_node_ids) if e.created_node_ids else 0,
                "processing_error": e.processing_error,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_get_input_event(args: dict) -> list[TextContent]:
    """Get input event details."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(InputEvent).where(InputEvent.id == args["event_id"])
        )
        event = result.scalar_one_or_none()

        if not event:
            return [TextContent(type="text", text=f"Event {args['event_id']} not found")]

        output = {
            "id": event.id,
            "source_type": event.source_type,
            "event_type": event.event_type,
            "external_id": event.external_id,
            "status": event.status,
            "payload": event.payload,
            "results": event.results,
            "created_task_ids": event.created_task_ids,
            "created_node_ids": event.created_node_ids,
            "processing_started_at": event.processing_started_at.isoformat() if event.processing_started_at else None,
            "processing_completed_at": event.processing_completed_at.isoformat() if event.processing_completed_at else None,
            "processing_error": event.processing_error,
            "retry_count": event.retry_count,
            "created_at": event.created_at.isoformat(),
        }

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


# ============ Main ============

async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())

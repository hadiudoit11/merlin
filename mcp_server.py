#!/usr/bin/env python3
"""
MCP Server for Typequest Canvas

Exposes canvas management, templates, tasks, and integrations via MCP protocol.
This allows Claude and other AI agents to:
- Manage canvas nodes and templates
- Create and manage tasks
- Sync with Jira and Zoom integrations
- Process meeting transcripts and action items

Run with: python mcp_server.py

Environment variables:
- MCP_USER_ID: User ID for audit logging (required for logging)
- MCP_TOKEN_ID: Token ID for audit logging
- MCP_SESSION_ID: Session ID for tracking
- MCP_ENABLE_AUDIT: Enable audit logging (default: true)
"""

import asyncio
import json
import os
import time
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
from app.models.skill import Skill, SkillProvider, MeetingImport
from app.models.node import Node
from app.models.mcp import MCPAuditLog, MCPActionStatus
from app.models.project import Project
from app.models.artifact import Artifact
from app.models.change_proposal import ChangeProposal
from app.services import template_service


# Configuration from environment
MCP_USER_ID = os.getenv("MCP_USER_ID")
MCP_TOKEN_ID = os.getenv("MCP_TOKEN_ID")
MCP_SESSION_ID = os.getenv("MCP_SESSION_ID", "local")
MCP_ENABLE_AUDIT = os.getenv("MCP_ENABLE_AUDIT", "true").lower() == "true"


# Create MCP server
server = Server("typequest-canvas")


# ============ Audit Logging ============

async def log_mcp_action(
    tool_name: str,
    arguments: dict,
    status: MCPActionStatus,
    duration_ms: int,
    error_message: str | None = None,
    result_summary: str | None = None,
    canvas_id: int | None = None,
    node_id: int | None = None,
):
    """Log an MCP tool call to the audit log"""
    if not MCP_ENABLE_AUDIT or not MCP_USER_ID:
        return

    try:
        async with async_session_maker() as session:
            log = MCPAuditLog(
                user_id=int(MCP_USER_ID),
                token_id=int(MCP_TOKEN_ID) if MCP_TOKEN_ID else None,
                action="tool_call",
                tool_name=tool_name,
                arguments=arguments,
                status=status.value,
                error_message=error_message,
                result_summary=result_summary,
                canvas_id=canvas_id,
                node_id=node_id,
                session_id=MCP_SESSION_ID,
                duration_ms=duration_ms,
            )
            session.add(log)
            await session.commit()
    except Exception as e:
        # Don't fail the tool call if logging fails
        print(f"Warning: Failed to log MCP action: {e}", file=sys.stderr)


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
            description="Check Jira skill connection status for an organization",
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
            description="Check Zoom skill connection status for an organization",
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
            description="List webhook/skill events and their processing status",
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

        # Jira Strategic Context Tools
        Tool(
            name="search_jira_context",
            description="Search for Jira issues related to a topic/problem for strategic PM context. Uses semantic similarity to find relevant tickets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for (e.g., 'authentication performance issues')",
                    },
                    "canvas_id": {
                        "type": "integer",
                        "description": "Canvas ID to search within",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 10)",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_jira_connections",
            description="Get all Jira issues connected to a canvas or specific node. Useful for understanding related work.",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {
                        "type": "integer",
                        "description": "Canvas ID",
                    },
                    "node_id": {
                        "type": "integer",
                        "description": "Optional - get issues linked to specific node",
                    },
                },
                "required": ["canvas_id"],
            },
        ),
        Tool(
            name="index_jira_for_canvas",
            description="Index all Jira issues on a canvas for semantic search. Call this after importing Jira issues.",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {
                        "type": "integer",
                        "description": "Canvas ID to index",
                    },
                },
                "required": ["canvas_id"],
            },
        ),

        # Project & Artifact Tools
        Tool(
            name="list_projects",
            description="List product-development projects on a canvas. Projects track workflow stages from research to retrospective.",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {"type": "integer", "description": "Canvas ID"},
                    "stage": {
                        "type": "string",
                        "enum": ["research", "prd_review", "ux_review", "tech_spec", "project_kickoff", "development", "qa", "launch", "retrospective"],
                        "description": "Filter by workflow stage",
                    },
                },
            },
        ),
        Tool(
            name="get_project",
            description="Get full details of a project including its artifacts and change proposals.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "The project ID"},
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="create_project",
            description="Create a new product-development project linked to a canvas.",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {"type": "integer", "description": "Canvas to attach the project to"},
                    "name": {"type": "string", "description": "Project name"},
                    "description": {"type": "string", "description": "What this project is building"},
                    "stage": {
                        "type": "string",
                        "enum": ["research", "prd_review", "ux_review", "tech_spec", "project_kickoff", "development", "qa", "launch", "retrospective"],
                        "description": "Starting workflow stage (default: research)",
                    },
                },
                "required": ["canvas_id", "name"],
            },
        ),
        Tool(
            name="create_artifact",
            description="Create an artifact (PRD, tech spec, UX notes, etc.) linked to a project. Also creates a doc node on the canvas.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "Project ID"},
                    "canvas_id": {"type": "integer", "description": "Canvas ID for the doc node"},
                    "artifact_type": {
                        "type": "string",
                        "enum": ["prd", "tech_spec", "ux_design", "timeline", "test_plan", "meeting_notes", "research_notes", "other"],
                        "description": "Type of artifact",
                    },
                    "name": {"type": "string", "description": "Artifact name"},
                    "content": {"type": "string", "description": "Initial markdown content"},
                },
                "required": ["project_id", "canvas_id", "artifact_type", "name"],
            },
        ),
        Tool(
            name="list_change_proposals",
            description="List change proposals (AI-generated change requests) for a canvas or project. Shows pending proposals that need stakeholder review.",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {"type": "integer", "description": "Filter by canvas"},
                    "project_id": {"type": "integer", "description": "Filter by project"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "approved", "rejected", "expired"],
                        "description": "Filter by status (default: pending)",
                    },
                },
            },
        ),
    ]


# ============ Tool Handler ============

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Route tool calls to handlers with audit logging."""
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
        # Jira Strategic Context
        "search_jira_context": handle_search_jira_context,
        "get_jira_connections": handle_get_jira_connections,
        "index_jira_for_canvas": handle_index_jira_for_canvas,
        # Zoom
        "get_zoom_status": handle_get_zoom_status,
        "list_zoom_recordings": handle_list_zoom_recordings,
        "list_meeting_imports": handle_list_meeting_imports,
        # Input Events
        "list_input_events": handle_list_input_events,
        "get_input_event": handle_get_input_event,
        # Projects & Artifacts
        "list_projects": handle_list_projects,
        "get_project": handle_get_project,
        "create_project": handle_create_project,
        "create_artifact": handle_create_artifact,
        "list_change_proposals": handle_list_change_proposals,
    }

    handler = handlers.get(name)
    if not handler:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    # Execute with timing and audit logging
    start_time = time.time()
    error_message = None
    status = MCPActionStatus.SUCCESS

    try:
        result = await handler(arguments)
        duration_ms = int((time.time() - start_time) * 1000)

        # Extract canvas_id/node_id from arguments for context
        canvas_id = arguments.get("canvas_id")
        node_id = arguments.get("node_id") or arguments.get("task_id")

        # Log successful action
        await log_mcp_action(
            tool_name=name,
            arguments=arguments,
            status=status,
            duration_ms=duration_ms,
            canvas_id=canvas_id,
            node_id=node_id,
            result_summary=f"Executed {name} successfully",
        )

        return result

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_message = str(e)
        status = MCPActionStatus.ERROR

        # Log failed action
        await log_mcp_action(
            tool_name=name,
            arguments=arguments,
            status=status,
            duration_ms=duration_ms,
            error_message=error_message,
            canvas_id=arguments.get("canvas_id"),
        )

        return [TextContent(type="text", text=f"Error: {error_message}")]


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
            "doc": {"can_connect_to": ["doc", "agent", "skill"]},
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
    """Get Jira skill status."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Skill).where(
                Skill.organization_id == args["organization_id"],
                Skill.provider == SkillProvider.JIRA.value,
            )
        )
        integration = result.scalar_one_or_none()

        if not integration:
            return [TextContent(type="text", text=json.dumps({
                "connected": False,
                "message": "No Jira skill found",
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
        "endpoint": "POST /api/v1/skills/jira/import",
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
        "endpoint": "POST /api/v1/skills/jira/push",
        "body": {
            "task_id": args["task_id"],
            "project_key": args["project_key"],
            "issue_type": args.get("issue_type", "Task"),
        },
        "note": "This endpoint requires authentication",
    }, indent=2))]


# ============ Zoom Handlers ============

async def handle_get_zoom_status(args: dict) -> list[TextContent]:
    """Get Zoom skill status."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(Skill).where(
                Skill.organization_id == args["organization_id"],
                Skill.provider == SkillProvider.ZOOM.value,
            )
        )
        integration = result.scalar_one_or_none()

        if not integration:
            return [TextContent(type="text", text=json.dumps({
                "connected": False,
                "message": "No Zoom skill found",
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
        "endpoint": "GET /api/v1/skills/zoom/recordings",
        "params": {"days": args.get("days", 30)},
        "note": "This endpoint requires authentication and fetches from Zoom API",
    }, indent=2))]


async def handle_list_meeting_imports(args: dict) -> list[TextContent]:
    """List imported meetings."""
    async with async_session_maker() as session:
        # Get integration first
        int_result = await session.execute(
            select(Skill).where(
                Skill.organization_id == args["organization_id"],
                Skill.provider == SkillProvider.ZOOM.value,
            )
        )
        integration = int_result.scalar_one_or_none()

        if not integration:
            return [TextContent(type="text", text=json.dumps({
                "meetings": [],
                "message": "No Zoom skill found",
            }, indent=2))]

        query = select(MeetingImport).where(
            MeetingImport.skill_id == integration.id
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


# ============ Jira Strategic Context Handlers ============

async def handle_search_jira_context(args: dict) -> list[TextContent]:
    """Search for Jira issues related to a query for strategic PM context."""
    from app.services.jira_context_service import JiraContextService

    async with async_session_maker() as session:
        query = args["query"]
        canvas_id = args.get("canvas_id")
        top_k = args.get("top_k", 10)

        # Note: In MCP context, we might not have user_id/org_id from session
        # For now, search without user context (or you could require them as args)
        issues = await JiraContextService.search_relevant_jira_issues(
            session,
            query_text=query,
            canvas_id=canvas_id,
            user_id=int(MCP_USER_ID) if MCP_USER_ID else None,
            organization_id=None,  # Could add as optional arg
            top_k=top_k,
        )

        # Format for Claude to read
        output = {
            "query": query,
            "canvas_id": canvas_id,
            "results_count": len(issues),
            "issues": [
                {
                    "issue_key": issue["issue_key"],
                    "title": issue["title"],
                    "description": issue.get("description", "")[:200],  # Truncate
                    "status": issue["status"],
                    "priority": issue["priority"],
                    "confidence_score": round(issue["score"] * 100, 1),
                    "source_url": issue.get("source_url"),
                    "task_id": issue["task_id"],
                    "assignee": issue.get("assignee_name"),
                }
                for issue in issues
            ],
        }

        # Also include formatted context for direct use in prompts
        formatted_context = JiraContextService.format_jira_context_for_ai(issues)

        return [
            TextContent(
                type="text",
                text=f"# Jira Strategic Context Search Results\n\n{formatted_context}\n\n## Structured Data\n\n```json\n{json.dumps(output, indent=2)}\n```"
            )
        ]


async def handle_get_jira_connections(args: dict) -> list[TextContent]:
    """Get all Jira issues connected to a canvas or specific node."""
    async with async_session_maker() as session:
        canvas_id = args["canvas_id"]
        node_id = args.get("node_id")

        if node_id:
            # Get issues linked to specific node
            query = select(Task).options(selectinload(Task.linked_nodes)).where(
                and_(
                    Task.source == TaskSource.JIRA,
                    Task.linked_nodes.any(Node.id == node_id),
                )
            )
        else:
            # Get all Jira issues on canvas
            query = select(Task).where(
                and_(
                    Task.canvas_id == canvas_id,
                    Task.source == TaskSource.JIRA,
                )
            )

        result = await session.execute(query)
        tasks = result.scalars().all()

        output = {
            "canvas_id": canvas_id,
            "node_id": node_id,
            "connected_issues_count": len(tasks),
            "issues": [
                {
                    "task_id": t.id,
                    "issue_key": t.source_id,
                    "title": t.title,
                    "description": t.description,
                    "status": t.status,
                    "priority": t.priority,
                    "assignee": t.assignee_name,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                    "source_url": t.source_url,
                    "linked_nodes_count": len(t.linked_nodes) if hasattr(t, 'linked_nodes') else 0,
                }
                for t in tasks
            ],
        }

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_index_jira_for_canvas(args: dict) -> list[TextContent]:
    """Index all Jira issues on a canvas for semantic search."""
    from app.services.jira_context_service import JiraContextService

    async with async_session_maker() as session:
        canvas_id = args["canvas_id"]

        # Index issues (requires user_id for settings)
        if not MCP_USER_ID:
            return [
                TextContent(
                    type="text",
                    text="Error: MCP_USER_ID environment variable required for indexing"
                )
            ]

        result = await JiraContextService.index_jira_issues(
            session,
            canvas_id=canvas_id,
            user_id=int(MCP_USER_ID),
            organization_id=None,  # Could add as arg
        )

        output = {
            "canvas_id": canvas_id,
            "indexed_count": result["indexed"],
            "status": result["status"],
            "message": f"Successfully indexed {result['indexed']} Jira issues for AI-powered search",
        }

        return [TextContent(type="text", text=json.dumps(output, indent=2))]


# ============ Project & Artifact Handlers ============

async def handle_list_projects(arguments: dict) -> list[TextContent]:
    """List projects on a canvas."""
    canvas_id = arguments.get("canvas_id")
    stage_filter = arguments.get("stage")

    async with async_session_maker() as session:
        query = select(Project)
        if canvas_id:
            query = query.where(Project.canvas_id == canvas_id)
        if stage_filter:
            query = query.where(Project.current_stage == stage_filter)
        query = query.order_by(Project.updated_at.desc())
        result = await session.execute(query)
        projects = result.scalars().all()

    output = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "canvas_id": p.canvas_id,
            "stage": p.current_stage,
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in projects
    ]
    return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_get_project(arguments: dict) -> list[TextContent]:
    """Get project with artifacts and change proposals."""
    project_id = arguments["project_id"]

    async with async_session_maker() as session:
        project = await session.get(Project, project_id)
        if not project:
            return [TextContent(type="text", text=json.dumps({"error": "Project not found"}))]

        artifacts_result = await session.execute(
            select(Artifact).where(Artifact.project_id == project_id)
        )
        artifacts = artifacts_result.scalars().all()

        proposals_result = await session.execute(
            select(ChangeProposal).where(
                ChangeProposal.project_id == project_id,
                ChangeProposal.status == "pending",
            )
        )
        pending_proposals = proposals_result.scalars().all()

    output = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "canvas_id": project.canvas_id,
        "stage": project.current_stage,
        "status": project.status,
        "artifacts": [
            {
                "id": a.id,
                "name": a.name,
                "type": a.artifact_type,
                "status": a.status,
                "version": a.version,
            }
            for a in artifacts
        ],
        "pending_proposals_count": len(pending_proposals),
    }
    return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_create_project(arguments: dict) -> list[TextContent]:
    """Create a product-development project."""
    if not MCP_USER_ID:
        return [TextContent(type="text", text=json.dumps({"error": "MCP_USER_ID not set"}))]

    async with async_session_maker() as session:
        project = Project(
            canvas_id=arguments["canvas_id"],
            name=arguments["name"],
            description=arguments.get("description", ""),
            current_stage=arguments.get("stage", "research"),
            created_by_id=int(MCP_USER_ID),
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

    output = {
        "project_id": project.id,
        "name": project.name,
        "stage": project.current_stage,
        "canvas_id": project.canvas_id,
    }
    return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_create_artifact(arguments: dict) -> list[TextContent]:
    """Create an artifact and a doc node on the canvas."""
    if not MCP_USER_ID:
        return [TextContent(type="text", text=json.dumps({"error": "MCP_USER_ID not set"}))]

    project_id = arguments["project_id"]
    canvas_id = arguments["canvas_id"]
    artifact_type = arguments["artifact_type"]
    name = arguments["name"]
    content = arguments.get("content", "")

    async with async_session_maker() as session:
        # Count nodes to determine position
        nodes_result = await session.execute(
            select(Node).where(Node.canvas_id == canvas_id)
        )
        node_count = len(nodes_result.scalars().all())

        node = Node(
            canvas_id=canvas_id,
            name=name,
            node_type="doc",
            content=content[:500] if content else "",
            position_x=200 + (node_count % 3) * 380,
            position_y=200 + (node_count // 3) * 280,
            width=320,
            height=200,
        )
        session.add(node)
        await session.flush()

        artifact = Artifact(
            name=name,
            artifact_type=artifact_type,
            project_id=project_id,
            canvas_id=canvas_id,
            node_id=node.id,
            content=content or "",
            content_format="markdown",
            created_by_id=int(MCP_USER_ID),
            current_owner_id=int(MCP_USER_ID),
        )
        session.add(artifact)
        await session.commit()
        await session.refresh(artifact)

    output = {
        "artifact_id": artifact.id,
        "node_id": node.id,
        "name": name,
        "type": artifact_type,
        "canvas_id": canvas_id,
    }
    return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_list_change_proposals(arguments: dict) -> list[TextContent]:
    """List change proposals for a canvas or project."""
    canvas_id = arguments.get("canvas_id")
    project_id = arguments.get("project_id")
    status_filter = arguments.get("status", "pending")

    async with async_session_maker() as session:
        query = select(ChangeProposal)
        if project_id:
            query = query.where(ChangeProposal.project_id == project_id)
        if status_filter:
            query = query.where(ChangeProposal.status == status_filter)
        query = query.order_by(ChangeProposal.created_at.desc()).limit(50)
        result = await session.execute(query)
        proposals = result.scalars().all()

    output = [
        {
            "id": p.id,
            "title": p.title,
            "artifact_id": p.artifact_id,
            "project_id": p.project_id,
            "change_type": p.change_type,
            "severity": p.severity,
            "status": p.status,
            "triggered_by": p.triggered_by_type,
            "triggered_by_id": p.triggered_by_id,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in proposals
    ]
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

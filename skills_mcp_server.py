#!/usr/bin/env python3
"""
MCP Server for Jira & Confluence Skills

Exposes CRUD operations on Jira issues/comments and Confluence pages/spaces
via MCP protocol. Authenticates using the user's connected OAuth skills.

Run with: python skills_mcp_server.py

Environment variables:
- MCP_USER_ID: User ID for skill lookup and audit logging (required)
- MCP_TOKEN_ID: Token ID for audit logging
- MCP_SESSION_ID: Session ID for tracking
- MCP_ENABLE_AUDIT: Enable audit logging (default: true)
"""

import asyncio
import json
import os
import time
import sys
import logging
from typing import Any, Optional
from datetime import datetime, timedelta

# MCP SDK imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("MCP SDK not installed. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

# Database imports
from sqlalchemy import select
from app.core.database import async_session_maker
from app.models.skill import Skill, SkillProvider
from app.models.mcp import MCPAuditLog, MCPActionStatus
from app.services.jira import JiraService, JiraSkillService, JiraError
from app.services.confluence import ConfluenceService
from app.core.config import settings

logger = logging.getLogger(__name__)

# Configuration from environment
MCP_USER_ID = os.getenv("MCP_USER_ID")
MCP_TOKEN_ID = os.getenv("MCP_TOKEN_ID")
MCP_SESSION_ID = os.getenv("MCP_SESSION_ID", "local")
MCP_ENABLE_AUDIT = os.getenv("MCP_ENABLE_AUDIT", "true").lower() == "true"

# Create MCP server
server = Server("typequest-skills")


# ============ Audit Logging ============

async def log_mcp_action(
    tool_name: str,
    arguments: dict,
    status: MCPActionStatus,
    duration_ms: int,
    error_message: str | None = None,
    result_summary: str | None = None,
):
    """Log an MCP tool call to the audit log."""
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
                session_id=MCP_SESSION_ID,
                duration_ms=duration_ms,
            )
            session.add(log)
            await session.commit()
    except Exception as e:
        print(f"Warning: Failed to log MCP action: {e}", file=sys.stderr)


# ============ Connection Helpers ============

async def get_jira_connection(session) -> tuple[JiraService, str, str]:
    """
    Look up user's Jira skill, refresh token if needed, return (service, access_token, cloud_id).

    Uses JiraSkillService fallback chain: personal → org → individual.
    """
    if not MCP_USER_ID:
        raise JiraError("MCP_USER_ID not set")

    user_id = int(MCP_USER_ID)
    skill_service = JiraSkillService()

    # Try individual first (no org), then let the service figure out fallback
    integration = await skill_service.get_integration(
        session, organization_id=None, user_id=user_id
    )

    if not integration or not integration.is_connected:
        raise JiraError("No connected Jira skill found for this user")

    # Refresh token if needed
    access_token = await skill_service.get_or_refresh_token(session, integration)
    cloud_id = integration.provider_data.get("cloud_id")

    if not cloud_id:
        raise JiraError("Jira skill missing cloud_id in provider_data")

    return JiraService(), access_token, cloud_id


async def get_confluence_connection(session) -> ConfluenceService:
    """
    Look up user's Confluence skill, refresh token if needed, return ready ConfluenceService.
    """
    if not MCP_USER_ID:
        raise ValueError("MCP_USER_ID not set")

    user_id = int(MCP_USER_ID)

    # Look up Confluence skill for the user
    result = await session.execute(
        select(Skill).where(
            Skill.user_id == user_id,
            Skill.organization_id == None,
            Skill.provider == SkillProvider.CONFLUENCE,
        )
    )
    integration = result.scalar_one_or_none()

    if not integration or not integration.is_connected:
        raise ValueError("No connected Confluence skill found for this user")

    # Refresh token if expired
    access_token = integration.access_token
    if integration.is_token_expired and integration.refresh_token:
        svc = ConfluenceService()
        tokens = await svc.refresh_access_token(integration.refresh_token)
        integration.access_token = tokens["access_token"]
        integration.refresh_token = tokens.get("refresh_token", integration.refresh_token)
        integration.token_expires_at = datetime.utcnow() + timedelta(
            seconds=tokens.get("expires_in", 3600)
        )
        await session.commit()
        access_token = integration.access_token

    cloud_id = integration.provider_data.get("cloud_id")
    if not cloud_id:
        raise ValueError("Confluence skill missing cloud_id in provider_data")

    return ConfluenceService(access_token=access_token, cloud_id=cloud_id)


# ============ Tool Definitions ============

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        # ---- Jira Tools ----
        Tool(
            name="jira_search_issues",
            description="Search for Jira issues using JQL (Jira Query Language)",
            inputSchema={
                "type": "object",
                "properties": {
                    "jql": {
                        "type": "string",
                        "description": "JQL query string (e.g., 'project = PROJ AND status != Done')",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results (default 50)",
                    },
                },
                "required": ["jql"],
            },
        ),
        Tool(
            name="jira_get_issue",
            description="Get full details of a Jira issue by key (e.g., PROJ-123)",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key (e.g., PROJ-123)",
                    },
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="jira_create_issue",
            description="Create a new Jira issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_key": {
                        "type": "string",
                        "description": "Jira project key (e.g., PROJ)",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Issue summary/title",
                    },
                    "issue_type": {
                        "type": "string",
                        "description": "Issue type (default: Task). Common types: Task, Bug, Story, Epic",
                    },
                    "description": {
                        "type": "string",
                        "description": "Issue description (plain text, converted to ADF)",
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority name (e.g., High, Medium, Low)",
                    },
                    "assignee_id": {
                        "type": "string",
                        "description": "Atlassian account ID of the assignee",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Labels to apply to the issue",
                    },
                },
                "required": ["project_key", "summary"],
            },
        ),
        Tool(
            name="jira_update_issue",
            description="Update fields on an existing Jira issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key (e.g., PROJ-123)",
                    },
                    "summary": {"type": "string", "description": "New summary"},
                    "description": {"type": "string", "description": "New description (plain text)"},
                    "priority": {"type": "string", "description": "New priority name"},
                    "assignee_id": {"type": "string", "description": "New assignee account ID"},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "New labels (replaces existing)",
                    },
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="jira_delete_issue",
            description="Delete a Jira issue. This action is irreversible.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key to delete (e.g., PROJ-123)",
                    },
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="jira_transition_issue",
            description="Transition a Jira issue to a new status (e.g., In Progress, Done)",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key (e.g., PROJ-123)",
                    },
                    "transition_id": {
                        "type": "string",
                        "description": "Transition ID. Use jira_get_transitions to find available IDs.",
                    },
                },
                "required": ["issue_key", "transition_id"],
            },
        ),
        Tool(
            name="jira_get_transitions",
            description="Get available status transitions for a Jira issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key (e.g., PROJ-123)",
                    },
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="jira_get_comments",
            description="Get all comments on a Jira issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key (e.g., PROJ-123)",
                    },
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="jira_add_comment",
            description="Add a comment to a Jira issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key (e.g., PROJ-123)",
                    },
                    "body": {
                        "type": "string",
                        "description": "Comment text (plain text, converted to ADF)",
                    },
                },
                "required": ["issue_key", "body"],
            },
        ),

        # ---- Confluence Tools ----
        Tool(
            name="confluence_list_spaces",
            description="List all Confluence spaces the user has access to",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="confluence_get_space",
            description="Get details of a specific Confluence space by key",
            inputSchema={
                "type": "object",
                "properties": {
                    "space_key": {
                        "type": "string",
                        "description": "Space key (e.g., ENG, PRODUCT)",
                    },
                },
                "required": ["space_key"],
            },
        ),
        Tool(
            name="confluence_list_pages",
            description="List pages in a Confluence space",
            inputSchema={
                "type": "object",
                "properties": {
                    "space_id": {
                        "type": "string",
                        "description": "Space ID (use confluence_get_space to find the ID from a key)",
                    },
                },
                "required": ["space_id"],
            },
        ),
        Tool(
            name="confluence_get_page",
            description="Get a Confluence page with its content",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID",
                    },
                },
                "required": ["page_id"],
            },
        ),
        Tool(
            name="confluence_create_page",
            description="Create a new Confluence page",
            inputSchema={
                "type": "object",
                "properties": {
                    "space_id": {
                        "type": "string",
                        "description": "Space ID to create the page in",
                    },
                    "title": {
                        "type": "string",
                        "description": "Page title",
                    },
                    "body": {
                        "type": "string",
                        "description": "Page content in Confluence storage format (HTML)",
                    },
                    "parent_id": {
                        "type": "string",
                        "description": "Optional parent page ID for nesting",
                    },
                },
                "required": ["space_id", "title", "body"],
            },
        ),
        Tool(
            name="confluence_update_page",
            description="Update an existing Confluence page",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID to update",
                    },
                    "title": {
                        "type": "string",
                        "description": "New page title",
                    },
                    "body": {
                        "type": "string",
                        "description": "New page content in Confluence storage format (HTML)",
                    },
                    "version_number": {
                        "type": "integer",
                        "description": "Current version number (for optimistic locking). Get from confluence_get_page.",
                    },
                },
                "required": ["page_id", "title", "body", "version_number"],
            },
        ),
        Tool(
            name="confluence_delete_page",
            description="Delete a Confluence page. This action is irreversible.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID to delete",
                    },
                },
                "required": ["page_id"],
            },
        ),
    ]


# ============ Jira Handlers ============

async def handle_jira_search_issues(args: dict) -> list[TextContent]:
    """Search Jira issues via JQL."""
    async with async_session_maker() as session:
        jira, token, cloud_id = await get_jira_connection(session)
        result = await jira.search_issues(
            access_token=token,
            cloud_id=cloud_id,
            jql=args["jql"],
            max_results=args.get("max_results", 50),
        )
        # Simplify output for MCP consumption
        issues = []
        for issue in result.get("issues", []):
            fields = issue.get("fields", {})
            issues.append({
                "key": issue["key"],
                "summary": fields.get("summary"),
                "status": fields.get("status", {}).get("name"),
                "priority": fields.get("priority", {}).get("name"),
                "assignee": fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
                "issue_type": fields.get("issuetype", {}).get("name"),
                "created": fields.get("created"),
                "updated": fields.get("updated"),
            })
        output = {
            "total": result.get("total", 0),
            "issues": issues,
        }
        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_jira_get_issue(args: dict) -> list[TextContent]:
    """Get a single Jira issue."""
    async with async_session_maker() as session:
        jira, token, cloud_id = await get_jira_connection(session)
        issue = await jira.get_issue(
            access_token=token,
            cloud_id=cloud_id,
            issue_key=args["issue_key"],
        )
        fields = issue.get("fields", {})
        output = {
            "key": issue["key"],
            "id": issue["id"],
            "summary": fields.get("summary"),
            "description": fields.get("description"),
            "status": fields.get("status", {}).get("name"),
            "priority": fields.get("priority", {}).get("name"),
            "issue_type": fields.get("issuetype", {}).get("name"),
            "assignee": fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
            "reporter": fields.get("reporter", {}).get("displayName") if fields.get("reporter") else None,
            "labels": fields.get("labels", []),
            "project": fields.get("project", {}).get("key"),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "due_date": fields.get("duedate"),
        }
        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_jira_create_issue(args: dict) -> list[TextContent]:
    """Create a new Jira issue."""
    async with async_session_maker() as session:
        jira, token, cloud_id = await get_jira_connection(session)
        result = await jira.create_issue(
            access_token=token,
            cloud_id=cloud_id,
            project_key=args["project_key"],
            issue_type=args.get("issue_type", "Task"),
            summary=args["summary"],
            description=args.get("description"),
            assignee_id=args.get("assignee_id"),
            priority=args.get("priority"),
            labels=args.get("labels"),
        )
        output = {
            "key": result.get("key"),
            "id": result.get("id"),
            "self": result.get("self"),
            "created": True,
        }
        return [TextContent(type="text", text=json.dumps(output, indent=2))]


async def handle_jira_update_issue(args: dict) -> list[TextContent]:
    """Update a Jira issue."""
    async with async_session_maker() as session:
        jira, token, cloud_id = await get_jira_connection(session)

        fields = {}
        if args.get("summary"):
            fields["summary"] = args["summary"]
        if args.get("description"):
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": args["description"]}],
                    }
                ],
            }
        if args.get("priority"):
            fields["priority"] = {"name": args["priority"]}
        if args.get("assignee_id"):
            fields["assignee"] = {"id": args["assignee_id"]}
        if args.get("labels") is not None:
            fields["labels"] = args["labels"]

        await jira.update_issue(
            access_token=token,
            cloud_id=cloud_id,
            issue_key=args["issue_key"],
            fields=fields,
        )
        return [TextContent(type="text", text=json.dumps({
            "issue_key": args["issue_key"],
            "updated": True,
            "fields_updated": list(fields.keys()),
        }, indent=2))]


async def handle_jira_delete_issue(args: dict) -> list[TextContent]:
    """Delete a Jira issue."""
    async with async_session_maker() as session:
        jira, token, cloud_id = await get_jira_connection(session)
        await jira.delete_issue(
            access_token=token,
            cloud_id=cloud_id,
            issue_key=args["issue_key"],
        )
        return [TextContent(type="text", text=json.dumps({
            "issue_key": args["issue_key"],
            "deleted": True,
        }, indent=2))]


async def handle_jira_transition_issue(args: dict) -> list[TextContent]:
    """Transition a Jira issue to a new status."""
    async with async_session_maker() as session:
        jira, token, cloud_id = await get_jira_connection(session)
        await jira.transition_issue(
            access_token=token,
            cloud_id=cloud_id,
            issue_key=args["issue_key"],
            transition_id=args["transition_id"],
        )
        return [TextContent(type="text", text=json.dumps({
            "issue_key": args["issue_key"],
            "transitioned": True,
            "transition_id": args["transition_id"],
        }, indent=2))]


async def handle_jira_get_transitions(args: dict) -> list[TextContent]:
    """Get available transitions for an issue."""
    async with async_session_maker() as session:
        jira, token, cloud_id = await get_jira_connection(session)
        transitions = await jira.get_transitions(
            access_token=token,
            cloud_id=cloud_id,
            issue_key=args["issue_key"],
        )
        output = [
            {
                "id": t["id"],
                "name": t["name"],
                "to_status": t.get("to", {}).get("name"),
            }
            for t in transitions
        ]
        return [TextContent(type="text", text=json.dumps({
            "issue_key": args["issue_key"],
            "transitions": output,
        }, indent=2))]


async def handle_jira_get_comments(args: dict) -> list[TextContent]:
    """Get comments on an issue."""
    async with async_session_maker() as session:
        jira, token, cloud_id = await get_jira_connection(session)
        comments = await jira.get_comments(
            access_token=token,
            cloud_id=cloud_id,
            issue_key=args["issue_key"],
        )
        output = []
        for c in comments:
            # Extract plain text from ADF body
            body_text = ""
            body = c.get("body", {})
            if isinstance(body, dict):
                for content_block in body.get("content", []):
                    for inner in content_block.get("content", []):
                        if inner.get("type") == "text":
                            body_text += inner.get("text", "")
            output.append({
                "id": c.get("id"),
                "author": c.get("author", {}).get("displayName"),
                "body": body_text,
                "created": c.get("created"),
                "updated": c.get("updated"),
            })
        return [TextContent(type="text", text=json.dumps({
            "issue_key": args["issue_key"],
            "comments": output,
        }, indent=2))]


async def handle_jira_add_comment(args: dict) -> list[TextContent]:
    """Add a comment to an issue."""
    async with async_session_maker() as session:
        jira, token, cloud_id = await get_jira_connection(session)
        result = await jira.add_comment(
            access_token=token,
            cloud_id=cloud_id,
            issue_key=args["issue_key"],
            body=args["body"],
        )
        return [TextContent(type="text", text=json.dumps({
            "issue_key": args["issue_key"],
            "comment_id": result.get("id"),
            "created": True,
        }, indent=2))]


# ============ Confluence Handlers ============

async def handle_confluence_list_spaces(args: dict) -> list[TextContent]:
    """List Confluence spaces."""
    async with async_session_maker() as session:
        confluence = await get_confluence_connection(session)
        try:
            spaces = await confluence.list_spaces()
            output = [
                {
                    "id": s.id,
                    "key": s.key,
                    "name": s.name,
                    "type": s.type,
                    "description": s.description,
                }
                for s in spaces
            ]
            return [TextContent(type="text", text=json.dumps(output, indent=2))]
        finally:
            await confluence.close()


async def handle_confluence_get_space(args: dict) -> list[TextContent]:
    """Get a specific Confluence space."""
    async with async_session_maker() as session:
        confluence = await get_confluence_connection(session)
        try:
            space = await confluence.get_space(args["space_key"])
            if not space:
                return [TextContent(type="text", text=json.dumps({
                    "error": f"Space '{args['space_key']}' not found",
                }))]
            output = {
                "id": space.id,
                "key": space.key,
                "name": space.name,
                "type": space.type,
            }
            return [TextContent(type="text", text=json.dumps(output, indent=2))]
        finally:
            await confluence.close()


async def handle_confluence_list_pages(args: dict) -> list[TextContent]:
    """List pages in a Confluence space."""
    async with async_session_maker() as session:
        confluence = await get_confluence_connection(session)
        try:
            result = await confluence.list_pages(space_id=args["space_id"])
            output = [
                {
                    "id": p.id,
                    "title": p.title,
                    "version": p.version,
                    "web_url": p.web_url,
                }
                for p in result["pages"]
            ]
            return [TextContent(type="text", text=json.dumps({
                "total": result["total"],
                "pages": output,
            }, indent=2))]
        finally:
            await confluence.close()


async def handle_confluence_get_page(args: dict) -> list[TextContent]:
    """Get a Confluence page with content."""
    async with async_session_maker() as session:
        confluence = await get_confluence_connection(session)
        try:
            page = await confluence.get_page(page_id=args["page_id"], include_body=True)
            if not page:
                return [TextContent(type="text", text=json.dumps({
                    "error": f"Page '{args['page_id']}' not found",
                }))]
            output = {
                "id": page.id,
                "title": page.title,
                "space_key": page.space_key,
                "version": page.version,
                "web_url": page.web_url,
                "body_html": page.body_html,
            }
            return [TextContent(type="text", text=json.dumps(output, indent=2))]
        finally:
            await confluence.close()


async def handle_confluence_create_page(args: dict) -> list[TextContent]:
    """Create a new Confluence page."""
    async with async_session_maker() as session:
        confluence = await get_confluence_connection(session)
        try:
            page = await confluence.create_page(
                space_id=args["space_id"],
                title=args["title"],
                body_html=args["body"],
                parent_id=args.get("parent_id"),
            )
            output = {
                "id": page.id,
                "title": page.title,
                "version": page.version,
                "web_url": page.web_url,
                "created": True,
            }
            return [TextContent(type="text", text=json.dumps(output, indent=2))]
        finally:
            await confluence.close()


async def handle_confluence_update_page(args: dict) -> list[TextContent]:
    """Update an existing Confluence page."""
    async with async_session_maker() as session:
        confluence = await get_confluence_connection(session)
        try:
            page = await confluence.update_page(
                page_id=args["page_id"],
                title=args["title"],
                body_html=args["body"],
                version=args["version_number"],
            )
            output = {
                "id": page.id,
                "title": page.title,
                "version": page.version,
                "web_url": page.web_url,
                "updated": True,
            }
            return [TextContent(type="text", text=json.dumps(output, indent=2))]
        finally:
            await confluence.close()


async def handle_confluence_delete_page(args: dict) -> list[TextContent]:
    """Delete a Confluence page."""
    async with async_session_maker() as session:
        confluence = await get_confluence_connection(session)
        try:
            await confluence.delete_page(page_id=args["page_id"])
            return [TextContent(type="text", text=json.dumps({
                "page_id": args["page_id"],
                "deleted": True,
            }, indent=2))]
        finally:
            await confluence.close()


# ============ Tool Dispatcher ============

TOOL_HANDLERS = {
    # Jira
    "jira_search_issues": handle_jira_search_issues,
    "jira_get_issue": handle_jira_get_issue,
    "jira_create_issue": handle_jira_create_issue,
    "jira_update_issue": handle_jira_update_issue,
    "jira_delete_issue": handle_jira_delete_issue,
    "jira_transition_issue": handle_jira_transition_issue,
    "jira_get_transitions": handle_jira_get_transitions,
    "jira_get_comments": handle_jira_get_comments,
    "jira_add_comment": handle_jira_add_comment,
    # Confluence
    "confluence_list_spaces": handle_confluence_list_spaces,
    "confluence_get_space": handle_confluence_get_space,
    "confluence_list_pages": handle_confluence_list_pages,
    "confluence_get_page": handle_confluence_get_page,
    "confluence_create_page": handle_confluence_create_page,
    "confluence_update_page": handle_confluence_update_page,
    "confluence_delete_page": handle_confluence_delete_page,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Route tool calls to handlers with audit logging."""
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

    start_time = time.time()

    try:
        result = await handler(arguments)
        duration_ms = int((time.time() - start_time) * 1000)

        await log_mcp_action(
            tool_name=name,
            arguments=arguments,
            status=MCPActionStatus.SUCCESS,
            duration_ms=duration_ms,
            result_summary=f"Executed {name} successfully",
        )

        return result

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_message = str(e)

        await log_mcp_action(
            tool_name=name,
            arguments=arguments,
            status=MCPActionStatus.ERROR,
            duration_ms=duration_ms,
            error_message=error_message,
        )

        return [TextContent(type="text", text=json.dumps({"error": error_message}))]


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

"""
Canvas / Lifecycle Agent Service.

AI-powered assistant using Claude with tool use.  Handles:
- Pre-canvas (global) chat: creates the canvas, project, nodes, and artifacts
  from a conversational prompt.
- In-canvas chat: continues managing nodes, artifacts, and change proposals
  throughout the full product-development lifecycle.
"""
import json
import httpx
from typing import Optional, Dict, Any, List, AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.node import Node, NodeConnection
from app.models.canvas import Canvas
from app.models.project import Project, WorkflowStage
from app.models.artifact import Artifact, ArtifactType
from app.models.skill import Skill, SkillProvider
from app.services.settings_service import SettingsService
from app.services.jira import JiraService, JiraSkillService, JiraError
from app.services.confluence import ConfluenceService


class CanvasAgentError(Exception):
    """Raised when canvas agent operations fail."""
    pass


# ---------------------------------------------------------------------------
# Tool definitions for Claude
# ---------------------------------------------------------------------------

CANVAS_TOOLS = [
    {
        "name": "create_canvas",
        "description": (
            "Create a brand-new canvas workspace for a product. "
            "Call this first when the user describes a new project and no canvas exists yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Canvas name (e.g. 'Mobile Payments App')"
                },
                "description": {
                    "type": "string",
                    "description": "Short description of what the product is"
                },
                "organization_id": {
                    "type": "integer",
                    "description": "Organization to attach the canvas to (optional)"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "create_project",
        "description": (
            "Create a product-development project linked to the current canvas. "
            "A project tracks the workflow from Research → PRD → UX → Tech Spec → "
            "Kickoff → Development → QA → Launch → Retrospective. "
            "Call this after the canvas is created."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Project name"
                },
                "description": {
                    "type": "string",
                    "description": "What this project is building"
                },
                "canvas_id": {
                    "type": "integer",
                    "description": "Canvas to attach the project to"
                },
                "organization_id": {
                    "type": "integer",
                    "description": "Organization ID (optional)"
                },
                "stage": {
                    "type": "string",
                    "enum": [
                        "research", "prd_review", "ux_review", "tech_spec",
                        "project_kickoff", "development", "qa", "launch", "retrospective"
                    ],
                    "description": "Starting workflow stage (default: research)"
                }
            },
            "required": ["name", "canvas_id"]
        }
    },
    {
        "name": "create_artifact",
        "description": (
            "Create a product artifact (PRD, tech spec, UX notes, timeline, test plan, etc.) "
            "linked to a project. Also creates a doc node on the canvas. "
            "Use this to proactively draft documents from the user's description."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "Project to attach the artifact to"
                },
                "canvas_id": {
                    "type": "integer",
                    "description": "Canvas to place the doc node on"
                },
                "artifact_type": {
                    "type": "string",
                    "enum": [
                        "prd", "tech_spec", "ux_design", "timeline",
                        "test_plan", "meeting_notes", "research_notes", "other"
                    ],
                    "description": "Type of artifact"
                },
                "name": {
                    "type": "string",
                    "description": "Artifact/document name"
                },
                "initial_content": {
                    "type": "string",
                    "description": "Initial markdown content for the artifact (optional — generate from context if not provided)"
                }
            },
            "required": ["project_id", "canvas_id", "artifact_type", "name"]
        }
    },
    {
        "name": "create_node",
        "description": (
            "Create a new node on the canvas. Use for problem statements, objectives, "
            "key results, metrics, or generic docs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_type": {
                    "type": "string",
                    "enum": ["problem", "objective", "keyresult", "metric", "doc"],
                    "description": "The type of node to create"
                },
                "name": {
                    "type": "string",
                    "description": "The name/title of the node"
                },
                "content": {
                    "type": "string",
                    "description": "Optional content/description for the node"
                },
                "canvas_id": {
                    "type": "integer",
                    "description": "Canvas to create the node on (uses current canvas if omitted)"
                }
            },
            "required": ["node_type", "name"]
        }
    },
    {
        "name": "connect_nodes",
        "description": (
            "Create a connection between two nodes. "
            "Flows: problem→objective, objective→keyresult, keyresult→metric."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_node_id": {"type": "integer"},
                "target_node_id": {"type": "integer"}
            },
            "required": ["source_node_id", "target_node_id"]
        }
    },
    {
        "name": "update_node",
        "description": "Update an existing node's name or content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "integer"},
                "name": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["node_id"]
        }
    },
    {
        "name": "delete_node",
        "description": "Delete a node from the canvas. Use with caution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "integer"}
            },
            "required": ["node_id"]
        }
    },
    {
        "name": "get_canvas_state",
        "description": (
            "Get the current state of all nodes, connections, and projects on the canvas. "
            "Call this before making changes to understand what already exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "canvas_id": {
                    "type": "integer",
                    "description": "Canvas to inspect (uses current canvas if omitted)"
                }
            },
            "required": []
        }
    },
    {
        "name": "search_jira_issues",
        "description": (
            "Search Jira issues using JQL. Use this to find relevant issues, bugs, or tasks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jql": {
                    "type": "string",
                    "description": "JQL query string (e.g. 'project = PROJ AND status != Done')"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 10)"
                }
            },
            "required": ["jql"]
        }
    },
    {
        "name": "get_jira_issue",
        "description": (
            "Get details of a specific Jira issue including description and comments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_key": {
                    "type": "string",
                    "description": "Jira issue key (e.g. 'PROJ-123')"
                }
            },
            "required": ["issue_key"]
        }
    },
    {
        "name": "search_confluence_pages",
        "description": (
            "List Confluence spaces or pages in a specific space. "
            "Omit space_key to list all available spaces."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "space_key": {
                    "type": "string",
                    "description": "Confluence space key (omit to list all spaces)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 20)"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_confluence_page",
        "description": "Get a Confluence page's content by page ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Confluence page ID"
                }
            },
            "required": ["page_id"]
        }
    }
]

SYSTEM_PROMPT = """You are Merlin, an intelligent product-development lifecycle assistant.

## Your Role
You help users go from "I have an idea" to a fully scaffolded product workspace — and then guide them through every stage of development.

## Capabilities
You can create and manage:
- **Canvases** — the strategic workspace (one per product area)
- **Projects** — workflow containers on a canvas; each moves through these stages:
  research → prd_review → ux_review → tech_spec → project_kickoff → development → qa → launch → retrospective
- **Artifacts** — documents linked to projects: PRD, Tech Spec, UX Design, Timeline, Test Plan, Meeting Notes, Research Notes
- **Nodes** — visual elements on the canvas: problem, objective, keyresult, metric, doc
- **Connections** — links between nodes following the hierarchy: problem→objective→keyresult→metric
- **Jira** — search issues, read issue details (when Jira is connected)
- **Confluence** — browse spaces, read pages (when Confluence is connected)

## Node Types
- **problem**: A problem statement or challenge to solve
- **objective**: A high-level goal (OKR style)
- **keyresult**: A measurable outcome for an objective
- **metric**: A KPI or tracked number
- **doc**: A document (auto-linked to an Artifact when created via create_artifact)

## Lifecycle Flow
1. User describes their product idea (possibly with an attachment/PDF)
2. You ask at most 3–4 targeted questions to clarify scope, users, and goals
3. You call `create_canvas` → `create_project` → create problem/objective nodes → `create_artifact` for a PRD
4. As the project evolves, you help manage artifacts, review Jira change proposals, and guide stage transitions

## Guiding Principles
- **Proactive**: If the user describes a product, immediately start building — don't ask unnecessary questions
- **Concise questions**: Never ask more than 4 questions before starting to build
- **PRD first**: Always create a PRD artifact from the user's description without being asked
- **Natural references**: If the user uploaded a document, refer to it naturally ("Based on your PRD...")
- **Suggest next steps**: After creating something, briefly suggest the logical next action

## When No Canvas Exists Yet
Start with `create_canvas`, then `create_project`, then add a couple of problem/objective nodes, then draft a PRD artifact.

## When Inside a Canvas
Use `get_canvas_state` first to understand existing context, then make targeted improvements.

## Connected Integrations
If the user has Jira or Confluence connected, you can search and read their data.
Use `search_jira_issues` with JQL to find issues. Use `search_confluence_pages` to browse.
If a tool returns "not connected", let the user know they can connect it from the canvas.

Always confirm what you've done and suggest the next logical step."""


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class CanvasAgent:
    """AI-powered lifecycle assistant using Claude."""

    async def _get_api_key(self, session: AsyncSession, user_id: int) -> tuple[str, str]:
        """Get API key and model from settings."""
        settings = await SettingsService.get_effective_settings(session, user_id)

        provider = settings.get("preferred_llm_provider", "anthropic")
        model = settings.get("preferred_llm_model", "claude-sonnet-4-20250514")

        if provider == "anthropic":
            api_key = settings.get("anthropic_api_key")
            if not api_key:
                raise CanvasAgentError("Anthropic API key not configured")
            return api_key, model
        else:
            raise CanvasAgentError("Only Anthropic provider is supported for canvas agent")

    async def _call_claude(
        self,
        api_key: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Call Claude API with tool use."""
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 8192,
                    "system": SYSTEM_PROMPT,
                    "messages": messages,
                    "tools": tools,
                },
            )

            if response.status_code != 200:
                raise CanvasAgentError(
                    f"Claude API error: {response.status_code} - {response.text}"
                )

            return response.json()

    async def _execute_tool(
        self,
        session: AsyncSession,
        canvas_id: Optional[int],
        user_id: int,
        tool_name: str,
        tool_input: Dict[str, Any],
        session_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a tool and return the result."""
        # Use canvas_id from tool_input if provided, else fall back to session canvas_id
        effective_canvas_id = tool_input.get("canvas_id") or canvas_id

        if tool_name == "create_canvas":
            return await self._tool_create_canvas(session, user_id, tool_input)

        elif tool_name == "create_project":
            return await self._tool_create_project(session, user_id, tool_input)

        elif tool_name == "create_artifact":
            return await self._tool_create_artifact(session, user_id, tool_input)

        elif tool_name == "get_canvas_state":
            return await self._tool_get_canvas_state(session, effective_canvas_id)

        elif tool_name == "create_node":
            return await self._tool_create_node(session, effective_canvas_id, tool_input)

        elif tool_name == "connect_nodes":
            source_id = tool_input["source_node_id"]
            target_id = tool_input["target_node_id"]
            return await self._tool_connect_nodes(session, effective_canvas_id, source_id, target_id)

        elif tool_name == "update_node":
            return await self._tool_update_node(session, effective_canvas_id, tool_input)

        elif tool_name == "delete_node":
            return await self._tool_delete_node(session, effective_canvas_id, tool_input["node_id"])

        elif tool_name == "search_jira_issues":
            return await self._tool_search_jira(
                session, user_id, tool_input["jql"], tool_input.get("max_results", 10)
            )

        elif tool_name == "get_jira_issue":
            return await self._tool_get_jira_issue(session, user_id, tool_input["issue_key"])

        elif tool_name == "search_confluence_pages":
            return await self._tool_search_confluence(
                session, user_id, tool_input.get("space_key"), tool_input.get("limit", 20)
            )

        elif tool_name == "get_confluence_page":
            return await self._tool_get_confluence_page(session, user_id, tool_input["page_id"])

        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

    # ------------------------------------------------------------------
    # Individual tool implementations
    # ------------------------------------------------------------------

    async def _tool_create_canvas(
        self, session: AsyncSession, user_id: int, tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new canvas."""
        canvas = Canvas(
            name=tool_input["name"],
            description=tool_input.get("description", ""),
            created_by_id=user_id,
            organization_id=tool_input.get("organization_id"),
        )
        session.add(canvas)
        await session.flush()
        return {
            "success": True,
            "canvas_id": canvas.id,
            "canvas_name": canvas.name,
            "canvas_url": f"/canvas/{canvas.id}",
        }

    async def _tool_create_project(
        self, session: AsyncSession, user_id: int, tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a project on a canvas."""
        canvas_id = tool_input.get("canvas_id")
        if not canvas_id:
            return {"success": False, "error": "canvas_id is required to create a project"}

        stage = tool_input.get("stage", "research")
        project = Project(
            name=tool_input["name"],
            description=tool_input.get("description", ""),
            canvas_id=canvas_id,
            organization_id=tool_input.get("organization_id"),
            current_stage=stage,
            created_by_id=user_id,
        )
        session.add(project)
        await session.flush()
        return {
            "success": True,
            "project_id": project.id,
            "project_name": project.name,
            "stage": stage,
        }

    async def _tool_create_artifact(
        self, session: AsyncSession, user_id: int, tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create an artifact and a corresponding doc node on the canvas."""
        project_id = tool_input["project_id"]
        canvas_id = tool_input["canvas_id"]
        artifact_type = tool_input["artifact_type"]
        name = tool_input["name"]
        content = tool_input.get("initial_content", "")

        # Count nodes to determine position
        nodes_result = await session.execute(
            select(Node).where(Node.canvas_id == canvas_id)
        )
        node_count = len(nodes_result.scalars().all())

        # Create doc node on canvas
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

        # Create artifact record
        artifact = Artifact(
            name=name,
            artifact_type=artifact_type,
            project_id=project_id,
            canvas_id=canvas_id,
            node_id=node.id,
            content=content or "",
            content_format="markdown",
            created_by_id=user_id,
            current_owner_id=user_id,
        )
        session.add(artifact)
        await session.flush()

        return {
            "success": True,
            "artifact_id": artifact.id,
            "node_id": node.id,
            "artifact_name": name,
            "artifact_type": artifact_type,
        }

    async def _tool_get_canvas_state(
        self, session: AsyncSession, canvas_id: Optional[int]
    ) -> Dict[str, Any]:
        """Get nodes, connections, and projects on the canvas."""
        if not canvas_id:
            return {"success": False, "error": "No canvas in context"}

        nodes_result = await session.execute(
            select(Node).where(Node.canvas_id == canvas_id)
        )
        nodes = nodes_result.scalars().all()

        connections_result = await session.execute(
            select(NodeConnection).where(NodeConnection.canvas_id == canvas_id)
        )
        connections = connections_result.scalars().all()

        projects_result = await session.execute(
            select(Project).where(Project.canvas_id == canvas_id)
        )
        projects = projects_result.scalars().all()

        return {
            "success": True,
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "type": n.node_type,
                    "content": n.content[:200] if n.content else None,
                }
                for n in nodes
            ],
            "connections": [
                {"id": c.id, "source_id": c.source_node_id, "target_id": c.target_node_id}
                for c in connections
            ],
            "projects": [
                {"id": p.id, "name": p.name, "stage": p.current_stage}
                for p in projects
            ],
        }

    async def _tool_create_node(
        self, session: AsyncSession, canvas_id: Optional[int], tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a node on the canvas."""
        if not canvas_id:
            return {"success": False, "error": "No canvas in context"}

        nodes_result = await session.execute(
            select(Node).where(Node.canvas_id == canvas_id)
        )
        node_count = len(nodes_result.scalars().all())

        node = Node(
            canvas_id=canvas_id,
            name=tool_input["name"],
            node_type=tool_input["node_type"],
            content=tool_input.get("content", ""),
            position_x=200 + (node_count % 3) * 350,
            position_y=200 + (node_count // 3) * 250,
            width=280,
            height=180,
        )
        session.add(node)
        await session.flush()

        return {
            "success": True,
            "node_id": node.id,
            "name": node.name,
            "type": node.node_type,
        }

    async def _tool_connect_nodes(
        self,
        session: AsyncSession,
        canvas_id: Optional[int],
        source_id: int,
        target_id: int,
    ) -> Dict[str, Any]:
        """Connect two nodes."""
        source = await session.get(Node, source_id)
        target = await session.get(Node, target_id)

        if not source or not target:
            return {"success": False, "error": "One or both nodes not found"}

        existing = await session.execute(
            select(NodeConnection).where(
                NodeConnection.source_node_id == source_id,
                NodeConnection.target_node_id == target_id,
            )
        )
        if existing.scalar_one_or_none():
            return {"success": False, "error": "Connection already exists"}

        connection = NodeConnection(
            canvas_id=canvas_id or source.canvas_id,
            source_node_id=source_id,
            target_node_id=target_id,
        )
        session.add(connection)
        await session.flush()

        return {
            "success": True,
            "connection_id": connection.id,
            "source": source.name,
            "target": target.name,
        }

    async def _tool_update_node(
        self, session: AsyncSession, canvas_id: Optional[int], tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update a node."""
        node = await session.get(Node, tool_input["node_id"])
        if not node:
            return {"success": False, "error": "Node not found"}

        if "name" in tool_input:
            node.name = tool_input["name"]
        if "content" in tool_input:
            node.content = tool_input["content"]

        await session.flush()
        return {"success": True, "node_id": node.id, "name": node.name}

    async def _tool_delete_node(
        self, session: AsyncSession, canvas_id: Optional[int], node_id: int
    ) -> Dict[str, Any]:
        """Delete a node."""
        node = await session.get(Node, node_id)
        if not node:
            return {"success": False, "error": "Node not found"}

        node_name = node.name
        await session.delete(node)
        await session.flush()
        return {"success": True, "deleted_node": node_name}

    # ------------------------------------------------------------------
    # Jira tool implementations
    # ------------------------------------------------------------------

    async def _get_jira_auth(
        self, session: AsyncSession, user_id: int
    ) -> tuple[str, str]:
        """Get valid Jira access token and cloud_id, or raise."""
        jira_skill_svc = JiraSkillService()
        integration = await jira_skill_svc.get_integration(session, user_id=user_id)
        if not integration:
            raise CanvasAgentError("Jira not connected")
        access_token = await jira_skill_svc.get_or_refresh_token(session, integration)
        cloud_id = integration.provider_data["cloud_id"]
        return access_token, cloud_id

    async def _tool_search_jira(
        self, session: AsyncSession, user_id: int, jql: str, max_results: int = 10
    ) -> Dict[str, Any]:
        """Search Jira issues via JQL."""
        try:
            access_token, cloud_id = await self._get_jira_auth(session, user_id)
        except CanvasAgentError:
            return {"success": False, "error": "Jira not connected. Connect Jira from the canvas to use this tool."}

        try:
            data = await JiraService().search_issues(access_token, cloud_id, jql, max_results=max_results)
            issues = []
            for raw in data.get("issues", []):
                fields = raw.get("fields", {})
                issues.append({
                    "key": raw.get("key"),
                    "summary": fields.get("summary"),
                    "status": (fields.get("status") or {}).get("name"),
                    "priority": (fields.get("priority") or {}).get("name"),
                    "assignee": (fields.get("assignee") or {}).get("displayName"),
                    "type": (fields.get("issuetype") or {}).get("name"),
                })
            return {"success": True, "issues": issues, "total": data.get("total", 0)}
        except JiraError as e:
            return {"success": False, "error": f"Jira search failed: {e}"}

    async def _tool_get_jira_issue(
        self, session: AsyncSession, user_id: int, issue_key: str
    ) -> Dict[str, Any]:
        """Get details of a specific Jira issue."""
        try:
            access_token, cloud_id = await self._get_jira_auth(session, user_id)
        except CanvasAgentError:
            return {"success": False, "error": "Jira not connected. Connect Jira from the canvas to use this tool."}

        try:
            jira_svc = JiraService()
            raw = await jira_svc.get_issue(access_token, cloud_id, issue_key)
            fields = raw.get("fields", {})

            # Get description text (truncated)
            description = ""
            desc_field = fields.get("description")
            if isinstance(desc_field, str):
                description = desc_field[:2000]
            elif isinstance(desc_field, dict):
                # ADF format — extract text content
                parts = []
                for block in desc_field.get("content", []):
                    for inline in block.get("content", []):
                        if inline.get("type") == "text":
                            parts.append(inline.get("text", ""))
                description = "\n".join(parts)[:2000]

            # Get last 5 comments
            comments_data = await jira_svc.get_comments(access_token, cloud_id, issue_key)
            raw_comments = comments_data if isinstance(comments_data, list) else comments_data.get("comments", [])
            comments = []
            for c in raw_comments[-5:]:
                body = c.get("body", "")
                if isinstance(body, dict):
                    # ADF format
                    parts = []
                    for block in body.get("content", []):
                        for inline in block.get("content", []):
                            if inline.get("type") == "text":
                                parts.append(inline.get("text", ""))
                    body = "\n".join(parts)
                comments.append({
                    "author": (c.get("author") or {}).get("displayName"),
                    "body": body[:500] if isinstance(body, str) else str(body)[:500],
                    "created": c.get("created"),
                })

            return {
                "success": True,
                "key": raw.get("key"),
                "summary": fields.get("summary"),
                "description": description,
                "status": (fields.get("status") or {}).get("name"),
                "priority": (fields.get("priority") or {}).get("name"),
                "assignee": (fields.get("assignee") or {}).get("displayName"),
                "comments": comments,
            }
        except JiraError as e:
            return {"success": False, "error": f"Failed to get Jira issue: {e}"}

    # ------------------------------------------------------------------
    # Confluence tool implementations
    # ------------------------------------------------------------------

    async def _get_confluence_service(
        self, session: AsyncSession, user_id: int
    ) -> ConfluenceService:
        """Build an authenticated ConfluenceService, or raise."""
        result = await session.execute(
            select(Skill).where(
                Skill.user_id == user_id,
                Skill.provider == SkillProvider.CONFLUENCE,
            )
        )
        integration = result.scalar_one_or_none()
        if not integration or not integration.is_connected:
            raise CanvasAgentError("Confluence not connected")

        access_token = integration.access_token
        # Refresh if expired
        if integration.is_token_expired and integration.refresh_token:
            svc = ConfluenceService()
            new_tokens = await svc.refresh_access_token(integration.refresh_token)
            integration.access_token = new_tokens["access_token"]
            if new_tokens.get("refresh_token"):
                integration.refresh_token = new_tokens["refresh_token"]
            from datetime import datetime, timedelta
            integration.token_expires_at = datetime.utcnow() + timedelta(
                seconds=new_tokens.get("expires_in", 3600)
            )
            await session.flush()
            access_token = integration.access_token

        cloud_id = integration.provider_data.get("cloud_id", "")
        return ConfluenceService(access_token=access_token, cloud_id=cloud_id)

    async def _tool_search_confluence(
        self, session: AsyncSession, user_id: int,
        space_key: Optional[str] = None, limit: int = 20
    ) -> Dict[str, Any]:
        """List Confluence spaces or pages in a space."""
        try:
            svc = await self._get_confluence_service(session, user_id)
        except CanvasAgentError:
            return {"success": False, "error": "Confluence not connected. Connect Confluence from the canvas to use this tool."}

        try:
            if not space_key:
                spaces = await svc.list_spaces(limit=limit)
                return {
                    "success": True,
                    "spaces": [
                        {"key": s.key, "name": s.name, "type": s.type}
                        for s in spaces
                    ],
                }
            else:
                # Resolve space_key → space_id
                space = await svc.get_space(space_key)
                if not space:
                    return {"success": False, "error": f"Space '{space_key}' not found"}
                data = await svc.list_pages(space.id, limit=limit)
                pages = data.get("pages", [])
                return {
                    "success": True,
                    "pages": [
                        {"id": p.id, "title": p.title, "space_key": space_key}
                        for p in pages
                    ],
                }
        except Exception as e:
            return {"success": False, "error": f"Confluence search failed: {e}"}

    async def _tool_get_confluence_page(
        self, session: AsyncSession, user_id: int, page_id: str
    ) -> Dict[str, Any]:
        """Get a Confluence page's content."""
        try:
            svc = await self._get_confluence_service(session, user_id)
        except CanvasAgentError:
            return {"success": False, "error": "Confluence not connected. Connect Confluence from the canvas to use this tool."}

        try:
            page = await svc.get_page(page_id, include_body=True)
            if not page:
                return {"success": False, "error": f"Page '{page_id}' not found"}
            body = (page.body_html or "")[:3000]
            return {
                "success": True,
                "id": page.id,
                "title": page.title,
                "space_key": page.space_key,
                "body": body,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get Confluence page: {e}"}

    # ------------------------------------------------------------------
    # Main chat loop (non-streaming)
    # ------------------------------------------------------------------

    async def chat(
        self,
        session: AsyncSession,
        canvas_id: Optional[int],
        user_id: int,
        user_message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a chat message and execute canvas/project actions.

        Returns dict with response text, actions taken, and updated conversation.
        """
        api_key, model = await self._get_api_key(session, user_id)

        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})

        # Auto-inject canvas state for context
        if canvas_id:
            state = await self._tool_get_canvas_state(session, canvas_id)
            if state.get("success") and (state.get("nodes") or state.get("projects")):
                context_block = json.dumps({
                    "nodes": state["nodes"],
                    "connections": state["connections"],
                    "projects": state["projects"],
                })
                messages[-1]["content"] = (
                    f"[Canvas context — {len(state['nodes'])} nodes, "
                    f"{len(state['projects'])} projects:\n{context_block}]\n\n"
                    + messages[-1]["content"]
                )

        actions_taken = []

        # Main tool-use loop
        response = await self._call_claude(api_key, model, messages, CANVAS_TOOLS)

        while response.get("stop_reason") == "tool_use":
            tool_uses = [
                block for block in response.get("content", [])
                if block.get("type") == "tool_use"
            ]

            tool_results = []
            for tool_use in tool_uses:
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]

                result = await self._execute_tool(
                    session, canvas_id, user_id, tool_name, tool_input, session_context
                )

                # Update canvas_id in context if a canvas was just created
                if tool_name == "create_canvas" and result.get("success"):
                    canvas_id = result["canvas_id"]
                    if session_context is not None:
                        session_context["canvas_id"] = canvas_id

                actions_taken.append({
                    "type": tool_name,
                    "params": tool_input,
                    "result": result,
                    "status": "complete" if result.get("success") else "error",
                    "description": self._describe_action(tool_name, tool_input, result),
                })

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use["id"],
                    "content": json.dumps(result),
                })

            messages.append({"role": "assistant", "content": response.get("content", [])})
            messages.append({"role": "user", "content": tool_results})
            response = await self._call_claude(api_key, model, messages, CANVAS_TOOLS)

        text_blocks = [
            block.get("text", "")
            for block in response.get("content", [])
            if block.get("type") == "text"
        ]
        response_text = "\n".join(text_blocks)

        await session.commit()

        return {
            "response": response_text,
            "actions": actions_taken,
            "conversation": messages,
            "canvas_id": canvas_id,
        }

    # ------------------------------------------------------------------
    # Streaming chat (SSE-compatible generator)
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        session: AsyncSession,
        canvas_id: Optional[int],
        user_id: int,
        user_message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Streaming version of chat.

        Yields SSE-compatible dicts:
          {"type": "text", "content": "..."}
          {"type": "action", "action": "create_canvas", "status": "running", "params": {...}}
          {"type": "action", "action": "create_canvas", "status": "done", "result": {...}}
          {"type": "done", "canvas_id": 5}
        """
        api_key, model = await self._get_api_key(session, user_id)

        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})

        # Auto-inject canvas state for context
        if canvas_id:
            state = await self._tool_get_canvas_state(session, canvas_id)
            if state.get("success") and (state.get("nodes") or state.get("projects")):
                context_block = json.dumps({
                    "nodes": state["nodes"],
                    "connections": state["connections"],
                    "projects": state["projects"],
                })
                messages[-1]["content"] = (
                    f"[Canvas context — {len(state['nodes'])} nodes, "
                    f"{len(state['projects'])} projects:\n{context_block}]\n\n"
                    + messages[-1]["content"]
                )

        response = await self._call_claude(api_key, model, messages, CANVAS_TOOLS)

        while response.get("stop_reason") == "tool_use":
            # Emit any text content that came before the tool call
            for block in response.get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    yield {"type": "text", "content": block["text"]}

            tool_uses = [
                block for block in response.get("content", [])
                if block.get("type") == "tool_use"
            ]

            tool_results = []
            for tool_use in tool_uses:
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]

                yield {"type": "action", "action": tool_name, "status": "running", "params": tool_input}

                result = await self._execute_tool(
                    session, canvas_id, user_id, tool_name, tool_input, session_context
                )

                # Propagate newly created canvas_id
                if tool_name == "create_canvas" and result.get("success"):
                    canvas_id = result["canvas_id"]
                    if session_context is not None:
                        session_context["canvas_id"] = canvas_id

                yield {
                    "type": "action",
                    "action": tool_name,
                    "status": "done" if result.get("success") else "error",
                    "result": result,
                    "description": self._describe_action(tool_name, tool_input, result),
                }

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use["id"],
                    "content": json.dumps(result),
                })

            messages.append({"role": "assistant", "content": response.get("content", [])})
            messages.append({"role": "user", "content": tool_results})
            response = await self._call_claude(api_key, model, messages, CANVAS_TOOLS)

        # Final text response
        for block in response.get("content", []):
            if block.get("type") == "text" and block.get("text"):
                yield {"type": "text", "content": block["text"]}

        await session.commit()

        yield {"type": "done", "canvas_id": canvas_id}

    def _describe_action(
        self, tool_name: str, tool_input: Dict[str, Any], result: Dict[str, Any]
    ) -> str:
        """Generate a human-readable description of an action."""
        if not result.get("success"):
            return f"Failed: {result.get('error', 'Unknown error')}"

        if tool_name == "create_canvas":
            return f"Created canvas: \"{tool_input.get('name', '')}\""
        elif tool_name == "create_project":
            return f"Created project: \"{tool_input.get('name', '')}\""
        elif tool_name == "create_artifact":
            return f"Created {tool_input.get('artifact_type', 'artifact')}: \"{tool_input.get('name', '')}\""
        elif tool_name == "create_node":
            return f"Created {tool_input['node_type']} node: \"{tool_input['name']}\""
        elif tool_name == "connect_nodes":
            return f"Connected \"{result.get('source')}\" → \"{result.get('target')}\""
        elif tool_name == "update_node":
            return f"Updated node: \"{result.get('name')}\""
        elif tool_name == "delete_node":
            return f"Deleted node: \"{result.get('deleted_node')}\""
        elif tool_name == "get_canvas_state":
            nodes = result.get("nodes", [])
            return f"Retrieved canvas state ({len(nodes)} nodes)"
        elif tool_name == "search_jira_issues":
            total = result.get("total", 0)
            return f"Found {total} Jira issues"
        elif tool_name == "get_jira_issue":
            return f"Retrieved Jira issue: {result.get('key', '')}"
        elif tool_name == "search_confluence_pages":
            if "spaces" in result:
                return f"Listed {len(result['spaces'])} Confluence spaces"
            return f"Listed {len(result.get('pages', []))} Confluence pages"
        elif tool_name == "get_confluence_page":
            return f"Retrieved Confluence page: \"{result.get('title', '')}\""
        else:
            return f"Executed {tool_name}"


# Singleton instance
canvas_agent = CanvasAgent()

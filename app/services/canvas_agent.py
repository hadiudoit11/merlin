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
from app.services.settings_service import SettingsService


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
                    "max_tokens": 4096,
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
        else:
            return f"Executed {tool_name}"


# Singleton instance
canvas_agent = CanvasAgent()

"""
Canvas Agent Service.

AI-powered assistant for canvas operations using Claude with tool use.
"""
import json
import httpx
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.node import Node, NodeConnection
from app.models.canvas import Canvas
from app.services.settings_service import SettingsService


class CanvasAgentError(Exception):
    """Raised when canvas agent operations fail."""
    pass


# Tool definitions for Claude
CANVAS_TOOLS = [
    {
        "name": "create_node",
        "description": "Create a new node on the canvas. Use this when the user wants to add a problem statement, objective, key result, metric, or document.",
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
                }
            },
            "required": ["node_type", "name"]
        }
    },
    {
        "name": "connect_nodes",
        "description": "Create a connection between two nodes. Connections flow from source to target, typically: objective -> keyresult -> metric, or problem -> objective.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_node_id": {
                    "type": "integer",
                    "description": "ID of the source node"
                },
                "target_node_id": {
                    "type": "integer",
                    "description": "ID of the target node"
                }
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
                "node_id": {
                    "type": "integer",
                    "description": "ID of the node to update"
                },
                "name": {
                    "type": "string",
                    "description": "New name for the node (optional)"
                },
                "content": {
                    "type": "string",
                    "description": "New content for the node (optional)"
                }
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
                "node_id": {
                    "type": "integer",
                    "description": "ID of the node to delete"
                }
            },
            "required": ["node_id"]
        }
    },
    {
        "name": "get_canvas_state",
        "description": "Get the current state of all nodes and connections on the canvas. Use this to understand what already exists before making changes.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

SYSTEM_PROMPT = """You are a helpful canvas assistant for a product management tool. You help users create and organize nodes on their canvas.

The canvas supports these node types:
- **problem**: A problem statement or blocker that needs to be addressed
- **objective**: A high-level goal or objective (OKR style)
- **keyresult**: A measurable key result that contributes to an objective
- **metric**: A trackable metric or KPI
- **doc**: A document for notes, PRDs, or other written content

Connection hierarchy typically flows:
- problem → objective (problems feed into objectives)
- objective → keyresult (objectives have key results)
- keyresult → metric (key results are measured by metrics)

When the user asks you to create something, infer the appropriate node type based on context:
- Mentions of problems, issues, blockers, challenges → create problem node
- Mentions of goals, objectives, targets → create objective node
- Mentions of results, outcomes, deliverables → create keyresult node
- Mentions of metrics, KPIs, numbers, tracking → create metric node
- Mentions of documents, notes, specs → create doc node

Be helpful and proactive. If the user creates an objective, offer to create related key results. If they create a problem, offer to create an objective to address it.

Always confirm what you've done and suggest logical next steps."""


class CanvasAgent:
    """AI-powered canvas assistant using Claude."""

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
        async with httpx.AsyncClient(timeout=60.0) as client:
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
        canvas_id: int,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a tool and return the result."""

        if tool_name == "get_canvas_state":
            # Get all nodes and connections for this canvas
            nodes_result = await session.execute(
                select(Node).where(Node.canvas_id == canvas_id)
            )
            nodes = nodes_result.scalars().all()

            connections_result = await session.execute(
                select(NodeConnection).where(NodeConnection.canvas_id == canvas_id)
            )
            connections = connections_result.scalars().all()

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
                    {
                        "id": c.id,
                        "source_id": c.source_node_id,
                        "target_id": c.target_node_id,
                    }
                    for c in connections
                ]
            }

        elif tool_name == "create_node":
            # Get canvas to find position for new node
            canvas = await session.get(Canvas, canvas_id)
            if not canvas:
                return {"success": False, "error": "Canvas not found"}

            # Count existing nodes to offset position
            nodes_result = await session.execute(
                select(Node).where(Node.canvas_id == canvas_id)
            )
            node_count = len(nodes_result.scalars().all())

            # Create the node
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

        elif tool_name == "connect_nodes":
            source_id = tool_input["source_node_id"]
            target_id = tool_input["target_node_id"]

            # Verify nodes exist
            source = await session.get(Node, source_id)
            target = await session.get(Node, target_id)

            if not source or not target:
                return {"success": False, "error": "One or both nodes not found"}

            if source.canvas_id != canvas_id or target.canvas_id != canvas_id:
                return {"success": False, "error": "Nodes must be on the same canvas"}

            # Check if connection already exists
            existing = await session.execute(
                select(NodeConnection).where(
                    NodeConnection.source_node_id == source_id,
                    NodeConnection.target_node_id == target_id,
                )
            )
            if existing.scalar_one_or_none():
                return {"success": False, "error": "Connection already exists"}

            # Create connection
            connection = NodeConnection(
                canvas_id=canvas_id,
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

        elif tool_name == "update_node":
            node_id = tool_input["node_id"]
            node = await session.get(Node, node_id)

            if not node:
                return {"success": False, "error": "Node not found"}

            if node.canvas_id != canvas_id:
                return {"success": False, "error": "Node not on this canvas"}

            if "name" in tool_input:
                node.name = tool_input["name"]
            if "content" in tool_input:
                node.content = tool_input["content"]

            await session.flush()

            return {
                "success": True,
                "node_id": node.id,
                "name": node.name,
            }

        elif tool_name == "delete_node":
            node_id = tool_input["node_id"]
            node = await session.get(Node, node_id)

            if not node:
                return {"success": False, "error": "Node not found"}

            if node.canvas_id != canvas_id:
                return {"success": False, "error": "Node not on this canvas"}

            node_name = node.name
            await session.delete(node)
            await session.flush()

            return {
                "success": True,
                "deleted_node": node_name,
            }

        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

    async def chat(
        self,
        session: AsyncSession,
        canvas_id: int,
        user_id: int,
        user_message: str,
        conversation_history: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a chat message and execute any requested canvas actions.

        Returns:
            Dict with response text and actions taken
        """
        api_key, model = await self._get_api_key(session, user_id)

        # Build messages list
        messages = conversation_history or []
        messages.append({"role": "user", "content": user_message})

        # Track actions taken
        actions_taken = []

        # Call Claude with tools
        response = await self._call_claude(api_key, model, messages, CANVAS_TOOLS)

        # Process response - handle tool use loop
        while response.get("stop_reason") == "tool_use":
            # Extract tool calls
            tool_uses = [
                block for block in response.get("content", [])
                if block.get("type") == "tool_use"
            ]

            # Build tool results
            tool_results = []
            for tool_use in tool_uses:
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]

                # Execute the tool
                result = await self._execute_tool(session, canvas_id, tool_name, tool_input)

                # Track action
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

            # Add assistant's tool use message
            messages.append({
                "role": "assistant",
                "content": response.get("content", []),
            })

            # Add tool results
            messages.append({
                "role": "user",
                "content": tool_results,
            })

            # Continue conversation
            response = await self._call_claude(api_key, model, messages, CANVAS_TOOLS)

        # Extract final text response
        text_blocks = [
            block.get("text", "")
            for block in response.get("content", [])
            if block.get("type") == "text"
        ]
        response_text = "\n".join(text_blocks)

        # Commit any changes
        await session.commit()

        return {
            "response": response_text,
            "actions": actions_taken,
            "conversation": messages,
        }

    def _describe_action(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        result: Dict[str, Any],
    ) -> str:
        """Generate a human-readable description of an action."""
        if not result.get("success"):
            return f"Failed: {result.get('error', 'Unknown error')}"

        if tool_name == "create_node":
            return f"Created {tool_input['node_type']} node: \"{tool_input['name']}\""
        elif tool_name == "connect_nodes":
            return f"Connected \"{result.get('source')}\" to \"{result.get('target')}\""
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

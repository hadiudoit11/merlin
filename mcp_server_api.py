#!/usr/bin/env python3
"""
MCP Server for Typequest Canvas (Production-ready)

This MCP server connects to the Typequest API using authenticated HTTP requests.
It does NOT require direct database access.

Environment variables:
- TYPEQUEST_API_URL: Backend API URL (e.g., https://api.typequest.io)
- TYPEQUEST_API_TOKEN: User's API token (JWT or API key)

Run with: python mcp_server_api.py
"""

import asyncio
import json
import os
import sys
from typing import Any

import httpx

# MCP SDK imports
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("MCP SDK not installed. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)


# Configuration
# Default to production URL - override with env var for local development
API_URL = os.getenv("TYPEQUEST_API_URL", "https://merlin-j5sk.onrender.com")
API_TOKEN = os.getenv("TYPEQUEST_API_TOKEN")

if not API_TOKEN:
    print("Warning: TYPEQUEST_API_TOKEN not set. API calls will fail.", file=sys.stderr)


# Create MCP server
server = Server("typequest-canvas")


# HTTP client helper
async def api_request(
    method: str,
    endpoint: str,
    params: dict | None = None,
    json_body: dict | None = None,
) -> dict:
    """Make an authenticated API request."""
    if not API_TOKEN:
        return {"error": "No API token configured"}

    url = f"{API_URL}/api/v1{endpoint}"
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if method == "GET":
                response = await client.get(url, headers=headers, params=params)
            elif method == "POST":
                response = await client.post(url, headers=headers, json=json_body)
            elif method == "PUT":
                response = await client.put(url, headers=headers, json=json_body)
            elif method == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                return {"error": f"Unsupported method: {method}"}

            if response.status_code == 401:
                return {"error": "Authentication failed. Check your API token."}
            elif response.status_code == 403:
                return {"error": "Permission denied for this operation."}
            elif response.status_code >= 400:
                return {"error": f"API error {response.status_code}: {response.text}"}

            return response.json()

        except httpx.TimeoutException:
            return {"error": "API request timed out"}
        except httpx.RequestError as e:
            return {"error": f"API request failed: {str(e)}"}
        except json.JSONDecodeError:
            return {"error": "Invalid JSON response from API"}


# ============ Tool Definitions ============

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        # Canvas Tools
        Tool(
            name="list_canvases",
            description="List all canvases you have access to",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
            },
        ),
        Tool(
            name="get_canvas",
            description="Get details of a specific canvas including all its nodes and connections",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {"type": "integer", "description": "The canvas ID"},
                },
                "required": ["canvas_id"],
            },
        ),
        Tool(
            name="create_canvas",
            description="Create a new canvas",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Canvas name"},
                    "description": {"type": "string", "description": "Optional description"},
                },
                "required": ["name"],
            },
        ),

        # Node Tools
        Tool(
            name="create_node",
            description="Create a new node on a canvas. Types: problem, objective, keyresult, metric, doc, agent",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {"type": "integer", "description": "Canvas to add node to"},
                    "node_type": {
                        "type": "string",
                        "enum": ["problem", "objective", "keyresult", "metric", "doc", "agent"],
                        "description": "Type of node",
                    },
                    "name": {"type": "string", "description": "Node name/title"},
                    "content": {"type": "string", "description": "Node content or description"},
                    "position_x": {"type": "number", "description": "X position on canvas"},
                    "position_y": {"type": "number", "description": "Y position on canvas"},
                },
                "required": ["canvas_id", "node_type", "name"],
            },
        ),
        Tool(
            name="update_node",
            description="Update an existing node's name or content",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "integer", "description": "The node ID"},
                    "name": {"type": "string", "description": "New name"},
                    "content": {"type": "string", "description": "New content"},
                },
                "required": ["node_id"],
            },
        ),
        Tool(
            name="delete_node",
            description="Delete a node from the canvas",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "integer", "description": "The node ID to delete"},
                },
                "required": ["node_id"],
            },
        ),
        Tool(
            name="connect_nodes",
            description="Create a connection between two nodes. Rules: problem->objective, objective->keyresult, keyresult->metric",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_node_id": {"type": "integer", "description": "Source node ID"},
                    "target_node_id": {"type": "integer", "description": "Target node ID"},
                },
                "required": ["source_node_id", "target_node_id"],
            },
        ),

        # OKR Tools
        Tool(
            name="create_okr_structure",
            description="Create a complete OKR structure with problem, objective, and key results",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {"type": "integer", "description": "Canvas ID"},
                    "problem": {"type": "string", "description": "The problem statement"},
                    "objective": {"type": "string", "description": "The objective to achieve"},
                    "key_results": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of key results (1-5 recommended)",
                    },
                },
                "required": ["canvas_id", "problem", "objective", "key_results"],
            },
        ),

        # Task Tools
        Tool(
            name="list_tasks",
            description="List tasks with optional filters",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                    },
                    "canvas_id": {"type": "integer"},
                    "limit": {"type": "integer", "description": "Max results (default 50)"},
                },
            },
        ),
        Tool(
            name="create_task",
            description="Create a new task",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "description": {"type": "string", "description": "Task description"},
                    "assignee_name": {"type": "string"},
                    "assignee_email": {"type": "string"},
                    "due_date": {"type": "string", "description": "Due date (ISO format)"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                    "canvas_id": {"type": "integer"},
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
                    "task_id": {"type": "integer", "description": "Task ID"},
                    "title": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                    "assignee_name": {"type": "string"},
                    "due_date": {"type": "string"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="get_task_stats",
            description="Get task statistics",
            inputSchema={
                "type": "object",
                "properties": {
                    "canvas_id": {"type": "integer", "description": "Optional canvas filter"},
                },
            },
        ),

        # User Info
        Tool(
            name="whoami",
            description="Get information about the currently authenticated user",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


# ============ Tool Handler ============

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Route tool calls to API endpoints."""

    handlers = {
        # Canvas
        "list_canvases": lambda args: api_request("GET", "/canvases/", params={"limit": args.get("limit", 20)}),
        "get_canvas": lambda args: api_request("GET", f"/canvases/{args['canvas_id']}/"),
        "create_canvas": lambda args: api_request("POST", "/canvases/", json_body={
            "name": args["name"],
            "description": args.get("description", ""),
        }),

        # Nodes
        "create_node": lambda args: api_request("POST", "/nodes/", json_body={
            "canvas_id": args["canvas_id"],
            "node_type": args["node_type"],
            "name": args["name"],
            "content": args.get("content", ""),
            "position_x": args.get("position_x", 0),
            "position_y": args.get("position_y", 0),
        }),
        "update_node": lambda args: api_request("PUT", f"/nodes/{args['node_id']}/", json_body={
            k: v for k, v in args.items() if k != "node_id" and v is not None
        }),
        "delete_node": lambda args: api_request("DELETE", f"/nodes/{args['node_id']}/"),
        "connect_nodes": lambda args: api_request("POST", f"/nodes/{args['source_node_id']}/connect/", json_body={
            "target_node_id": args["target_node_id"],
        }),

        # OKR
        "create_okr_structure": handle_create_okr_structure,

        # Tasks
        "list_tasks": lambda args: api_request("GET", "/tasks/", params={
            k: v for k, v in args.items() if v is not None
        }),
        "create_task": lambda args: api_request("POST", "/tasks/", json_body=args),
        "update_task": lambda args: api_request("PUT", f"/tasks/{args['task_id']}/", json_body={
            k: v for k, v in args.items() if k != "task_id" and v is not None
        }),
        "get_task_stats": lambda args: api_request("GET", "/tasks/stats", params={
            k: v for k, v in args.items() if v is not None
        }),

        # User
        "whoami": lambda args: api_request("GET", "/auth/me"),
    }

    handler = handlers.get(name)
    if not handler:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    try:
        if asyncio.iscoroutinefunction(handler):
            result = await handler(arguments)
        else:
            result = await handler(arguments)

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, indent=2))]


async def handle_create_okr_structure(args: dict) -> dict:
    """Create a full OKR structure with connected nodes."""
    canvas_id = args["canvas_id"]
    results = {"created_nodes": [], "connections": []}

    # Create problem node
    problem = await api_request("POST", "/nodes/", json_body={
        "canvas_id": canvas_id,
        "node_type": "problem",
        "name": "Problem",
        "content": args["problem"],
        "position_x": 100,
        "position_y": 100,
    })
    if "error" in problem:
        return problem
    results["created_nodes"].append({"type": "problem", "id": problem.get("id")})

    # Create objective node
    objective = await api_request("POST", "/nodes/", json_body={
        "canvas_id": canvas_id,
        "node_type": "objective",
        "name": args["objective"],
        "content": "",
        "position_x": 400,
        "position_y": 100,
    })
    if "error" in objective:
        return objective
    results["created_nodes"].append({"type": "objective", "id": objective.get("id")})

    # Connect problem -> objective
    conn1 = await api_request("POST", f"/nodes/{problem['id']}/connect/", json_body={
        "target_node_id": objective["id"],
    })
    results["connections"].append({"from": "problem", "to": "objective", "success": "error" not in conn1})

    # Create key result nodes
    for i, kr_text in enumerate(args.get("key_results", [])):
        kr = await api_request("POST", "/nodes/", json_body={
            "canvas_id": canvas_id,
            "node_type": "keyresult",
            "name": kr_text,
            "content": "",
            "position_x": 700,
            "position_y": 100 + (i * 150),
        })
        if "error" not in kr:
            results["created_nodes"].append({"type": "keyresult", "id": kr.get("id")})

            # Connect objective -> key result
            conn = await api_request("POST", f"/nodes/{objective['id']}/connect/", json_body={
                "target_node_id": kr["id"],
            })
            results["connections"].append({"from": "objective", "to": f"keyresult_{i}", "success": "error" not in conn})

    return results


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

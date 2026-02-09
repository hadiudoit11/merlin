"""
Canvas Agent API Endpoints

AI-powered canvas assistant for natural language canvas operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from app.core.database import get_session
from app.core.permissions import can_access_canvas
from app.models.user import User
from app.api.deps import get_current_user
from app.services.canvas_agent import canvas_agent, CanvasAgentError

router = APIRouter()


class AgentMessage(BaseModel):
    """A single message in the conversation."""
    role: str  # "user" or "assistant"
    content: Any  # String or list of content blocks


class AgentChatRequest(BaseModel):
    """Request to send a message to the canvas agent."""
    message: str
    conversation_history: Optional[List[Dict[str, Any]]] = None


class AgentAction(BaseModel):
    """An action taken by the agent."""
    type: str
    description: str
    status: str  # "pending", "complete", "error"
    params: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None


class AgentChatResponse(BaseModel):
    """Response from the canvas agent."""
    response: str
    actions: List[AgentAction]


@router.post("/{canvas_id}/chat", response_model=AgentChatResponse)
async def chat_with_agent(
    canvas_id: int,
    request: AgentChatRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Send a message to the canvas agent.

    The agent can:
    - Create nodes (problems, objectives, key results, metrics, docs)
    - Connect nodes together
    - Update node names and content
    - Delete nodes
    - Query the current canvas state

    Example messages:
    - "Create a problem statement about user retention"
    - "Add an objective to improve onboarding"
    - "Connect the problem to the objective"
    - "What's on my canvas?"
    """
    # Check canvas access
    has_access = await can_access_canvas(session, current_user.id, canvas_id)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this canvas"
        )

    try:
        # Convert conversation history to proper format if provided
        history = None
        if request.conversation_history:
            # Filter to just user/assistant messages for Claude
            history = [
                msg for msg in request.conversation_history
                if msg.get("role") in ("user", "assistant")
            ]

        result = await canvas_agent.chat(
            session=session,
            canvas_id=canvas_id,
            user_id=current_user.id,
            user_message=request.message,
            conversation_history=history,
        )

        return AgentChatResponse(
            response=result["response"],
            actions=[
                AgentAction(
                    type=action["type"],
                    description=action["description"],
                    status=action["status"],
                    params=action["params"],
                    result=action.get("result"),
                )
                for action in result.get("actions", [])
            ],
        )

    except CanvasAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent error: {str(e)}"
        )

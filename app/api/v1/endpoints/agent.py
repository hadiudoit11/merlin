"""
Canvas / Lifecycle Agent API Endpoints

Provides:
  POST /agent/chat                      - Global chat (no canvas yet), SSE streaming
  POST /agent/{canvas_id}/chat          - Canvas-specific chat (existing behaviour)
  POST /agent/session/{session_id}/upload  - Upload a file to a session
  GET  /agent/session/{session_id}      - Get session history
"""
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_session
from app.core.permissions import can_access_canvas
from app.models.user import User
from app.models.agent_session import AgentSession
from app.api.deps import get_current_user
from app.services.canvas_agent import canvas_agent, CanvasAgentError

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AgentMessage(BaseModel):
    role: str
    content: Any


class AgentChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    conversation_history: Optional[List[Dict[str, Any]]] = None
    organization_id: Optional[int] = None


class AgentAction(BaseModel):
    type: str
    description: str
    status: str
    params: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None


class AgentChatResponse(BaseModel):
    response: str
    actions: List[AgentAction]
    session_id: Optional[str] = None
    canvas_id: Optional[int] = None


class AgentSessionResponse(BaseModel):
    id: str
    canvas_id: Optional[int]
    messages: List[Dict[str, Any]]
    context_summary: Optional[str]
    attached_files: List[Dict[str, Any]]
    created_at: str
    updated_at: Optional[str]


# ---------------------------------------------------------------------------
# Helper: get or create AgentSession
# ---------------------------------------------------------------------------

async def _get_or_create_session(
    db: AsyncSession,
    user_id: int,
    session_id: Optional[str],
    canvas_id: Optional[int] = None,
    organization_id: Optional[int] = None,
) -> AgentSession:
    """Load existing session or create a fresh one."""
    if session_id:
        agent_session = await db.get(AgentSession, session_id)
        if agent_session and agent_session.user_id == user_id:
            return agent_session

    # Create new session
    new_session = AgentSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        canvas_id=canvas_id,
        organization_id=organization_id,
        messages=[],
        attached_files=[],
    )
    db.add(new_session)
    await db.flush()
    return new_session


# ---------------------------------------------------------------------------
# POST /agent/chat  — global SSE streaming endpoint
# ---------------------------------------------------------------------------

@router.post("/chat")
async def global_chat_stream(
    request: AgentChatRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Global lifecycle agent chat — no canvas required yet.

    Returns a Server-Sent Events stream:
      data: {"type":"text","content":"..."}
      data: {"type":"action","action":"create_canvas","status":"running","params":{...}}
      data: {"type":"action","action":"create_canvas","status":"done","result":{...},"description":"..."}
      data: {"type":"done","canvas_id":5,"session_id":"uuid"}

    The frontend should open an EventSource or fetch with streaming and display
    each event as it arrives.
    """
    # Load/create session
    agent_session = await _get_or_create_session(
        db,
        current_user.id,
        request.session_id,
        organization_id=request.organization_id,
    )

    # Inject attachment context into the message if files are attached
    user_message = request.message
    if agent_session.attached_files:
        file_summaries = "\n".join(
            f"- [{f['name']}]: {f.get('summary', 'no summary')}"
            for f in agent_session.attached_files
        )
        user_message = (
            f"{user_message}\n\n"
            f"[Context from uploaded files:]\n{file_summaries}"
        )

    # Recover conversation history from session
    history = agent_session.get_claude_messages()

    # Shared mutable context for canvas_id propagation
    session_context: Dict[str, Any] = {"canvas_id": agent_session.canvas_id}

    async def event_generator():
        all_actions = []
        response_text_parts = []
        final_canvas_id = agent_session.canvas_id

        try:
            async for event in canvas_agent.chat_stream(
                session=db,
                canvas_id=agent_session.canvas_id,
                user_id=current_user.id,
                user_message=user_message,
                conversation_history=history,
                session_context=session_context,
            ):
                if event["type"] == "text":
                    response_text_parts.append(event["content"])
                elif event["type"] == "action" and event["status"] == "done":
                    all_actions.append(event)
                elif event["type"] == "done":
                    final_canvas_id = event.get("canvas_id") or session_context.get("canvas_id")
                    event["session_id"] = agent_session.id
                    event["canvas_id"] = final_canvas_id

                yield f"data: {json.dumps(event)}\n\n"

            # Persist to session after streaming completes
            full_response = "\n".join(response_text_parts)
            agent_session.add_message("user", request.message)
            agent_session.add_message("assistant", full_response, actions=all_actions)

            if final_canvas_id and not agent_session.canvas_id:
                agent_session.canvas_id = final_canvas_id

            await db.commit()

        except CanvasAgentError as exc:
            error_event = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(error_event)}\n\n"
        except Exception as exc:
            error_event = {"type": "error", "message": f"Agent error: {exc}"}
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /agent/{canvas_id}/chat  — canvas-specific chat (non-streaming, legacy)
# ---------------------------------------------------------------------------

@router.post("/{canvas_id}/chat", response_model=AgentChatResponse)
async def chat_with_canvas_agent(
    canvas_id: int,
    request: AgentChatRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Send a message to the canvas agent for a specific canvas.

    Existing behaviour preserved.  Now also accepts an optional session_id
    to maintain persistent history.
    """
    has_access = await can_access_canvas(db, current_user.id, canvas_id)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this canvas",
        )

    agent_session = await _get_or_create_session(
        db, current_user.id, request.session_id, canvas_id=canvas_id
    )

    history = agent_session.get_claude_messages()

    try:
        result = await canvas_agent.chat(
            session=db,
            canvas_id=canvas_id,
            user_id=current_user.id,
            user_message=request.message,
            conversation_history=history,
        )

        # Persist messages
        agent_session.add_message("user", request.message)
        agent_session.add_message("assistant", result["response"], actions=result.get("actions", []))
        await db.commit()

        return AgentChatResponse(
            response=result["response"],
            actions=[
                AgentAction(
                    type=a["type"],
                    description=a["description"],
                    status=a["status"],
                    params=a["params"],
                    result=a.get("result"),
                )
                for a in result.get("actions", [])
            ],
            session_id=agent_session.id,
            canvas_id=canvas_id,
        )

    except CanvasAgentError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent error: {exc}",
        )


# ---------------------------------------------------------------------------
# POST /agent/session/{session_id}/upload  — file upload
# ---------------------------------------------------------------------------

@router.post("/session/{session_id}/upload")
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a file (PDF, image, txt, docx) to an agent session.

    The file's text content is extracted and stored in the session so the
    agent can reference it in subsequent messages.

    Returns: {"name": "...", "type": "...", "summary": "...", "text_length": N}
    """
    agent_session = await db.get(AgentSession, session_id)
    if not agent_session or agent_session.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    raw_bytes = await file.read()
    content_type = file.content_type or ""
    filename = file.filename or "upload"
    text_content = ""
    summary = ""

    # --- Text extraction ---
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        text_content = _extract_pdf_text(raw_bytes)
    elif content_type in ("text/plain",) or filename.lower().endswith((".txt", ".md")):
        text_content = raw_bytes.decode("utf-8", errors="replace")
    elif content_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ) or filename.lower().endswith(".docx"):
        text_content = _extract_docx_text(raw_bytes)
    elif content_type.startswith("image/"):
        # For images just note the filename — vision analysis would need a separate call
        text_content = f"[Image file: {filename}]"
    else:
        text_content = raw_bytes.decode("utf-8", errors="replace")[:5000]

    # Truncate to reasonable size
    text_content = text_content[:8000]

    # Build a simple summary (first 300 chars of extracted text)
    summary = text_content[:300].strip().replace("\n", " ") if text_content else "No content extracted"

    # Append to session's attached_files list
    attached = list(agent_session.attached_files or [])
    attached.append({
        "name": filename,
        "type": content_type,
        "text_content": text_content,
        "summary": summary,
        "uploaded_at": datetime.utcnow().isoformat(),
    })
    agent_session.attached_files = attached
    agent_session.updated_at = datetime.utcnow()
    await db.commit()

    return {
        "name": filename,
        "type": content_type,
        "summary": summary,
        "text_length": len(text_content),
    }


# ---------------------------------------------------------------------------
# GET /agent/session/{session_id}  — retrieve session history
# ---------------------------------------------------------------------------

@router.get("/session/{session_id}", response_model=AgentSessionResponse)
async def get_session_history(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get the full history of an agent session."""
    agent_session = await db.get(AgentSession, session_id)
    if not agent_session or agent_session.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return AgentSessionResponse(
        id=agent_session.id,
        canvas_id=agent_session.canvas_id,
        messages=agent_session.messages or [],
        context_summary=agent_session.context_summary,
        attached_files=[
            {"name": f["name"], "type": f["type"], "summary": f.get("summary", "")}
            for f in (agent_session.attached_files or [])
        ],
        created_at=agent_session.created_at.isoformat(),
        updated_at=agent_session.updated_at.isoformat() if agent_session.updated_at else None,
    )


# ---------------------------------------------------------------------------
# Helpers: file text extraction
# ---------------------------------------------------------------------------

def _extract_pdf_text(raw_bytes: bytes) -> str:
    """Extract text from a PDF using pypdf (if available)."""
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    except ImportError:
        return "[PDF extraction requires pypdf — install with: pip install pypdf]"
    except Exception as exc:
        return f"[PDF extraction error: {exc}]"


def _extract_docx_text(raw_bytes: bytes) -> str:
    """Extract text from a .docx file using python-docx (if available)."""
    try:
        import io
        from docx import Document
        doc = Document(io.BytesIO(raw_bytes))
        paragraphs = [p.text for p in doc.paragraphs]
        return "\n".join(paragraphs)
    except ImportError:
        return "[DOCX extraction requires python-docx — install with: pip install python-docx]"
    except Exception as exc:
        return f"[DOCX extraction error: {exc}]"

"""
AgentSession Model - Persistent conversation context for the lifecycle agent.

Stores conversation history in Postgres and enables the agent to maintain
context across canvas creation and ongoing lifecycle management.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, ForeignKey, Text, DateTime, JSON
from sqlalchemy.orm import relationship

from app.core.database import Base


class AgentSession(Base):
    """
    Persistent session for the lifecycle agent.

    Stores full conversation history (messages), extracted file context,
    and a rolling summary so the agent can pick up where it left off.
    """
    __tablename__ = "agent_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    canvas_id = Column(Integer, ForeignKey("canvases.id"), nullable=True, index=True)
    organization_id = Column(Integer, nullable=True)

    # Full conversation history as list of {role, content, timestamp, actions}
    messages = Column(JSON, default=list, nullable=False)

    # AI-generated rolling summary — updated after each exchange
    context_summary = Column(Text, nullable=True)

    # Extracted text from uploaded files: [{name, type, text_content, summary}]
    attached_files = Column(JSON, default=list, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    canvas = relationship("Canvas", foreign_keys=[canvas_id])

    def add_message(self, role: str, content, actions: list = None):
        """Append a message to the session history."""
        if self.messages is None:
            self.messages = []
        self.messages = self.messages + [{
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
            "actions": actions or [],
        }]
        self.updated_at = datetime.utcnow()

    def get_claude_messages(self) -> list:
        """Return messages formatted for the Claude API (role + content only)."""
        result = []
        for msg in (self.messages or []):
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                result.append({"role": role, "content": content})
        return result

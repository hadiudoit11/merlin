from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
from enum import Enum
import secrets

from app.core.database import Base


class MCPActionStatus(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"
    PENDING = "pending"


class MCPToken(Base):
    """MCP access tokens for Claude integration"""
    __tablename__ = "mcp_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Token (stored hashed for security)
    token_hash = Column(String(64), unique=True, index=True)
    token_prefix = Column(String(8))  # First 8 chars for identification

    # Metadata
    name = Column(String(100))  # "Work Laptop", "Home PC"

    # Scopes and permissions
    scopes = Column(JSON, default=list)  # ["canvas:read", "canvas:write", ...]
    allowed_canvas_ids = Column(JSON, nullable=True)  # null = all canvases

    # Expiration
    expires_at = Column(DateTime, nullable=True)  # null = no expiration

    # Status
    is_active = Column(Boolean, default=True)
    revoked_at = Column(DateTime, nullable=True)

    # Usage tracking
    last_used_at = Column(DateTime, nullable=True)
    last_ip = Column(String(45), nullable=True)
    use_count = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", backref="mcp_tokens")
    audit_logs = relationship("MCPAuditLog", back_populates="token")

    @classmethod
    def generate_token(cls) -> tuple[str, str, str]:
        """Generate a new token, returns (raw_token, token_hash, token_prefix)"""
        import hashlib
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_prefix = raw_token[:8]
        return raw_token, token_hash, token_prefix

    @classmethod
    def hash_token(cls, raw_token: str) -> str:
        """Hash a raw token for comparison"""
        import hashlib
        return hashlib.sha256(raw_token.encode()).hexdigest()


class MCPAuditLog(Base):
    """Audit log for all MCP actions"""
    __tablename__ = "mcp_audit_logs"

    id = Column(Integer, primary_key=True, index=True)

    # Who
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_id = Column(Integer, ForeignKey("mcp_tokens.id"), nullable=True)

    # What
    action = Column(String(50), nullable=False)  # "tool_call", "connect", "disconnect"
    tool_name = Column(String(100), nullable=True)
    arguments = Column(JSON, nullable=True)

    # Result
    status = Column(String(20), default=MCPActionStatus.SUCCESS.value)
    error_message = Column(Text, nullable=True)
    result_summary = Column(Text, nullable=True)  # Brief description of result

    # Context
    canvas_id = Column(Integer, ForeignKey("canvases.id"), nullable=True)
    node_id = Column(Integer, nullable=True)

    # Request metadata
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    session_id = Column(String(64), nullable=True)  # Track MCP session

    # Timing
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    duration_ms = Column(Integer, nullable=True)  # How long the action took

    # Relationships
    user = relationship("User", backref="mcp_audit_logs")
    token = relationship("MCPToken", back_populates="audit_logs")
    canvas = relationship("Canvas", backref="mcp_audit_logs")


# Available MCP scopes
MCP_SCOPES = {
    "canvas:read": "View canvases and their structure",
    "canvas:write": "Create and modify canvases",
    "node:read": "View node content and connections",
    "node:write": "Create and modify nodes",
    "node:delete": "Delete nodes",
    "okr:read": "View objectives, key results, and metrics",
    "okr:write": "Create and modify OKRs",
    "task:read": "View tasks",
    "task:write": "Create and modify tasks",
    "template:read": "View templates",
    "integration:read": "View integration status",
}

# Map tools to required scopes
TOOL_REQUIRED_SCOPES = {
    "list_canvases": ["canvas:read"],
    "get_canvas": ["canvas:read"],
    "create_canvas": ["canvas:write"],
    "update_canvas": ["canvas:write"],
    "delete_canvas": ["canvas:write"],
    "list_nodes": ["node:read"],
    "get_node": ["node:read"],
    "get_node_context": ["node:read"],
    "create_node": ["node:write"],
    "update_node": ["node:write"],
    "delete_node": ["node:delete"],
    "connect_nodes": ["node:write"],
    "list_objectives": ["okr:read"],
    "get_objective": ["okr:read"],
    "create_objective": ["okr:write"],
    "list_tasks": ["task:read"],
    "create_task": ["task:write"],
    "update_task": ["task:write"],
    "list_templates": ["template:read"],
    "get_template": ["template:read"],
}

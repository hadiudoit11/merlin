"""
MCP (Model Context Protocol) Service

Handles MCP token management, authentication, and audit logging.
"""

from datetime import datetime, timedelta
from typing import Optional
import time

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp import (
    MCPToken,
    MCPAuditLog,
    MCPActionStatus,
    MCP_SCOPES,
    TOOL_REQUIRED_SCOPES,
)
from app.models.user import User


class MCPService:
    """Service for MCP token and audit management"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ==================== Token Management ====================

    async def create_token(
        self,
        user_id: int,
        name: str,
        scopes: list[str] | None = None,
        allowed_canvas_ids: list[int] | None = None,
        expires_in_days: int | None = None,
    ) -> tuple[str, MCPToken]:
        """
        Create a new MCP token for a user.
        Returns (raw_token, token_object) - raw_token is only shown once!
        """
        # Default to read-only scopes if none specified
        if scopes is None:
            scopes = ["canvas:read", "node:read", "okr:read", "task:read", "template:read"]

        # Validate scopes
        invalid_scopes = set(scopes) - set(MCP_SCOPES.keys())
        if invalid_scopes:
            raise ValueError(f"Invalid scopes: {invalid_scopes}")

        # Generate token
        raw_token, token_hash, token_prefix = MCPToken.generate_token()

        # Calculate expiration
        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

        # Create token record
        token = MCPToken(
            user_id=user_id,
            token_hash=token_hash,
            token_prefix=token_prefix,
            name=name,
            scopes=scopes,
            allowed_canvas_ids=allowed_canvas_ids,
            expires_at=expires_at,
        )

        self.db.add(token)
        await self.db.commit()
        await self.db.refresh(token)

        # Log token creation
        await self.log_action(
            user_id=user_id,
            token_id=token.id,
            action="token_created",
            result_summary=f"Created MCP token '{name}' with scopes: {', '.join(scopes)}",
        )

        return raw_token, token

    async def validate_token(
        self,
        raw_token: str,
        ip_address: str | None = None,
    ) -> tuple[User, MCPToken] | None:
        """
        Validate an MCP token and return the user and token if valid.
        Returns None if invalid.
        """
        token_hash = MCPToken.hash_token(raw_token)

        result = await self.db.execute(
            select(MCPToken)
            .where(
                and_(
                    MCPToken.token_hash == token_hash,
                    MCPToken.is_active == True,
                    MCPToken.revoked_at.is_(None),
                )
            )
        )
        token = result.scalar_one_or_none()

        if not token:
            return None

        # Check expiration
        if token.expires_at and token.expires_at < datetime.utcnow():
            return None

        # Get user
        user = await self.db.get(User, token.user_id)
        if not user or not user.is_active:
            return None

        # Update usage stats
        token.last_used_at = datetime.utcnow()
        token.last_ip = ip_address
        token.use_count += 1
        await self.db.commit()

        return user, token

    async def list_tokens(self, user_id: int) -> list[MCPToken]:
        """List all active tokens for a user"""
        result = await self.db.execute(
            select(MCPToken)
            .where(
                and_(
                    MCPToken.user_id == user_id,
                    MCPToken.is_active == True,
                    MCPToken.revoked_at.is_(None),
                )
            )
            .order_by(desc(MCPToken.created_at))
        )
        return list(result.scalars().all())

    async def revoke_token(self, token_id: int, user_id: int) -> bool:
        """Revoke an MCP token"""
        result = await self.db.execute(
            select(MCPToken)
            .where(
                and_(
                    MCPToken.id == token_id,
                    MCPToken.user_id == user_id,
                )
            )
        )
        token = result.scalar_one_or_none()

        if not token:
            return False

        token.is_active = False
        token.revoked_at = datetime.utcnow()
        await self.db.commit()

        # Log revocation
        await self.log_action(
            user_id=user_id,
            token_id=token_id,
            action="token_revoked",
            result_summary=f"Revoked MCP token '{token.name}'",
        )

        return True

    async def check_scope(
        self,
        token: MCPToken,
        tool_name: str,
    ) -> bool:
        """Check if token has required scope for a tool"""
        required_scopes = TOOL_REQUIRED_SCOPES.get(tool_name, [])
        token_scopes = set(token.scopes or [])
        return all(scope in token_scopes for scope in required_scopes)

    async def check_canvas_access(
        self,
        token: MCPToken,
        canvas_id: int,
    ) -> bool:
        """Check if token has access to a specific canvas"""
        if token.allowed_canvas_ids is None:
            return True  # Access to all canvases
        return canvas_id in token.allowed_canvas_ids

    # ==================== Audit Logging ====================

    async def log_action(
        self,
        user_id: int,
        action: str,
        token_id: int | None = None,
        tool_name: str | None = None,
        arguments: dict | None = None,
        status: MCPActionStatus = MCPActionStatus.SUCCESS,
        error_message: str | None = None,
        result_summary: str | None = None,
        canvas_id: int | None = None,
        node_id: int | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        session_id: str | None = None,
        duration_ms: int | None = None,
    ) -> MCPAuditLog:
        """Log an MCP action"""
        log = MCPAuditLog(
            user_id=user_id,
            token_id=token_id,
            action=action,
            tool_name=tool_name,
            arguments=arguments,
            status=status.value if isinstance(status, MCPActionStatus) else status,
            error_message=error_message,
            result_summary=result_summary,
            canvas_id=canvas_id,
            node_id=node_id,
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=session_id,
            duration_ms=duration_ms,
        )

        self.db.add(log)
        await self.db.commit()
        await self.db.refresh(log)

        return log

    async def get_audit_logs(
        self,
        user_id: int | None = None,
        canvas_id: int | None = None,
        token_id: int | None = None,
        action: str | None = None,
        status: MCPActionStatus | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MCPAuditLog]:
        """Query audit logs with filters"""
        query = select(MCPAuditLog)

        conditions = []
        if user_id:
            conditions.append(MCPAuditLog.user_id == user_id)
        if canvas_id:
            conditions.append(MCPAuditLog.canvas_id == canvas_id)
        if token_id:
            conditions.append(MCPAuditLog.token_id == token_id)
        if action:
            conditions.append(MCPAuditLog.action == action)
        if status:
            conditions.append(MCPAuditLog.status == status.value)
        if since:
            conditions.append(MCPAuditLog.created_at >= since)

        if conditions:
            query = query.where(and_(*conditions))

        query = query.order_by(desc(MCPAuditLog.created_at)).offset(offset).limit(limit)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_audit_stats(
        self,
        user_id: int,
        since: datetime | None = None,
    ) -> dict:
        """Get audit statistics for a user"""
        if since is None:
            since = datetime.utcnow() - timedelta(days=30)

        logs = await self.get_audit_logs(user_id=user_id, since=since, limit=10000)

        stats = {
            "total_actions": len(logs),
            "by_status": {},
            "by_tool": {},
            "by_canvas": {},
            "recent_errors": [],
        }

        for log in logs:
            # Count by status
            stats["by_status"][log.status] = stats["by_status"].get(log.status, 0) + 1

            # Count by tool
            if log.tool_name:
                stats["by_tool"][log.tool_name] = stats["by_tool"].get(log.tool_name, 0) + 1

            # Count by canvas
            if log.canvas_id:
                stats["by_canvas"][log.canvas_id] = stats["by_canvas"].get(log.canvas_id, 0) + 1

            # Collect recent errors
            if log.status == MCPActionStatus.ERROR.value and len(stats["recent_errors"]) < 10:
                stats["recent_errors"].append({
                    "id": log.id,
                    "action": log.action,
                    "tool_name": log.tool_name,
                    "error_message": log.error_message,
                    "created_at": log.created_at.isoformat(),
                })

        return stats


class MCPToolLogger:
    """Context manager for logging MCP tool calls with timing"""

    def __init__(
        self,
        mcp_service: MCPService,
        user_id: int,
        token_id: int,
        tool_name: str,
        arguments: dict,
        canvas_id: int | None = None,
        node_id: int | None = None,
        ip_address: str | None = None,
        session_id: str | None = None,
    ):
        self.mcp_service = mcp_service
        self.user_id = user_id
        self.token_id = token_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.canvas_id = canvas_id
        self.node_id = node_id
        self.ip_address = ip_address
        self.session_id = session_id
        self.start_time = None
        self.log = None

    async def __aenter__(self):
        self.start_time = time.time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration_ms = int((time.time() - self.start_time) * 1000)

        if exc_type:
            # Log error
            self.log = await self.mcp_service.log_action(
                user_id=self.user_id,
                token_id=self.token_id,
                action="tool_call",
                tool_name=self.tool_name,
                arguments=self.arguments,
                status=MCPActionStatus.ERROR,
                error_message=str(exc_val),
                canvas_id=self.canvas_id,
                node_id=self.node_id,
                ip_address=self.ip_address,
                session_id=self.session_id,
                duration_ms=duration_ms,
            )
        else:
            # Log success
            self.log = await self.mcp_service.log_action(
                user_id=self.user_id,
                token_id=self.token_id,
                action="tool_call",
                tool_name=self.tool_name,
                arguments=self.arguments,
                status=MCPActionStatus.SUCCESS,
                canvas_id=self.canvas_id,
                node_id=self.node_id,
                ip_address=self.ip_address,
                session_id=self.session_id,
                duration_ms=duration_ms,
            )

        return False  # Don't suppress exceptions

    async def set_result(self, result_summary: str):
        """Set the result summary after successful execution"""
        if self.log:
            self.log.result_summary = result_summary
            await self.mcp_service.db.commit()

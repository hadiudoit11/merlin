"""
MCP Token Authentication, Scope Enforcement, and Rate Limiting.

This module provides:
- Token validation with scope checking
- Canvas-level access control
- Rate limiting per token
- Audit logging for MCP actions
"""

import time
import hashlib
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import defaultdict
from functools import wraps

from fastapi import HTTPException, status, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_session
from app.models.mcp import MCPToken, MCPAuditLog, TOOL_REQUIRED_SCOPES, MCP_SCOPES
from app.models.user import User


# ============ Rate Limiting ============

class RateLimiter:
    """
    Simple in-memory sliding window rate limiter.

    For production, consider using Redis for distributed rate limiting.
    """

    def __init__(self):
        # token_hash -> list of request timestamps
        self._requests: Dict[str, List[float]] = defaultdict(list)

        # Default limits
        self.requests_per_minute = 60
        self.requests_per_hour = 500

    def _cleanup_old_requests(self, token_hash: str, window_seconds: int):
        """Remove requests older than the window."""
        cutoff = time.time() - window_seconds
        self._requests[token_hash] = [
            ts for ts in self._requests[token_hash] if ts > cutoff
        ]

    def check_rate_limit(self, token_hash: str) -> tuple[bool, Optional[str]]:
        """
        Check if the request is within rate limits.

        Returns:
            (allowed, error_message)
        """
        now = time.time()

        # Check per-minute limit
        self._cleanup_old_requests(token_hash, 60)
        minute_count = len(self._requests[token_hash])
        if minute_count >= self.requests_per_minute:
            return False, f"Rate limit exceeded: {self.requests_per_minute} requests per minute"

        # Check per-hour limit
        self._cleanup_old_requests(token_hash, 3600)
        hour_count = len(self._requests[token_hash])
        if hour_count >= self.requests_per_hour:
            return False, f"Rate limit exceeded: {self.requests_per_hour} requests per hour"

        # Record this request
        self._requests[token_hash].append(now)

        return True, None

    def get_usage(self, token_hash: str) -> Dict[str, int]:
        """Get current usage stats for a token."""
        self._cleanup_old_requests(token_hash, 3600)

        minute_cutoff = time.time() - 60
        minute_count = len([ts for ts in self._requests[token_hash] if ts > minute_cutoff])
        hour_count = len(self._requests[token_hash])

        return {
            "requests_last_minute": minute_count,
            "requests_last_hour": hour_count,
            "limit_per_minute": self.requests_per_minute,
            "limit_per_hour": self.requests_per_hour,
        }


# Global rate limiter instance
rate_limiter = RateLimiter()


# ============ Canvas Scope Definitions ============

# Scopes that are canvas-only (safe for limited access)
CANVAS_SCOPES = {
    "canvas:read": "View canvases and their structure",
    "canvas:write": "Create and modify canvases",
    "node:read": "View node content and connections",
    "node:write": "Create and modify nodes",
    "node:delete": "Delete nodes",
    "okr:read": "View objectives, key results, and metrics",
    "okr:write": "Create and modify OKRs",
}

# Scopes that require additional permissions
EXTENDED_SCOPES = {
    "task:read": "View tasks",
    "task:write": "Create and modify tasks",
    "template:read": "View templates",
    "integration:read": "View integration status",
}


# ============ Scope Validation ============

def check_scopes(token_scopes: List[str], required_scopes: List[str]) -> bool:
    """Check if token has all required scopes."""
    if not required_scopes:
        return True
    return all(scope in token_scopes for scope in required_scopes)


def get_required_scopes_for_endpoint(endpoint_path: str, method: str) -> List[str]:
    """
    Determine required scopes based on endpoint path and method.

    This is used for HTTP API access (not MCP tool calls).
    """
    path = endpoint_path.lower()

    # Canvas endpoints
    if "/canvases" in path:
        if method in ("GET", "HEAD"):
            return ["canvas:read"]
        elif method in ("POST", "PUT", "PATCH"):
            return ["canvas:write"]
        elif method == "DELETE":
            return ["canvas:write"]

    # Node endpoints
    if "/nodes" in path:
        if method in ("GET", "HEAD"):
            return ["node:read"]
        elif method in ("POST", "PUT", "PATCH"):
            return ["node:write"]
        elif method == "DELETE":
            return ["node:delete"]

    # OKR endpoints
    if "/okrs" in path or "/objectives" in path or "/keyresults" in path or "/metrics" in path:
        if method in ("GET", "HEAD"):
            return ["okr:read"]
        else:
            return ["okr:write"]

    # Task endpoints
    if "/tasks" in path:
        if method in ("GET", "HEAD"):
            return ["task:read"]
        else:
            return ["task:write"]

    # Templates
    if "/templates" in path:
        return ["template:read"]

    # Auth endpoints are always allowed
    if "/auth" in path:
        return []

    # Token management endpoints
    if "/tokens" in path:
        return []

    # Default: no specific scope required
    return []


# ============ Canvas Access Control ============

def check_canvas_access(token: MCPToken, canvas_id: int) -> bool:
    """Check if token has access to a specific canvas."""
    # If no canvas restrictions, allow all
    if token.allowed_canvas_ids is None:
        return True

    # Check if canvas_id is in allowed list
    return canvas_id in token.allowed_canvas_ids


# ============ Token Validation with Scope & Rate Limit ============

async def validate_mcp_token_with_scopes(
    raw_token: str,
    session: AsyncSession,
    request: Optional[Request] = None,
    required_scopes: Optional[List[str]] = None,
    canvas_id: Optional[int] = None,
) -> tuple[Optional[User], Optional[MCPToken], Optional[str]]:
    """
    Validate an MCP token with scope and canvas checks.

    Returns:
        (user, token, error_message)
        If validation fails, user and token are None, error_message explains why.
    """
    token_hash = MCPToken.hash_token(raw_token)

    # Check rate limit first
    allowed, rate_error = rate_limiter.check_rate_limit(token_hash)
    if not allowed:
        return None, None, rate_error

    # Find token
    result = await session.execute(
        select(MCPToken)
        .where(MCPToken.token_hash == token_hash)
        .where(MCPToken.is_active == True)
        .where(MCPToken.revoked_at.is_(None))
    )
    mcp_token = result.scalar_one_or_none()

    if not mcp_token:
        return None, None, "Invalid or expired API token"

    # Check expiration
    if mcp_token.expires_at and datetime.utcnow() > mcp_token.expires_at:
        return None, None, "Token has expired"

    # Check scopes
    if required_scopes:
        token_scopes = mcp_token.scopes or []
        if not check_scopes(token_scopes, required_scopes):
            missing = set(required_scopes) - set(token_scopes)
            return None, None, f"Token missing required scopes: {', '.join(missing)}"

    # Check canvas access
    if canvas_id is not None:
        if not check_canvas_access(mcp_token, canvas_id):
            return None, None, f"Token does not have access to canvas {canvas_id}"

    # Update usage stats
    mcp_token.last_used_at = datetime.utcnow()
    mcp_token.last_ip = request.client.host if request and request.client else None
    mcp_token.use_count = (mcp_token.use_count or 0) + 1
    await session.commit()

    # Get user
    user_result = await session.execute(
        select(User).where(User.id == mcp_token.user_id)
    )
    user = user_result.scalar_one_or_none()

    if not user:
        return None, None, "User not found"

    if not user.is_active:
        return None, None, "User account is disabled"

    return user, mcp_token, None


# ============ Audit Logging ============

async def log_mcp_action(
    session: AsyncSession,
    user_id: int,
    token_id: Optional[int],
    action: str,
    tool_name: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None,
    status: str = "success",
    error_message: Optional[str] = None,
    canvas_id: Optional[int] = None,
    node_id: Optional[int] = None,
    request: Optional[Request] = None,
    duration_ms: Optional[int] = None,
):
    """Log an MCP action for auditing."""
    log_entry = MCPAuditLog(
        user_id=user_id,
        token_id=token_id,
        action=action,
        tool_name=tool_name,
        arguments=arguments,
        status=status,
        error_message=error_message,
        canvas_id=canvas_id,
        node_id=node_id,
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500] if request else None,
        duration_ms=duration_ms,
    )
    session.add(log_entry)
    await session.commit()


# ============ FastAPI Dependencies ============

class MCPAuthDependency:
    """
    FastAPI dependency for MCP token authentication with scope checking.

    Usage:
        @router.get("/canvases/")
        async def list_canvases(
            auth: MCPAuth = Depends(MCPAuthDependency(scopes=["canvas:read"]))
        ):
            user = auth.user
            # ...
    """

    def __init__(self, scopes: Optional[List[str]] = None):
        self.required_scopes = scopes or []

    async def __call__(
        self,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> "MCPAuth":
        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:]  # Remove "Bearer " prefix

        # Check if it's an MCP token (not a JWT)
        if "." in token:
            # This is a JWT, not an MCP token - let regular auth handle it
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This endpoint requires an MCP token, not a JWT",
            )

        # Validate with scope checking
        user, mcp_token, error = await validate_mcp_token_with_scopes(
            raw_token=token,
            session=session,
            request=request,
            required_scopes=self.required_scopes,
        )

        if error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error,
            )

        return MCPAuth(
            user=user,
            token=mcp_token,
            session=session,
            request=request,
        )


class MCPAuth:
    """Container for MCP authentication context."""

    def __init__(
        self,
        user: User,
        token: MCPToken,
        session: AsyncSession,
        request: Request,
    ):
        self.user = user
        self.token = token
        self.session = session
        self.request = request

    def has_scope(self, scope: str) -> bool:
        """Check if the token has a specific scope."""
        return scope in (self.token.scopes or [])

    def has_canvas_access(self, canvas_id: int) -> bool:
        """Check if the token has access to a specific canvas."""
        return check_canvas_access(self.token, canvas_id)

    def check_canvas_access(self, canvas_id: int):
        """Check canvas access and raise HTTPException if denied."""
        if not self.has_canvas_access(canvas_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Token does not have access to canvas {canvas_id}",
            )

    async def log_action(
        self,
        action: str,
        tool_name: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        status: str = "success",
        error_message: Optional[str] = None,
        canvas_id: Optional[int] = None,
        node_id: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ):
        """Log an action for this token."""
        await log_mcp_action(
            session=self.session,
            user_id=self.user.id,
            token_id=self.token.id,
            action=action,
            tool_name=tool_name,
            arguments=arguments,
            status=status,
            error_message=error_message,
            canvas_id=canvas_id,
            node_id=node_id,
            request=self.request,
            duration_ms=duration_ms,
        )

    def get_rate_limit_status(self) -> Dict[str, int]:
        """Get current rate limit usage for this token."""
        return rate_limiter.get_usage(self.token.token_hash)

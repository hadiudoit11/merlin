"""
MCP (Model Context Protocol) API Endpoints

Token management and audit log viewing for Claude integration.
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.api.deps import get_current_user
from app.models.user import User
from app.models.mcp import MCP_SCOPES, MCPActionStatus
from app.services.mcp_service import MCPService


router = APIRouter(prefix="/mcp", tags=["MCP Integration"])


# ==================== Schemas ====================

class TokenCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Name for this token")
    scopes: list[str] | None = Field(None, description="Permission scopes (defaults to read-only)")
    allowed_canvas_ids: list[int] | None = Field(None, description="Specific canvas IDs (null = all)")
    expires_in_days: int | None = Field(None, ge=1, le=365, description="Token expiration in days")


class TokenResponse(BaseModel):
    id: int
    name: str
    token_prefix: str
    scopes: list[str]
    allowed_canvas_ids: list[int] | None
    expires_at: datetime | None
    last_used_at: datetime | None
    use_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class TokenCreatedResponse(BaseModel):
    token: str  # Raw token - only shown once!
    token_id: int
    name: str
    scopes: list[str]
    mcp_url: str
    claude_config: dict


class AuditLogResponse(BaseModel):
    id: int
    action: str
    tool_name: str | None
    arguments: dict | None
    status: str
    error_message: str | None
    result_summary: str | None
    canvas_id: int | None
    ip_address: str | None
    duration_ms: int | None
    created_at: datetime

    class Config:
        from_attributes = True


class AuditStatsResponse(BaseModel):
    total_actions: int
    by_status: dict[str, int]
    by_tool: dict[str, int]
    by_canvas: dict[int, int]
    recent_errors: list[dict]


class ScopesResponse(BaseModel):
    scopes: dict[str, str]


# ==================== Endpoints ====================

@router.get("/scopes", response_model=ScopesResponse)
async def list_available_scopes():
    """List all available MCP permission scopes"""
    return {"scopes": MCP_SCOPES}


@router.post("/tokens", response_model=TokenCreatedResponse)
async def create_mcp_token(
    request: Request,
    body: TokenCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """
    Create a new MCP access token for Claude integration.

    The raw token is only returned once - save it immediately!
    """
    service = MCPService(db)

    try:
        raw_token, token = await service.create_token(
            user_id=user.id,
            name=body.name,
            scopes=body.scopes,
            allowed_canvas_ids=body.allowed_canvas_ids,
            expires_in_days=body.expires_in_days,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Build the MCP URL (use request to get base URL)
    base_url = str(request.base_url).rstrip("/")
    mcp_url = f"{base_url}/api/v1/mcp/sse"

    return TokenCreatedResponse(
        token=raw_token,
        token_id=token.id,
        name=token.name,
        scopes=token.scopes,
        mcp_url=mcp_url,
        claude_config={
            "mcpServers": {
                "typequest": {
                    "url": mcp_url,
                    "headers": {
                        "Authorization": f"Bearer {raw_token}"
                    }
                }
            }
        }
    )


@router.get("/tokens", response_model=list[TokenResponse])
async def list_mcp_tokens(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """List all active MCP tokens for the current user"""
    service = MCPService(db)
    tokens = await service.list_tokens(user.id)
    return tokens


@router.delete("/tokens/{token_id}")
async def revoke_mcp_token(
    token_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Revoke an MCP token"""
    service = MCPService(db)
    success = await service.revoke_token(token_id, user.id)

    if not success:
        raise HTTPException(404, "Token not found")

    return {"message": "Token revoked successfully"}


@router.get("/audit-logs", response_model=list[AuditLogResponse])
async def get_audit_logs(
    canvas_id: int | None = Query(None, description="Filter by canvas ID"),
    action: str | None = Query(None, description="Filter by action type"),
    status: str | None = Query(None, description="Filter by status"),
    since_hours: int | None = Query(24, ge=1, le=720, description="Hours to look back"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """
    Get MCP audit logs for the current user.

    Shows all Claude actions on your canvases.
    """
    service = MCPService(db)

    since = None
    if since_hours:
        since = datetime.utcnow() - timedelta(hours=since_hours)

    status_enum = None
    if status:
        try:
            status_enum = MCPActionStatus(status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")

    logs = await service.get_audit_logs(
        user_id=user.id,
        canvas_id=canvas_id,
        action=action,
        status=status_enum,
        since=since,
        limit=limit,
        offset=offset,
    )

    return logs


@router.get("/audit-logs/stats", response_model=AuditStatsResponse)
async def get_audit_stats(
    since_days: int = Query(30, ge=1, le=90, description="Days to analyze"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Get MCP usage statistics for the current user"""
    service = MCPService(db)

    since = datetime.utcnow() - timedelta(days=since_days)
    stats = await service.get_audit_stats(user.id, since)

    return stats


@router.get("/audit-logs/canvas/{canvas_id}", response_model=list[AuditLogResponse])
async def get_canvas_audit_logs(
    canvas_id: int,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Get recent MCP activity for a specific canvas"""
    service = MCPService(db)

    # TODO: Verify user has access to this canvas

    logs = await service.get_audit_logs(
        canvas_id=canvas_id,
        limit=limit,
    )

    return logs

"""API Token management endpoints for MCP and programmatic access."""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import Optional, List

from app.core.database import get_session
from app.core.mcp_auth import (
    validate_mcp_token_with_scopes,
    rate_limiter,
    CANVAS_SCOPES,
    EXTENDED_SCOPES,
)
from app.models.mcp import MCPToken, MCP_SCOPES
from app.models.user import User
from app.api.v1.endpoints.auth import get_current_user

router = APIRouter()

# Default scopes for new tokens (canvas-only for safety)
DEFAULT_SCOPES = list(CANVAS_SCOPES.keys())


class TokenCreate(BaseModel):
    """Request to create a new API token."""
    name: str
    scopes: Optional[List[str]] = None  # None = all scopes
    expires_in_days: Optional[int] = None  # None = never expires
    allowed_canvas_ids: Optional[List[int]] = None  # None = all canvases


class TokenResponse(BaseModel):
    """Response after creating a token (only time you see the full token)."""
    id: int
    name: str
    token: str  # Full token - only shown once!
    token_prefix: str
    scopes: List[str]
    created_at: datetime
    expires_at: Optional[datetime]

    class Config:
        from_attributes = True


class TokenListItem(BaseModel):
    """Token info for listing (no full token)."""
    id: int
    name: str
    token_prefix: str
    scopes: List[str]
    allowed_canvas_ids: Optional[List[int]]
    is_active: bool
    last_used_at: Optional[datetime]
    use_count: int
    created_at: datetime
    expires_at: Optional[datetime]

    class Config:
        from_attributes = True


class ScopesResponse(BaseModel):
    """Available scopes for tokens."""
    scopes: dict
    canvas_scopes: dict
    extended_scopes: dict
    default_scopes: List[str]


class RateLimitResponse(BaseModel):
    """Rate limit status for a token."""
    requests_last_minute: int
    requests_last_hour: int
    limit_per_minute: int
    limit_per_hour: int


@router.get("/scopes", response_model=ScopesResponse)
async def list_scopes():
    """
    List all available scopes for API tokens.

    Returns:
        - scopes: All available scopes
        - canvas_scopes: Scopes for canvas operations only (recommended for MCP)
        - extended_scopes: Additional scopes (tasks, templates, etc.)
        - default_scopes: The default scopes assigned to new tokens
    """
    return ScopesResponse(
        scopes=MCP_SCOPES,
        canvas_scopes=CANVAS_SCOPES,
        extended_scopes=EXTENDED_SCOPES,
        default_scopes=DEFAULT_SCOPES,
    )


@router.post("/", response_model=TokenResponse)
async def create_token(
    token_data: TokenCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Create a new API token for MCP or programmatic access.

    The full token is only returned once - save it securely!

    Example usage with Claude Code:
    ```
    claude mcp add typequest \\
      --env TYPEQUEST_API_URL=https://api.typequest.io \\
      --env TYPEQUEST_API_TOKEN=<your-token> \\
      -- python mcp_server_api.py
    ```
    """
    # Generate token
    raw_token, token_hash, token_prefix = MCPToken.generate_token()

    # Validate scopes - default to canvas-only scopes for safety
    scopes = token_data.scopes if token_data.scopes is not None else DEFAULT_SCOPES
    invalid_scopes = set(scopes) - set(MCP_SCOPES.keys())
    if invalid_scopes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid scopes: {', '.join(invalid_scopes)}",
        )

    # Calculate expiration
    expires_at = None
    if token_data.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=token_data.expires_in_days)

    # Create token record
    mcp_token = MCPToken(
        user_id=current_user.id,
        name=token_data.name,
        token_hash=token_hash,
        token_prefix=token_prefix,
        scopes=scopes,
        allowed_canvas_ids=token_data.allowed_canvas_ids,
        expires_at=expires_at,
    )

    session.add(mcp_token)
    await session.commit()
    await session.refresh(mcp_token)

    return TokenResponse(
        id=mcp_token.id,
        name=mcp_token.name,
        token=raw_token,  # Only time we return the full token!
        token_prefix=token_prefix,
        scopes=scopes,
        created_at=mcp_token.created_at,
        expires_at=mcp_token.expires_at,
    )


@router.get("/", response_model=list[TokenListItem])
async def list_tokens(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List all API tokens for the current user."""
    result = await session.execute(
        select(MCPToken)
        .where(MCPToken.user_id == current_user.id)
        .where(MCPToken.revoked_at.is_(None))
        .order_by(MCPToken.created_at.desc())
    )
    tokens = result.scalars().all()

    return [
        TokenListItem(
            id=t.id,
            name=t.name,
            token_prefix=t.token_prefix,
            scopes=t.scopes or [],
            allowed_canvas_ids=t.allowed_canvas_ids,
            is_active=t.is_active and (t.expires_at is None or t.expires_at > datetime.utcnow()),
            last_used_at=t.last_used_at,
            use_count=t.use_count or 0,
            created_at=t.created_at,
            expires_at=t.expires_at,
        )
        for t in tokens
    ]


@router.delete("/{token_id}")
async def revoke_token(
    token_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Revoke an API token. This cannot be undone."""
    result = await session.execute(
        select(MCPToken).where(
            MCPToken.id == token_id,
            MCPToken.user_id == current_user.id,
        )
    )
    token = result.scalar_one_or_none()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )

    token.revoked_at = datetime.utcnow()
    token.is_active = False
    await session.commit()

    return {"message": "Token revoked successfully", "token_prefix": token.token_prefix}


@router.get("/{token_id}/rate-limit", response_model=RateLimitResponse)
async def get_token_rate_limit(
    token_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get the current rate limit status for a token."""
    result = await session.execute(
        select(MCPToken).where(
            MCPToken.id == token_id,
            MCPToken.user_id == current_user.id,
        )
    )
    token = result.scalar_one_or_none()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )

    usage = rate_limiter.get_usage(token.token_hash)
    return RateLimitResponse(**usage)


# Helper function to validate MCP tokens (used by auth middleware)
async def validate_mcp_token(
    raw_token: str,
    session: AsyncSession,
    request: Optional[Request] = None,
) -> Optional[User]:
    """
    Validate an MCP token and return the associated user.
    Uses the mcp_auth module for validation with rate limiting.
    """
    user, token, error = await validate_mcp_token_with_scopes(
        raw_token=raw_token,
        session=session,
        request=request,
        required_scopes=None,  # Basic validation, scope check at endpoint level
    )

    if error:
        return None

    return user

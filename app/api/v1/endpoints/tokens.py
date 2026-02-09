"""API Token management endpoints for MCP and programmatic access."""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import Optional, List

from app.core.database import get_session
from app.models.mcp import MCPToken, MCP_SCOPES
from app.models.user import User
from app.api.v1.endpoints.auth import get_current_user

router = APIRouter()


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


@router.get("/scopes", response_model=ScopesResponse)
async def list_scopes():
    """List all available scopes for API tokens."""
    return ScopesResponse(scopes=MCP_SCOPES)


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

    # Validate scopes
    scopes = token_data.scopes or list(MCP_SCOPES.keys())
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


# Helper function to validate MCP tokens (used by auth middleware)
async def validate_mcp_token(
    raw_token: str,
    session: AsyncSession,
    request: Request,
) -> Optional[User]:
    """
    Validate an MCP token and return the associated user.
    Also updates last_used_at and use_count.
    """
    token_hash = MCPToken.hash_token(raw_token)

    result = await session.execute(
        select(MCPToken)
        .where(MCPToken.token_hash == token_hash)
        .where(MCPToken.is_active == True)
        .where(MCPToken.revoked_at.is_(None))
    )
    mcp_token = result.scalar_one_or_none()

    if not mcp_token:
        return None

    # Check expiration
    if mcp_token.expires_at and datetime.utcnow() > mcp_token.expires_at:
        return None

    # Update usage stats
    mcp_token.last_used_at = datetime.utcnow()
    mcp_token.last_ip = request.client.host if request.client else None
    mcp_token.use_count = (mcp_token.use_count or 0) + 1
    await session.commit()

    # Get user
    user_result = await session.execute(
        select(User).where(User.id == mcp_token.user_id)
    )
    return user_result.scalar_one_or_none()

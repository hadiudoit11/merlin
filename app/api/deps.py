"""
FastAPI Dependencies for Authentication and Authorization

Provides dependency injection for:
- Current authenticated user (from Auth0 or legacy JWT)
- Organization context
- Permission checking
"""

from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_session
from app.core.auth0 import auth0_validator, Auth0TokenError, Auth0ConfigError
from app.models.user import User


# OAuth2 scheme for token extraction
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
http_bearer = HTTPBearer(auto_error=False)


async def get_current_user_from_auth0(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    session: AsyncSession = Depends(get_session)
) -> User:
    """
    Get current user from Auth0 token.

    Flow:
    1. Extract token from Authorization header
    2. Validate token with Auth0
    3. Extract user info from token claims
    4. Find or create user in database
    5. Update user info if changed

    Returns:
        User: The authenticated user

    Raises:
        HTTPException: If authentication fails
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        # Validate token with Auth0
        payload = await auth0_validator.validate_token(token)

        # Extract user info from token
        auth0_id = auth0_validator.get_user_id(payload)
        email = auth0_validator.get_email(payload)

        if not auth0_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user ID",
            )

        # Find user by Auth0 ID
        result = await session.execute(
            select(User).where(User.auth0_id == auth0_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            # User doesn't exist - create them
            # Get additional info from token
            name = payload.get("name") or payload.get("nickname") or email or "User"
            picture = payload.get("picture")
            email_verified = payload.get("email_verified", False)

            user = User(
                auth0_id=auth0_id,
                email=email or f"{auth0_id}@auth0.local",
                full_name=name,
                picture=picture,
                email_verified=email_verified,
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        else:
            # Update user info if changed
            updated = False
            if email and user.email != email:
                user.email = email
                updated = True
            if payload.get("name") and user.full_name != payload.get("name"):
                user.full_name = payload.get("name")
                updated = True
            if payload.get("picture") and user.picture != payload.get("picture"):
                user.picture = payload.get("picture")
                updated = True
            if payload.get("email_verified") is not None:
                user.email_verified = payload.get("email_verified")
                updated = True

            if updated:
                await session.commit()
                await session.refresh(user)

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled",
            )

        return user

    except Auth0TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Auth0ConfigError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication configuration error: {str(e)}",
        )


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    session: AsyncSession = Depends(get_session)
) -> Optional[User]:
    """
    Get current user if authenticated, None otherwise.
    Useful for endpoints that work for both authenticated and anonymous users.
    """
    if not credentials:
        return None

    try:
        return await get_current_user_from_auth0(credentials, session)
    except HTTPException:
        return None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    token: Optional[str] = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session)
) -> User:
    """
    Get current authenticated user.

    Supports both Auth0 and legacy JWT authentication for migration period.
    In DEBUG mode without any token, creates/returns a dev user.

    Returns:
        User: The authenticated user

    Raises:
        HTTPException: If authentication fails
    """
    # Try Auth0 first if configured
    if settings.USE_AUTH0 and credentials:
        return await get_current_user_from_auth0(credentials, session)

    # Fall back to legacy JWT if Auth0 not configured
    if token:
        # Import legacy auth for backwards compatibility
        from app.api.v1.endpoints.auth import get_current_user as legacy_get_current_user
        return await legacy_get_current_user(token, session)

    # In DEBUG mode, allow unauthenticated access with dev user
    if settings.DEBUG and not credentials and not token:
        from app.api.v1.endpoints.auth import get_or_create_dev_user
        return await get_or_create_dev_user(session)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_superuser(current_user: User = Depends(get_current_user)) -> User:
    """Dependency that requires the current user to be a superuser."""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser access required",
        )
    return current_user

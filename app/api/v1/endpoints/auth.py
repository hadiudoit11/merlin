from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from typing import Optional, Dict, Any

from app.core.config import settings
from app.core.database import get_session
from app.core.auth0 import auth0_validator, Auth0TokenError, Auth0ConfigError
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, Token

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": str(user_id), "exp": expire}
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


async def get_or_create_auth0_user(
    session: AsyncSession,
    auth0_payload: Dict[str, Any]
) -> User:
    """
    Get existing user by Auth0 ID or create a new one.

    Auth0 tokens contain claims like:
    - sub: Auth0 user ID (e.g., "auth0|123456")
    - email: User's email
    - name: User's full name
    - picture: Profile picture URL
    - email_verified: Whether email is verified
    """
    auth0_id = auth0_payload.get("sub")
    email = auth0_payload.get("email")
    full_name = auth0_payload.get("name") or auth0_payload.get("nickname")
    picture = auth0_payload.get("picture")
    email_verified = auth0_payload.get("email_verified", False)

    if not auth0_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Auth0 token: missing sub claim",
        )

    # Try to find user by Auth0 ID first
    result = await session.execute(
        select(User).where(User.auth0_id == auth0_id)
    )
    user = result.scalar_one_or_none()

    if user:
        # Update user info if changed
        updated = False
        if email and user.email != email:
            # Check if email is taken by another user
            existing = await session.execute(
                select(User).where(User.email == email, User.id != user.id)
            )
            if not existing.scalar_one_or_none():
                user.email = email
                updated = True
        if full_name and user.full_name != full_name:
            user.full_name = full_name
            updated = True
        if picture and user.picture != picture:
            user.picture = picture
            updated = True
        if user.email_verified != email_verified:
            user.email_verified = email_verified
            updated = True
        if updated:
            user.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(user)
        return user

    # Try to find user by email (for linking existing accounts)
    if email:
        result = await session.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()

        if user:
            # Link Auth0 ID to existing user
            user.auth0_id = auth0_id
            if picture:
                user.picture = picture
            user.email_verified = email_verified
            user.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(user)
            return user

    # Create new user
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Auth0 token missing email claim",
        )

    user = User(
        auth0_id=auth0_id,
        email=email,
        full_name=full_name,
        picture=picture,
        email_verified=email_verified,
        hashed_password=None,  # Auth0 users don't have local passwords
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_or_create_dev_user(session: AsyncSession) -> User:
    """Get or create a development user for testing without auth."""
    dev_email = "dev@localhost"
    result = await session.execute(select(User).where(User.email == dev_email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            email=dev_email,
            hashed_password=get_password_hash("devpassword"),
            full_name="Development User",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    return user


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session)
) -> User:
    # In DEBUG mode, allow unauthenticated access with a dev user
    if settings.DEBUG and not token:
        return await get_or_create_dev_user(session)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Try Auth0 validation first (if configured)
    if settings.USE_AUTH0:
        try:
            auth0_payload = await auth0_validator.validate_token(token)
            # Auth0 token is valid - get or create user
            return await get_or_create_auth0_user(session, auth0_payload)
        except Auth0ConfigError:
            # Auth0 not configured, fall through to legacy JWT
            pass
        except Auth0TokenError:
            # Not a valid Auth0 token, try legacy JWT
            pass

    # Try legacy JWT validation
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await session.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user


@router.post("/register", response_model=UserResponse)
async def register(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_session)
):
    # Check if user exists
    result = await session.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Create user
    user = User(
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
        full_name=user_data.full_name,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session)
):
    result = await session.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(user.id)
    return Token(access_token=access_token)


from pydantic import BaseModel

class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login/")
async def login_json(
    login_data: LoginRequest,
    session: AsyncSession = Depends(get_session)
):
    """JSON login endpoint for NextAuth credentials provider."""
    result = await session.execute(select(User).where(User.email == login_data.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    access_token = create_access_token(user.id)

    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.full_name,
        "image": user.picture,
        "access_token": access_token,
        "access_token_expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user

from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None


class UserCreate(UserBase):
    """Create user - password optional for Auth0 users."""
    password: Optional[str] = None


class UserUpdate(BaseModel):
    """Update user profile."""
    full_name: Optional[str] = None
    picture: Optional[str] = None


class UserResponse(UserBase):
    """User response with all fields."""
    id: int
    auth0_id: Optional[str] = None
    picture: Optional[str] = None
    email_verified: bool = False
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserBrief(BaseModel):
    """Brief user info for embedding in other responses."""
    id: int
    email: str
    full_name: Optional[str] = None
    picture: Optional[str] = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    """JWT token response (legacy)."""
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """JWT token payload (legacy)."""
    sub: Optional[int] = None
    exp: Optional[datetime] = None


class Auth0UserInfo(BaseModel):
    """User info extracted from Auth0 token."""
    sub: str  # Auth0 user ID
    email: Optional[str] = None
    email_verified: Optional[bool] = None
    name: Optional[str] = None
    nickname: Optional[str] = None
    picture: Optional[str] = None
    permissions: List[str] = []

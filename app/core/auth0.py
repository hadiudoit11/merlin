"""
Auth0 JWT Validation Module

Handles validation of Auth0 access tokens using RS256 algorithm.
Fetches and caches JWKS (JSON Web Key Set) from Auth0.
"""

import httpx
from jose import jwt, jwk, JWTError
from jose.exceptions import ExpiredSignatureError
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from functools import lru_cache
import asyncio

from app.core.config import settings


class Auth0Error(Exception):
    """Base exception for Auth0 errors."""
    pass


class Auth0TokenError(Auth0Error):
    """Token validation failed."""
    pass


class Auth0ConfigError(Auth0Error):
    """Auth0 is not properly configured."""
    pass


class JWKSCache:
    """Cache for Auth0 JWKS keys with automatic refresh."""

    def __init__(self, cache_duration_seconds: int = 3600):
        self._keys: Dict[str, Any] = {}
        self._last_refresh: Optional[datetime] = None
        self._cache_duration = timedelta(seconds=cache_duration_seconds)
        self._lock = asyncio.Lock()

    def is_expired(self) -> bool:
        if self._last_refresh is None:
            return True
        return datetime.utcnow() - self._last_refresh > self._cache_duration

    async def get_keys(self, jwks_url: str) -> Dict[str, Any]:
        """Get cached keys or fetch new ones if expired."""
        async with self._lock:
            if self.is_expired():
                await self._refresh_keys(jwks_url)
            return self._keys

    async def _refresh_keys(self, jwks_url: str) -> None:
        """Fetch JWKS from Auth0."""
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url, timeout=10.0)
            response.raise_for_status()
            jwks = response.json()

        # Index keys by kid (key ID)
        self._keys = {key["kid"]: key for key in jwks.get("keys", [])}
        self._last_refresh = datetime.utcnow()

    def invalidate(self) -> None:
        """Force refresh on next get_keys call."""
        self._last_refresh = None


# Global JWKS cache
_jwks_cache = JWKSCache()


class Auth0TokenValidator:
    """Validates Auth0 JWT tokens."""

    def __init__(self):
        self.domain = settings.AUTH0_DOMAIN
        self.audience = settings.AUTH0_API_AUDIENCE
        self.issuer = settings.AUTH0_ISSUER
        self.jwks_url = settings.AUTH0_JWKS_URL
        self.algorithms = ["RS256"]

    def _check_configured(self) -> None:
        """Ensure Auth0 is configured."""
        if not settings.USE_AUTH0:
            raise Auth0ConfigError(
                "Auth0 is not configured. Set AUTH0_DOMAIN and AUTH0_API_AUDIENCE."
            )

    async def validate_token(self, token: str) -> Dict[str, Any]:
        """
        Validate an Auth0 access token.

        Args:
            token: The JWT access token from Auth0

        Returns:
            Decoded token payload containing user claims

        Raises:
            Auth0TokenError: If token validation fails
            Auth0ConfigError: If Auth0 is not configured
        """
        self._check_configured()

        try:
            # Get the unverified header to find the key ID
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")

            if not kid:
                raise Auth0TokenError("Token missing key ID (kid)")

            # Fetch the signing key from JWKS
            keys = await _jwks_cache.get_keys(self.jwks_url)
            key_data = keys.get(kid)

            if not key_data:
                # Key not found - try refreshing cache
                _jwks_cache.invalidate()
                keys = await _jwks_cache.get_keys(self.jwks_url)
                key_data = keys.get(kid)

                if not key_data:
                    raise Auth0TokenError(f"Unable to find signing key: {kid}")

            # Convert JWKS key to PEM format for verification
            public_key = jwk.construct(key_data)

            # Verify and decode the token
            payload = jwt.decode(
                token,
                public_key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
            )

            return payload

        except ExpiredSignatureError:
            raise Auth0TokenError("Token has expired")
        except JWTError as e:
            raise Auth0TokenError(f"Invalid token: {str(e)}")
        except httpx.HTTPError as e:
            raise Auth0TokenError(f"Failed to fetch JWKS: {str(e)}")

    def get_user_id(self, payload: Dict[str, Any]) -> str:
        """Extract Auth0 user ID from token payload."""
        return payload.get("sub", "")

    def get_email(self, payload: Dict[str, Any]) -> Optional[str]:
        """Extract email from token payload if available."""
        return payload.get("email") or payload.get(
            f"{self.issuer}email"
        )

    def get_permissions(self, payload: Dict[str, Any]) -> list:
        """Extract permissions from token payload."""
        return payload.get("permissions", [])

    def get_scope(self, payload: Dict[str, Any]) -> list:
        """Extract scopes from token payload."""
        scope = payload.get("scope", "")
        return scope.split() if scope else []


# Create global validator instance
auth0_validator = Auth0TokenValidator()


async def validate_auth0_token(token: str) -> Dict[str, Any]:
    """
    Convenience function to validate an Auth0 token.

    Args:
        token: The JWT access token

    Returns:
        Decoded token payload

    Raises:
        Auth0TokenError: If validation fails
    """
    return await auth0_validator.validate_token(token)

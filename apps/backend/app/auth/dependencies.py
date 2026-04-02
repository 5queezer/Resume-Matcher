"""FastAPI dependencies for authentication."""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import verify_access_token
from app.config import settings
from app.database import db

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict:
    """Extract and validate the current user from Bearer token."""
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify_access_token(
            credentials.credentials, secret=settings.effective_jwt_secret
        )
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await db.get_user_by_id(claims["sub"])
    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict | None:
    """Like get_current_user but returns None instead of raising."""
    if not credentials:
        return None
    try:
        claims = verify_access_token(
            credentials.credentials, secret=settings.effective_jwt_secret
        )
    except ValueError:
        return None
    return await db.get_user_by_id(claims["sub"])

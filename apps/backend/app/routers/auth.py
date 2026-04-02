"""Auth endpoints: registration and user profile."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import get_current_user
from app.auth.password import hash_password
from app.database import db
from app.schemas.auth import RegisterRequest, RegisterResponse, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(body: RegisterRequest) -> RegisterResponse:
    """Register a new user account."""
    hashed = hash_password(body.password)
    try:
        user = await db.create_user(
            email=body.email,
            hashed_password=hashed,
            display_name=body.display_name,
        )
    except (IntegrityError, Exception) as e:
        logger.error("Registration failed: %s", e)
        raise HTTPException(status_code=409, detail="Email already registered")

    return RegisterResponse(
        id=user["id"],
        email=user["email"],
        display_name=user["display_name"],
    )


@router.get("/me", response_model=UserResponse)
async def me(user: dict = Depends(get_current_user)) -> UserResponse:
    """Get the current authenticated user's profile."""
    return UserResponse(
        id=user["id"],
        email=user["email"],
        display_name=user.get("display_name"),
        is_active=user["is_active"],
        created_at=user.get("created_at"),
    )

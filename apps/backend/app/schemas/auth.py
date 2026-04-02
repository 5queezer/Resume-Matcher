"""Pydantic schemas for authentication endpoints."""

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)


class RegisterResponse(BaseModel):
    id: str
    email: str
    display_name: str | None


class AuthorizeRequest(BaseModel):
    """OAuth 2.1 authorization request with embedded credentials."""
    email: EmailStr
    password: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str = "S256"
    state: str | None = None
    scope: str | None = None


class TokenRequest(BaseModel):
    """OAuth 2.1 token exchange request."""
    grant_type: str  # "authorization_code" or "refresh_token"
    code: str | None = None
    code_verifier: str | None = None
    client_id: str | None = None
    redirect_uri: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    is_active: bool
    created_at: str | None


class ErrorResponse(BaseModel):
    """RFC 6750 error response."""
    error: str
    error_description: str | None = None

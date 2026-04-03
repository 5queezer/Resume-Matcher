"""Pydantic schemas for authentication endpoints."""

from typing import Literal

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


class ClientRegistrationRequest(BaseModel):
    """RFC 7591 Dynamic Client Registration request."""
    redirect_uris: list[str] = Field(min_length=1)
    client_name: str | None = None
    token_endpoint_auth_method: Literal["none"] = "none"
    grant_types: list[str] = Field(default=["authorization_code"])
    response_types: list[str] = Field(default=["code"])


class ClientRegistrationResponse(BaseModel):
    """RFC 7591 Dynamic Client Registration response."""
    client_id: str
    client_name: str | None
    redirect_uris: list[str]
    grant_types: list[str]
    response_types: list[str]
    token_endpoint_auth_method: str
    client_id_issued_at: int

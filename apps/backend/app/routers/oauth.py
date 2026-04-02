"""OAuth 2.1 endpoints: authorize, token, revoke, discovery."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.auth.constants import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    AUTHORIZATION_CODE_EXPIRE_MINUTES,
    FIRST_PARTY_CLIENT_ID,
    FIRST_PARTY_REDIRECT_URIS,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from app.auth.jwt import create_access_token
from app.auth.password import verify_password
from app.auth.pkce import verify_code_challenge
from app.config import settings
from app.database import db
from app.schemas.auth import AuthorizeRequest, TokenRequest, TokenResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _allowed_redirect_uris() -> list[str]:
    """Build list of allowed redirect URIs including dynamic frontend origin."""
    uris = list(FIRST_PARTY_REDIRECT_URIS)
    dynamic = f"{settings.frontend_origin.rstrip('/')}/callback"
    if dynamic not in uris:
        uris.append(dynamic)
    return uris


def _validate_client(client_id: str, redirect_uri: str) -> None:
    """Validate client_id and redirect_uri against known clients."""
    if client_id != FIRST_PARTY_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Unknown client_id")
    if redirect_uri not in _allowed_redirect_uris():
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")


@router.post("/authorize")
async def authorize(body: AuthorizeRequest) -> Response:
    """OAuth 2.1 authorization endpoint with embedded credentials."""
    _validate_client(body.client_id, body.redirect_uri)

    if body.code_challenge_method != "S256":
        raise HTTPException(status_code=400, detail="Only S256 is supported")

    # Authenticate user
    user = await db.get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account disabled")

    # Generate authorization code
    code = secrets.token_urlsafe(32)
    code_hash = hashlib.sha256(code.encode()).hexdigest()

    await db.create_authorization_code(
        code_hash=code_hash,
        user_id=user["id"],
        client_id=body.client_id,
        redirect_uri=body.redirect_uri,
        code_challenge=body.code_challenge,
        scope=body.scope,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=AUTHORIZATION_CODE_EXPIRE_MINUTES),
    )

    # Redirect with code
    params: dict[str, str] = {"code": code}
    if body.state:
        params["state"] = body.state
    redirect_url = f"{body.redirect_uri}?{urlencode(params)}"
    return RedirectResponse(url=redirect_url, status_code=303)

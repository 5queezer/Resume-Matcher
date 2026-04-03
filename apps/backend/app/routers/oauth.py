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
from app.schemas.auth import AuthorizeRequest, ClientRegistrationRequest, ClientRegistrationResponse, TokenRequest, TokenResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _allowed_redirect_uris() -> list[str]:
    """Build list of allowed redirect URIs including dynamic frontend origin."""
    uris = list(FIRST_PARTY_REDIRECT_URIS)
    dynamic = f"{settings.frontend_origin.rstrip('/')}/callback"
    if dynamic not in uris:
        uris.append(dynamic)
    return uris


async def _validate_client(client_id: str, redirect_uri: str) -> None:
    """Validate client_id and redirect_uri against registered clients."""
    oauth_client = await db.get_oauth_client(client_id)
    if not oauth_client or not oauth_client["is_active"]:
        raise HTTPException(status_code=400, detail="Unknown client_id")

    allowed = list(oauth_client["redirect_uris"])
    # First-party client also accepts dynamic frontend origin
    if client_id == FIRST_PARTY_CLIENT_ID:
        dynamic = f"{settings.frontend_origin.rstrip('/')}/callback"
        if dynamic not in allowed:
            allowed.append(dynamic)

    if redirect_uri not in allowed:
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")


@router.post("/authorize")
async def authorize(body: AuthorizeRequest) -> Response:
    """OAuth 2.1 authorization endpoint with embedded credentials."""
    await _validate_client(body.client_id, body.redirect_uri)

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


@router.post("/token", response_model=TokenResponse)
async def token(body: TokenRequest, request: Request, response: Response) -> TokenResponse:
    """OAuth 2.1 token endpoint: code exchange and refresh."""
    if body.grant_type == "authorization_code":
        return await _handle_code_exchange(body, response)
    elif body.grant_type == "refresh_token":
        refresh_cookie = request.cookies.get("refresh_token")
        if not refresh_cookie:
            raise HTTPException(status_code=400, detail="Missing refresh token")
        return await _handle_refresh(refresh_cookie, response)
    else:
        raise HTTPException(status_code=400, detail="Unsupported grant_type")


async def _handle_code_exchange(body: TokenRequest, response: Response) -> TokenResponse:
    """Exchange authorization code + PKCE verifier for tokens."""
    if not body.code or not body.code_verifier or not body.client_id or not body.redirect_uri:
        raise HTTPException(status_code=400, detail="Missing required parameters")

    code_hash = hashlib.sha256(body.code.encode()).hexdigest()
    stored = await db.get_authorization_code(code_hash)

    if not stored:
        raise HTTPException(status_code=400, detail="Invalid authorization code")

    expires_at = datetime.fromisoformat(stored["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Authorization code expired")

    marked = await db.mark_authorization_code_used(code_hash)
    if not marked:
        raise HTTPException(status_code=400, detail="Authorization code already used or not found")

    if stored["client_id"] != body.client_id or stored["redirect_uri"] != body.redirect_uri:
        raise HTTPException(status_code=400, detail="Client/redirect mismatch")

    if not verify_code_challenge(body.code_verifier, stored["code_challenge"], "S256"):
        raise HTTPException(status_code=400, detail="PKCE verification failed")

    user = await db.get_user_by_id(stored["user_id"])
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    return await _issue_tokens(user, response)


async def _handle_refresh(refresh_token_value: str, response: Response) -> TokenResponse:
    """Refresh: validate token, rotate, issue new tokens."""
    token_hash = hashlib.sha256(refresh_token_value.encode()).hexdigest()
    stored = await db.get_refresh_token(token_hash)

    if not stored:
        raise HTTPException(status_code=400, detail="Invalid refresh token")

    if stored["revoked_at"] is not None:
        await db.revoke_token_family(stored["family_id"])
        logger.warning("Refresh token reuse detected for family %s", stored["family_id"])
        raise HTTPException(status_code=400, detail="Token reuse detected")

    expires_at = datetime.fromisoformat(stored["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Refresh token expired")

    await db.revoke_refresh_token(token_hash)

    user = await db.get_user_by_id(stored["user_id"])
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    return await _issue_tokens(user, response, family_id=stored["family_id"])


async def _issue_tokens(
    user: dict, response: Response, family_id: str | None = None,
) -> TokenResponse:
    """Create access token (JWT) and refresh token (cookie)."""
    access_token = create_access_token(
        user_id=user["id"],
        email=user["email"],
    )

    raw_refresh = secrets.token_urlsafe(32)
    refresh_hash = hashlib.sha256(raw_refresh.encode()).hexdigest()
    fid = family_id or secrets.token_urlsafe(16)

    await db.create_refresh_token(
        token_hash=refresh_hash,
        user_id=user["id"],
        family_id=fid,
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )

    is_secure = settings.frontend_origin.startswith("https")

    response.set_cookie(
        key="refresh_token",
        value=raw_refresh,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/v1/oauth/token",
    )
    response.set_cookie(
        key="has_session",
        value="1",
        httponly=False,
        secure=is_secure,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
    )

    return TokenResponse(
        access_token=access_token,
        token_type="Bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/revoke")
async def revoke(request: Request, response: Response) -> dict:
    """Revoke the current refresh token and clear cookies."""
    refresh_cookie = request.cookies.get("refresh_token")
    if refresh_cookie:
        token_hash = hashlib.sha256(refresh_cookie.encode()).hexdigest()
        stored = await db.get_refresh_token(token_hash)
        if stored:
            await db.revoke_token_family(stored["family_id"])

    response.delete_cookie("refresh_token", path="/api/v1/oauth/token")
    response.delete_cookie("has_session", path="/")
    return {"status": "ok"}


@router.post("/register", status_code=201)
async def register_client(body: ClientRegistrationRequest) -> ClientRegistrationResponse:
    """RFC 7591 Dynamic Client Registration."""
    import time
    client = await db.create_oauth_client(
        client_name=body.client_name,
        redirect_uris=body.redirect_uris,
        grant_types=body.grant_types,
        response_types=body.response_types,
        token_endpoint_auth_method=body.token_endpoint_auth_method,
    )
    return ClientRegistrationResponse(
        client_id=client["client_id"],
        client_name=client["client_name"],
        redirect_uris=client["redirect_uris"],
        grant_types=client["grant_types"],
        response_types=client["response_types"],
        token_endpoint_auth_method=client["token_endpoint_auth_method"],
        client_id_issued_at=int(time.time()),
    )

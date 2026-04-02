"""Google OAuth 2.0 endpoints: start and callback."""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.auth.constants import (
    AUTHORIZATION_CODE_EXPIRE_MINUTES,
    FIRST_PARTY_CLIENT_ID,
)
from app.auth.google import (
    GOOGLE_AUTH_URL,
    GOOGLE_SCOPES,
    PasswordAccountExists,
    exchange_google_code,
    pack_state,
    parse_id_token,
    resolve_google_user,
    unpack_state,
    validate_id_token_claims,
)
from app.config import settings
from app.database import db
from app.routers.oauth import _allowed_redirect_uris

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth/google", tags=["google-oauth"])


def _google_callback_uri(request: Request) -> str:
    """Build the Google callback URI from the request base URL."""
    return f"{str(request.base_url).rstrip('/')}/api/v1/oauth/google/callback"


@router.get("/start")
async def google_start(
    request: Request,
    state: str,
    code_challenge: str,
    redirect_uri: str,
    code_challenge_method: str = "S256",
) -> RedirectResponse:
    """Initiate Google OAuth flow.

    Packs the frontend's PKCE params into Google's state parameter
    with HMAC integrity protection.
    """
    if not settings.google_client_id or not settings.google_client_secret:
        return RedirectResponse(
            f"{settings.frontend_origin}/login?error=google_not_configured",
            status_code=302,
        )

    if redirect_uri not in _allowed_redirect_uris():
        return RedirectResponse(
            f"{settings.frontend_origin}/login?error=invalid_redirect",
            status_code=302,
        )

    if code_challenge_method != "S256":
        return RedirectResponse(
            f"{settings.frontend_origin}/login?error=google_failed",
            status_code=302,
        )

    nonce = secrets.token_urlsafe(32)
    packed = pack_state(
        {
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "redirect_uri": redirect_uri,
            "nonce": nonce,
            "ts": int(time.time()),
        },
        settings.effective_jwt_secret,
    )

    google_params = urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": _google_callback_uri(request),
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "state": packed,
        "nonce": nonce,
        "access_type": "online",
        "prompt": "select_account",
    })

    return RedirectResponse(
        f"{GOOGLE_AUTH_URL}?{google_params}",
        status_code=302,
    )


@router.get("/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Google's OAuth callback."""
    frontend_login = f"{settings.frontend_origin}/login"

    if error or not code or not state:
        logger.warning("Google callback error: %s", error or "missing params")
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    try:
        data = unpack_state(state, settings.effective_jwt_secret)
    except ValueError as e:
        logger.warning("Google callback invalid state: %s", e)
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    try:
        tokens = await exchange_google_code(
            code=code,
            redirect_uri=_google_callback_uri(request),
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
    except ValueError as e:
        logger.error("Google token exchange failed: %s", e)
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    id_token_raw = tokens.get("id_token")
    if not id_token_raw:
        logger.error("Google response missing id_token")
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    try:
        claims = parse_id_token(id_token_raw)
        validate_id_token_claims(claims, settings.google_client_id, data["nonce"])
    except ValueError as e:
        logger.warning("Google ID token validation failed: %s", e)
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    try:
        user = await resolve_google_user(claims, db)
    except PasswordAccountExists:
        return RedirectResponse(
            f"{frontend_login}?error=account_exists",
            status_code=302,
        )

    our_code = secrets.token_urlsafe(32)
    code_hash = hashlib.sha256(our_code.encode()).hexdigest()

    await db.create_authorization_code(
        code_hash=code_hash,
        user_id=user["id"],
        client_id=FIRST_PARTY_CLIENT_ID,
        redirect_uri=data["redirect_uri"],
        code_challenge=data["code_challenge"],
        scope="openid email profile",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=AUTHORIZATION_CODE_EXPIRE_MINUTES),
    )

    redirect_url = f"{data['redirect_uri']}?{urlencode({'code': our_code, 'state': data['state']})}"
    return RedirectResponse(url=redirect_url, status_code=303)

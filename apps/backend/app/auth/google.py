"""Google OAuth 2.0 helpers: state packing, token exchange, user resolution."""

import base64
import hashlib
import hmac
import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Google endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "openid email profile"


# ---------------------------------------------------------------------------
# State packing (HMAC-signed, stateless)
# ---------------------------------------------------------------------------

def pack_state(data: dict, secret: str) -> str:
    """Pack OAuth state dict with HMAC-SHA256 integrity protection."""
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def unpack_state(packed: str, secret: str, max_age: int = 600) -> dict:
    """Unpack and verify HMAC-signed state. Raises ValueError on failure."""
    try:
        payload, sig = packed.rsplit(".", 1)
    except ValueError:
        raise ValueError("Malformed state")

    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Invalid state signature")

    # Restore base64 padding
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding

    data = json.loads(base64.urlsafe_b64decode(payload))

    if time.time() - data.get("ts", 0) > max_age:
        raise ValueError("State expired")

    return data


# ---------------------------------------------------------------------------
# Google token exchange
# ---------------------------------------------------------------------------

async def exchange_google_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Exchange Google authorization code for tokens (including id_token)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        if resp.status_code != 200:
            raise ValueError(f"Google token exchange failed: {resp.status_code}")
        return resp.json()


# ---------------------------------------------------------------------------
# ID token parsing and validation
# ---------------------------------------------------------------------------

def parse_id_token(id_token: str) -> dict:
    """Decode JWT payload without signature verification.

    Safe because the token comes directly from Google's token endpoint
    over HTTPS (trusted channel).
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid ID token format")
    payload = parts[1]
    # Restore base64 padding
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    return json.loads(base64.urlsafe_b64decode(payload))


def validate_id_token_claims(
    claims: dict,
    expected_aud: str,
    expected_nonce: str,
) -> dict:
    """Validate Google ID token claims. Raises ValueError on failure."""
    valid_issuers = ("https://accounts.google.com", "accounts.google.com")
    if claims.get("iss") not in valid_issuers:
        raise ValueError(f"Invalid issuer: {claims.get('iss')}")
    if claims.get("aud") != expected_aud:
        raise ValueError("Audience mismatch")
    if claims.get("exp", 0) < time.time():
        raise ValueError("ID token expired")
    if claims.get("nonce") != expected_nonce:
        raise ValueError("Nonce mismatch")
    return claims

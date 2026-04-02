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

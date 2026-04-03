"""JWT access token operations using joserfc (RS256)."""

import time

from joserfc import jwt

from app.auth.constants import ACCESS_TOKEN_EXPIRE_MINUTES
from app.auth.keys import get_kid, get_private_key, get_public_key

_ALGORITHM = "RS256"
_ISSUER = "resume-matcher"


def create_access_token(
    user_id: str,
    email: str,
    expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
) -> str:
    """Create an RS256-signed JWT access token."""
    now = int(time.time())
    claims = {
        "sub": user_id,
        "email": email,
        "iss": _ISSUER,
        "iat": now,
        "exp": now + (expires_minutes * 60),
    }
    key = get_private_key()
    return jwt.encode({"alg": _ALGORITHM, "kid": get_kid()}, claims, key)


def verify_access_token(token: str) -> dict:
    """Verify and decode an RS256 JWT access token. Raises ValueError on failure."""
    key = get_public_key()
    try:
        decoded = jwt.decode(token, key)
    except Exception as e:
        raise ValueError(f"Token invalid: {e}") from e

    claims = decoded.claims
    now = int(time.time())
    if claims.get("exp", 0) < now:
        raise ValueError("Token expired")
    if claims.get("iss") != _ISSUER:
        raise ValueError("Token invalid: wrong issuer")
    return claims

"""JWT access token operations using joserfc."""

import time

from joserfc import jwt
from joserfc.jwk import OctKey

from app.auth.constants import ACCESS_TOKEN_EXPIRE_MINUTES

_ALGORITHM = "HS256"
_ISSUER = "resume-matcher"


def create_access_token(
    user_id: str,
    email: str,
    secret: str,
    expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
) -> str:
    """Create a signed JWT access token."""
    now = int(time.time())
    claims = {
        "sub": user_id,
        "email": email,
        "iss": _ISSUER,
        "iat": now,
        "exp": now + (expires_minutes * 60),
    }
    key = OctKey.import_key(secret)
    return jwt.encode({"alg": _ALGORITHM}, claims, key)


def verify_access_token(token: str, secret: str) -> dict:
    """Verify and decode a JWT access token. Raises ValueError on failure."""
    key = OctKey.import_key(secret)
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

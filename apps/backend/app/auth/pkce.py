"""PKCE (RFC 7636) code challenge verification."""

import base64
import hashlib


def verify_code_challenge(
    code_verifier: str, code_challenge: str, method: str
) -> bool:
    """Verify a PKCE code challenge against a code verifier.

    Only S256 is supported (OAuth 2.1 mandate).
    """
    if method != "S256":
        raise ValueError("Only S256 code_challenge_method is supported")
    if not code_verifier:
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge

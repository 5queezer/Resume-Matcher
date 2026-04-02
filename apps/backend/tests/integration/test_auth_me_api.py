"""Integration tests for /auth/me and auth dependencies."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import pytest


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _full_login(
    client, email: str = "me@example.com", password: str = "password123456",
) -> str:
    """Register + OAuth flow, return access_token."""
    await client.post("/api/v1/auth/register", json={
        "email": email, "password": password,
    })
    verifier, challenge = _pkce_pair()
    resp = await client.post("/api/v1/oauth/authorize", json={
        "email": email,
        "password": password,
        "client_id": "resume-matcher-web",
        "redirect_uri": "http://localhost:3000/callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    query = parse_qs(urlparse(resp.headers["location"]).query)
    code = query["code"][0]
    token_resp = await client.post("/api/v1/oauth/token", json={
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": "resume-matcher-web",
        "redirect_uri": "http://localhost:3000/callback",
    })
    return token_resp.json()["access_token"]


class TestAuthMe:
    async def test_me_authenticated(self, client) -> None:
        token = await _full_login(client)
        resp = await client.get("/api/v1/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "me@example.com"
        assert "hashed_password" not in data

    async def test_me_no_token(self, client) -> None:
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers

    async def test_me_invalid_token(self, client) -> None:
        resp = await client.get("/api/v1/auth/me", headers={
            "Authorization": "Bearer invalid-token",
        })
        assert resp.status_code == 401

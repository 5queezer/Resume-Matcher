"""Integration tests for OAuth 2.1 authorization flow."""

import base64
import hashlib
import secrets

import pytest
from urllib.parse import parse_qs, urlparse


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _register_user(
    client, email: str = "oauth@example.com", password: str = "password123456",
) -> None:
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": password,
    })


class TestAuthorize:
    async def test_authorize_success_returns_redirect(self, client) -> None:
        await _register_user(client)
        verifier, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "oauth@example.com",
            "password": "password123456",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "random-state",
        }, follow_redirects=False)
        assert resp.status_code == 303
        location = resp.headers["location"]
        assert "code=" in location
        assert "state=random-state" in location
        assert location.startswith("http://localhost:3000/callback")

    async def test_authorize_wrong_password(self, client) -> None:
        await _register_user(client)
        _, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "oauth@example.com",
            "password": "wrongpassword",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert resp.status_code == 401

    async def test_authorize_unknown_client(self, client) -> None:
        await _register_user(client)
        _, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "oauth@example.com",
            "password": "password123456",
            "client_id": "unknown-client",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert resp.status_code == 400

    async def test_authorize_invalid_redirect_uri(self, client) -> None:
        await _register_user(client)
        _, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "oauth@example.com",
            "password": "password123456",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://evil.com/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert resp.status_code == 400

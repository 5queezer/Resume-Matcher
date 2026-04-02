"""Integration tests for OAuth 2.1 authorization flow."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import pytest


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


class TestTokenExchange:
    async def _get_auth_code(
        self, client, email: str = "token@example.com",
    ) -> tuple[str, str]:
        """Register, authorize, return (code, verifier)."""
        await _register_user(client, email)
        verifier, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": email,
            "password": "password123456",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "test",
        }, follow_redirects=False)
        location = resp.headers["location"]
        query = parse_qs(urlparse(location).query)
        return query["code"][0], verifier

    async def test_exchange_code_for_tokens(self, client) -> None:
        code, verifier = await self._get_auth_code(client)
        resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] == 900  # 15 min
        # Check refresh token cookie
        set_cookie = resp.headers.get("set-cookie", "")
        assert "refresh_token=" in set_cookie

    async def test_exchange_wrong_verifier(self, client) -> None:
        code, _ = await self._get_auth_code(client, email="wrong-v@example.com")
        resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": "wrong-verifier",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        assert resp.status_code == 400

    async def test_exchange_code_replay(self, client) -> None:
        code, verifier = await self._get_auth_code(client, email="replay@example.com")
        resp1 = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        assert resp1.status_code == 200
        resp2 = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        assert resp2.status_code == 400

    async def test_exchange_invalid_grant_type(self, client) -> None:
        resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "password",
        })
        assert resp.status_code == 400


class TestRevoke:
    async def test_revoke_endpoint(self, client) -> None:
        # Just test it responds 200 (even without cookie)
        resp = await client.post("/api/v1/oauth/revoke")
        assert resp.status_code == 200


class TestDiscovery:
    async def test_well_known_oauth(self, client) -> None:
        resp = await client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "code_challenge_methods_supported" in data
        assert "S256" in data["code_challenge_methods_supported"]
        assert "response_types_supported" in data
        assert "code" in data["response_types_supported"]

"""Integration tests for Google OAuth endpoints."""

import base64
import json
import time
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from app.auth.google import pack_state
from app.auth.password import hash_password

JWT_SECRET = "test-secret-for-tests"


def _make_id_token(claims: dict) -> str:
    """Build a fake JWT with the given payload claims (no real signature)."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(b"sig").decode().rstrip("=")
    return f"{header}.{payload}.{sig}"


def _valid_packed_state(
    nonce: str = "test-nonce",
    redirect_uri: str = "http://localhost:3000/callback",
) -> str:
    """Create a valid HMAC-signed packed state for callback tests."""
    return pack_state(
        {
            "state": "frontend-state",
            "code_challenge": "test-challenge",
            "code_challenge_method": "S256",
            "redirect_uri": redirect_uri,
            "nonce": nonce,
            "ts": int(time.time()),
        },
        JWT_SECRET,
    )


def _valid_id_claims(
    nonce: str = "test-nonce",
    email: str = "google@example.com",
    aud: str = "test-google-id",
) -> dict:
    """Return valid Google ID token claims."""
    return {
        "iss": "https://accounts.google.com",
        "aud": aud,
        "sub": "google-sub-123",
        "email": email,
        "email_verified": True,
        "name": "Google User",
        "nonce": nonce,
        "exp": int(time.time()) + 3600,
    }


# ---------------------------------------------------------------------------
# TestProvidersEndpoint
# ---------------------------------------------------------------------------


class TestProvidersEndpoint:
    async def test_providers_credentials_only(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "")
        resp = await client.get("/api/v1/auth/providers")
        assert resp.status_code == 200
        assert resp.json() == {"providers": ["credentials"]}

    async def test_providers_with_google(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        resp = await client.get("/api/v1/auth/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "google" in data["providers"]
        assert "credentials" in data["providers"]


# ---------------------------------------------------------------------------
# TestGoogleStart
# ---------------------------------------------------------------------------


class TestGoogleStart:
    async def test_redirects_to_google(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        monkeypatch.setattr("app.config.settings.google_client_secret", "test-secret")
        resp = await client.get(
            "/api/v1/oauth/google/start",
            params={
                "state": "frontend-state",
                "code_challenge": "abc123",
                "redirect_uri": "http://localhost:3000/callback",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "accounts.google.com" in location
        assert "client_id=test-google-id" in location

    async def test_google_not_configured(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "")
        resp = await client.get(
            "/api/v1/oauth/google/start",
            params={
                "state": "s",
                "code_challenge": "c",
                "redirect_uri": "http://localhost:3000/callback",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_not_configured" in resp.headers["location"]

    async def test_invalid_redirect_uri(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        resp = await client.get(
            "/api/v1/oauth/google/start",
            params={
                "state": "s",
                "code_challenge": "c",
                "redirect_uri": "https://evil.com",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "invalid_redirect" in resp.headers["location"]

    async def test_missing_params(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        resp = await client.get(
            "/api/v1/oauth/google/start",
            follow_redirects=False,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# TestGoogleCallback
# ---------------------------------------------------------------------------


class TestGoogleCallback:
    async def test_full_callback_new_user(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        monkeypatch.setattr("app.config.settings.google_client_secret", "test-secret")

        nonce = "cb-nonce"
        packed = _valid_packed_state(nonce=nonce)
        claims = _valid_id_claims(nonce=nonce)
        fake_jwt = _make_id_token(claims)

        mock_exchange = AsyncMock(return_value={"id_token": fake_jwt})
        with patch("app.routers.google_oauth.exchange_google_code", mock_exchange):
            resp = await client.get(
                "/api/v1/oauth/google/callback",
                params={"code": "google-auth-code", "state": packed},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        location = resp.headers["location"]
        assert location.startswith("http://localhost:3000/callback")
        parsed = parse_qs(urlparse(location).query)
        assert "code" in parsed
        assert parsed["state"] == ["frontend-state"]

    async def test_callback_google_error(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        resp = await client.get(
            "/api/v1/oauth/google/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_failed" in resp.headers["location"]

    async def test_callback_invalid_state(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        monkeypatch.setattr("app.config.settings.google_client_secret", "test-secret")
        resp = await client.get(
            "/api/v1/oauth/google/callback",
            params={"code": "some-code", "state": "tampered.badsig"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_failed" in resp.headers["location"]

    async def test_callback_expired_state(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        monkeypatch.setattr("app.config.settings.google_client_secret", "test-secret")

        old_packed = pack_state(
            {
                "state": "old",
                "code_challenge": "c",
                "code_challenge_method": "S256",
                "redirect_uri": "http://localhost:3000/callback",
                "nonce": "n",
                "ts": int(time.time()) - 700,  # >600s max_age
            },
            JWT_SECRET,
        )
        resp = await client.get(
            "/api/v1/oauth/google/callback",
            params={"code": "some-code", "state": old_packed},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_failed" in resp.headers["location"]

    async def test_callback_password_account_denied(
        self, client, test_db, monkeypatch,
    ) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        monkeypatch.setattr("app.config.settings.google_client_secret", "test-secret")

        # Create a user with a password (credentials-based account)
        await test_db.create_user(
            email="existing@example.com",
            hashed_password=hash_password("password123"),
        )

        nonce = "pw-nonce"
        packed = _valid_packed_state(nonce=nonce)
        claims = _valid_id_claims(nonce=nonce, email="existing@example.com")
        fake_jwt = _make_id_token(claims)

        mock_exchange = AsyncMock(return_value={"id_token": fake_jwt})
        with patch("app.routers.google_oauth.exchange_google_code", mock_exchange):
            resp = await client.get(
                "/api/v1/oauth/google/callback",
                params={"code": "google-auth-code", "state": packed},
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert "account_exists" in resp.headers["location"]

    async def test_callback_missing_code(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        resp = await client.get(
            "/api/v1/oauth/google/callback",
            params={"state": "some-state"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_failed" in resp.headers["location"]

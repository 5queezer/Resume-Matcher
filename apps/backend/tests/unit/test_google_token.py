"""Unit tests for Google token exchange and ID token validation."""

import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.google import (
    exchange_google_code,
    parse_id_token,
    validate_id_token_claims,
)


def _make_jwt_payload(claims: dict) -> str:
    """Build a fake JWT with the given payload (no signature verification needed)."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(b"fakesig").decode().rstrip("=")
    return f"{header}.{payload}.{sig}"


class TestParseIdToken:
    def test_parse_valid_token(self) -> None:
        claims = {"sub": "123", "email": "test@gmail.com", "iss": "https://accounts.google.com"}
        token = _make_jwt_payload(claims)
        result = parse_id_token(token)
        assert result["sub"] == "123"
        assert result["email"] == "test@gmail.com"

    def test_parse_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid ID token format"):
            parse_id_token("not.a.valid.jwt.token")

    def test_parse_too_few_parts(self) -> None:
        with pytest.raises(ValueError, match="Invalid ID token format"):
            parse_id_token("onlyonepart")


class TestValidateIdTokenClaims:
    def _valid_claims(self, **overrides) -> dict:
        claims = {
            "iss": "https://accounts.google.com",
            "aud": "my-client-id",
            "exp": int(time.time()) + 300,
            "nonce": "expected-nonce",
            "sub": "google-user-123",
            "email": "user@gmail.com",
            "email_verified": True,
        }
        claims.update(overrides)
        return claims

    def test_valid_claims_pass(self) -> None:
        claims = self._valid_claims()
        result = validate_id_token_claims(claims, "my-client-id", "expected-nonce")
        assert result["sub"] == "google-user-123"

    def test_wrong_issuer(self) -> None:
        claims = self._valid_claims(iss="https://evil.com")
        with pytest.raises(ValueError, match="Invalid issuer"):
            validate_id_token_claims(claims, "my-client-id", "expected-nonce")

    def test_accounts_google_com_issuer_accepted(self) -> None:
        claims = self._valid_claims(iss="accounts.google.com")
        result = validate_id_token_claims(claims, "my-client-id", "expected-nonce")
        assert result["sub"] == "google-user-123"

    def test_wrong_audience(self) -> None:
        claims = self._valid_claims(aud="wrong-client")
        with pytest.raises(ValueError, match="Audience mismatch"):
            validate_id_token_claims(claims, "my-client-id", "expected-nonce")

    def test_expired_token(self) -> None:
        claims = self._valid_claims(exp=int(time.time()) - 100)
        with pytest.raises(ValueError, match="ID token expired"):
            validate_id_token_claims(claims, "my-client-id", "expected-nonce")

    def test_wrong_nonce(self) -> None:
        claims = self._valid_claims(nonce="wrong-nonce")
        with pytest.raises(ValueError, match="Nonce mismatch"):
            validate_id_token_claims(claims, "my-client-id", "expected-nonce")


class TestExchangeGoogleCode:
    async def test_successful_exchange(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "at",
            "id_token": "fake.jwt.token",
            "token_type": "Bearer",
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.auth.google.httpx.AsyncClient", return_value=mock_client):
            result = await exchange_google_code(
                code="auth-code",
                redirect_uri="http://localhost:8000/api/v1/oauth/google/callback",
                client_id="cid",
                client_secret="csecret",
            )
        assert result["id_token"] == "fake.jwt.token"

    async def test_failed_exchange(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 400

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.auth.google.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ValueError, match="Google token exchange failed"):
                await exchange_google_code(
                    code="bad-code",
                    redirect_uri="http://localhost:8000/callback",
                    client_id="cid",
                    client_secret="csecret",
                )

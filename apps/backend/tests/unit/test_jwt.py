"""Tests for JWT access token creation and verification."""

import time

import pytest
from app.auth.jwt import create_access_token, verify_access_token

TEST_SECRET = "test-secret-key-for-unit-tests-only"


class TestJWT:
    def test_create_returns_string(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        assert isinstance(token, str)
        assert len(token) > 0

    def test_verify_valid_token(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        claims = verify_access_token(token, secret=TEST_SECRET)
        assert claims["sub"] == "user-123"
        assert claims["email"] == "test@example.com"

    def test_verify_expired_token(self) -> None:
        token = create_access_token(
            user_id="user-123",
            email="test@example.com",
            secret=TEST_SECRET,
            expires_minutes=0,
        )
        time.sleep(1)
        with pytest.raises(ValueError, match="[Ee]xpired"):
            verify_access_token(token, secret=TEST_SECRET)

    def test_verify_invalid_signature(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        with pytest.raises(ValueError):
            verify_access_token(token, secret="wrong-secret")

    def test_verify_malformed_token(self) -> None:
        with pytest.raises(ValueError):
            verify_access_token("not.a.jwt", secret=TEST_SECRET)

    def test_token_contains_iss_claim(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        claims = verify_access_token(token, secret=TEST_SECRET)
        assert claims["iss"] == "resume-matcher"

    def test_token_contains_exp_and_iat(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        claims = verify_access_token(token, secret=TEST_SECRET)
        assert "exp" in claims
        assert "iat" in claims
        assert claims["exp"] > claims["iat"]

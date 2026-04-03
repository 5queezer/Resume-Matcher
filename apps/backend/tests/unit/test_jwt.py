"""Tests for RS256 JWT access token creation and verification."""

import time

import pytest
from app.auth.jwt import create_access_token, verify_access_token
from app.auth.keys import load_rsa_keys, reset_keys


@pytest.fixture(autouse=True)
def _rsa_keys():
    """Load test RSA keys for all JWT tests."""
    from joserfc.jwk import RSAKey
    reset_keys()
    key = RSAKey.generate_key(2048)
    load_rsa_keys(pem_data=key.as_pem(private=True).decode("utf-8"))
    yield
    reset_keys()


class TestJWT:
    def test_create_returns_string(self) -> None:
        token = create_access_token(user_id="user-123", email="test@example.com")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_verify_valid_token(self) -> None:
        token = create_access_token(user_id="user-123", email="test@example.com")
        claims = verify_access_token(token)
        assert claims["sub"] == "user-123"
        assert claims["email"] == "test@example.com"

    def test_verify_expired_token(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", expires_minutes=-1
        )
        with pytest.raises(ValueError, match="[Ee]xpired"):
            verify_access_token(token)

    def test_verify_wrong_key(self) -> None:
        """Token signed with one key should not verify with another."""
        from joserfc.jwk import RSAKey
        token = create_access_token(user_id="user-123", email="test@example.com")
        # Load a different key
        reset_keys()
        other_key = RSAKey.generate_key(2048)
        load_rsa_keys(pem_data=other_key.as_pem(private=True).decode("utf-8"))
        with pytest.raises(ValueError):
            verify_access_token(token)

    def test_verify_malformed_token(self) -> None:
        with pytest.raises(ValueError):
            verify_access_token("not.a.jwt")

    def test_token_contains_iss_claim(self) -> None:
        token = create_access_token(user_id="user-123", email="test@example.com")
        claims = verify_access_token(token)
        assert claims["iss"] == "resume-matcher"

    def test_token_contains_exp_and_iat(self) -> None:
        token = create_access_token(user_id="user-123", email="test@example.com")
        claims = verify_access_token(token)
        assert "exp" in claims
        assert "iat" in claims
        assert claims["exp"] > claims["iat"]

    def test_token_header_has_kid_and_rs256(self) -> None:
        from joserfc import jwt as jose_jwt
        from app.auth.keys import get_kid, get_public_key
        token = create_access_token(user_id="u1", email="e@x.com")
        obj = jose_jwt.decode(token, get_public_key())
        assert obj.header["alg"] == "RS256"
        assert obj.header["kid"] == get_kid()

    def test_tampered_token_rejected(self) -> None:
        token = create_access_token(user_id="u1", email="e@x.com")
        parts = token.split(".")
        parts[1] = parts[1][:-4] + "XXXX"
        tampered = ".".join(parts)
        with pytest.raises(ValueError):
            verify_access_token(tampered)

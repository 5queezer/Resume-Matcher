"""Tests for .well-known metadata endpoints."""
import pytest


class TestJWKS:
    @pytest.mark.anyio
    async def test_jwks_returns_public_key(self, client):
        resp = await client.get("/.well-known/jwks.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        assert len(data["keys"]) == 1
        key = data["keys"][0]
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"
        assert key["use"] == "sig"
        assert "kid" in key
        assert "n" in key
        assert "e" in key
        assert "d" not in key


class TestProtectedResourceMetadata:
    @pytest.mark.anyio
    async def test_returns_required_fields(self, client):
        resp = await client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.json()
        assert data["resource"] == "http://test"
        assert data["authorization_servers"] == ["http://test"]
        assert data["bearer_methods_supported"] == ["header"]


class TestOAuthServerMetadata:
    @pytest.mark.anyio
    async def test_includes_registration_and_jwks(self, client):
        resp = await client.get("/.well-known/oauth-authorization-server")
        data = resp.json()
        assert data["registration_endpoint"] is not None
        assert "register" in data["registration_endpoint"]
        assert data["jwks_uri"] is not None
        assert "jwks" in data["jwks_uri"]

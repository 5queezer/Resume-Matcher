"""Tests for claude.ai compatibility proxy endpoints."""
import pytest


class TestClaudeCompat:
    @pytest.mark.anyio
    async def test_root_register_proxies(self, client):
        resp = await client.post("/register", json={
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "client_name": "Claude via root",
        })
        assert resp.status_code == 201
        assert "client_id" in resp.json()

    @pytest.mark.anyio
    async def test_root_authorize_redirects(self, client):
        resp = await client.get(
            "/authorize",
            params={"response_type": "code", "client_id": "test"},
            follow_redirects=False,
        )
        assert resp.status_code == 307
        assert "/api/v1/oauth/authorize" in resp.headers["location"]

    @pytest.mark.anyio
    async def test_root_token_reaches_endpoint(self, client):
        resp = await client.post("/token", json={
            "grant_type": "authorization_code",
            "code": "invalid",
            "code_verifier": "test",
            "client_id": "test",
            "redirect_uri": "http://localhost/callback",
        })
        # Should reach the real token endpoint (400 because code is invalid, not 404)
        assert resp.status_code == 400

"""Tests for Dynamic Client Registration (RFC 7591)."""
import pytest


class TestDCR:
    @pytest.mark.anyio
    async def test_register_new_client(self, client):
        resp = await client.post("/api/v1/oauth/register", json={
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "client_name": "Claude",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "client_id" in data
        assert data["client_name"] == "Claude"
        assert data["token_endpoint_auth_method"] == "none"
        assert "client_id_issued_at" in data

    @pytest.mark.anyio
    async def test_register_without_redirect_uris_fails(self, client):
        resp = await client.post("/api/v1/oauth/register", json={
            "client_name": "Bad Client",
        })
        assert resp.status_code == 422  # validation error

    @pytest.mark.anyio
    async def test_defaults_auth_method_to_none(self, client):
        resp = await client.post("/api/v1/oauth/register", json={
            "redirect_uris": ["https://example.com/callback"],
        })
        assert resp.status_code == 201
        assert resp.json()["token_endpoint_auth_method"] == "none"


class TestDBClientValidation:
    @pytest.mark.anyio
    async def test_unknown_client_rejected(self, client):
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "x@test.com", "password": "pass",
            "client_id": "unknown-client",
            "redirect_uri": "http://evil.com/callback",
            "code_challenge": "abc",
        })
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_wrong_redirect_uri_rejected(self, client, test_db):
        await test_db.create_oauth_client(
            client_id="test-client",
            redirect_uris=["http://legit.com/callback"],
        )
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "x@test.com", "password": "pass",
            "client_id": "test-client",
            "redirect_uri": "http://evil.com/callback",
            "code_challenge": "abc",
        })
        assert resp.status_code == 400

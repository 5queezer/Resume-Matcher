"""Tests for OAuthClient database operations."""
import pytest


class TestOAuthClientCRUD:
    @pytest.mark.anyio
    async def test_create_oauth_client(self, test_db):
        client = await test_db.create_oauth_client(
            client_name="Test App",
            redirect_uris=["http://localhost:3000/callback"],
        )
        assert client["client_id"] is not None
        assert client["client_name"] == "Test App"
        assert client["token_endpoint_auth_method"] == "none"
        assert client["is_active"] is True

    @pytest.mark.anyio
    async def test_get_oauth_client(self, test_db):
        created = await test_db.create_oauth_client(
            client_name="App",
            redirect_uris=["http://example.com/cb"],
        )
        fetched = await test_db.get_oauth_client(created["client_id"])
        assert fetched is not None
        assert fetched["client_id"] == created["client_id"]

    @pytest.mark.anyio
    async def test_get_nonexistent_returns_none(self, test_db):
        result = await test_db.get_oauth_client("nonexistent")
        assert result is None

    @pytest.mark.anyio
    async def test_create_with_explicit_id(self, test_db):
        client = await test_db.create_oauth_client(
            client_id="resume-matcher-web",
            client_name="Resume Matcher Web",
            redirect_uris=["http://localhost:3000/callback"],
        )
        assert client["client_id"] == "resume-matcher-web"

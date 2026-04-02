"""Integration tests for health and status endpoints."""

from unittest.mock import AsyncMock, patch


class TestHealthEndpoint:
    """GET /api/v1/health"""

    @patch("app.routers.health.check_llm_health", new_callable=AsyncMock)
    async def test_health_returns_healthy(self, mock_health, client):
        mock_health.return_value = {
            "healthy": True,
            "provider": "openai",
            "model": "gpt-4",
        }
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    @patch("app.routers.health.check_llm_health", new_callable=AsyncMock)
    async def test_health_returns_degraded(self, mock_health, client):
        mock_health.return_value = {
            "healthy": False,
            "provider": "openai",
            "model": "gpt-4",
            "error_code": "api_key_missing",
        }
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"


class TestStatusEndpoint:
    """GET /api/v1/status"""

    @patch("app.routers.health.db")
    @patch("app.routers.health.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_status_ready(self, mock_config, mock_health, mock_db, client):
        mock_config.return_value = type("C", (), {"api_key": "sk-test", "provider": "openai"})()
        mock_health.return_value = {"healthy": True}
        mock_db.get_stats.return_value = {
            "total_resumes": 1,
            "total_jobs": 0,
            "total_improvements": 0,
            "has_master_resume": True,
        }
        resp = await client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["llm_healthy"] is True
        assert data["has_master_resume"] is True

    @patch("app.routers.health.db")
    @patch("app.routers.health.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_status_setup_required(self, mock_config, mock_health, mock_db, client):
        mock_config.return_value = type("C", (), {"api_key": "", "provider": "openai"})()
        mock_health.return_value = {"healthy": False}
        mock_db.get_stats.return_value = {
            "total_resumes": 0,
            "total_jobs": 0,
            "total_improvements": 0,
            "has_master_resume": False,
        }
        resp = await client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "setup_required"

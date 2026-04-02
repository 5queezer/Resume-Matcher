"""Test DATABASE_URL configuration."""

import pytest
from app.config import Settings


def test_default_database_url_is_sqlite():
    """Default DATABASE_URL should be a local SQLite file."""
    s = Settings(llm_api_key="test")
    assert s.effective_database_url.startswith("sqlite+aiosqlite:///")
    assert "database.db" in s.effective_database_url


def test_database_url_from_env(monkeypatch):
    """DATABASE_URL env var should override default."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/rm")
    s = Settings(llm_api_key="test")
    assert s.effective_database_url == "postgresql+asyncpg://user:pass@localhost/rm"

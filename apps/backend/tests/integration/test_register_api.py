"""Integration tests for user registration."""

import pytest


class TestRegister:
    async def test_register_success(self, client) -> None:
        resp = await client.post("/api/v1/auth/register", json={
            "email": "new@example.com",
            "password": "securepassword123",
            "display_name": "New User",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "new@example.com"
        assert data["display_name"] == "New User"
        assert "id" in data
        assert "password" not in data

    async def test_register_duplicate_email(self, client) -> None:
        await client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "password": "password123456",
        })
        resp = await client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "password": "password123456",
        })
        assert resp.status_code == 409

    async def test_register_invalid_email(self, client) -> None:
        resp = await client.post("/api/v1/auth/register", json={
            "email": "not-an-email",
            "password": "password123456",
        })
        assert resp.status_code == 422

    async def test_register_short_password(self, client) -> None:
        resp = await client.post("/api/v1/auth/register", json={
            "email": "short@example.com",
            "password": "short",
        })
        assert resp.status_code == 422

    async def test_register_no_display_name(self, client) -> None:
        resp = await client.post("/api/v1/auth/register", json={
            "email": "noname@example.com",
            "password": "password123456",
        })
        assert resp.status_code == 201
        assert resp.json()["display_name"] is None

"""Tests for auth-related database operations."""

import pytest
from app.database import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite://")
    await database.init()
    yield database
    await database.close()


class TestUserOperations:
    async def test_create_user(self, db: Database) -> None:
        user = await db.create_user(
            email="test@example.com",
            hashed_password="$argon2id$fakehash",
            display_name="Test User",
        )
        assert user["email"] == "test@example.com"
        assert user["display_name"] == "Test User"
        assert "id" in user
        assert "hashed_password" not in user  # never expose password hash in default dict

    async def test_get_user_by_email(self, db: Database) -> None:
        await db.create_user(email="find@example.com", hashed_password="hash")
        user = await db.get_user_by_email("find@example.com")
        assert user is not None
        assert user["email"] == "find@example.com"

    async def test_get_user_by_email_not_found(self, db: Database) -> None:
        user = await db.get_user_by_email("nobody@example.com")
        assert user is None

    async def test_get_user_by_email_includes_password_hash(self, db: Database) -> None:
        await db.create_user(email="auth@example.com", hashed_password="$argon2id$hash")
        user = await db.get_user_by_email("auth@example.com")
        assert user["hashed_password"] == "$argon2id$hash"

    async def test_get_user_by_id(self, db: Database) -> None:
        created = await db.create_user(email="id@example.com", hashed_password="hash")
        user = await db.get_user_by_id(created["id"])
        assert user is not None
        assert user["email"] == "id@example.com"
        assert "hashed_password" not in user

    async def test_get_user_by_id_not_found(self, db: Database) -> None:
        user = await db.get_user_by_id("nonexistent-id")
        assert user is None

    async def test_duplicate_email_raises(self, db: Database) -> None:
        await db.create_user(email="dup@example.com", hashed_password="hash")
        with pytest.raises(Exception):
            await db.create_user(email="dup@example.com", hashed_password="hash2")

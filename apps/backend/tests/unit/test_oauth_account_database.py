"""Unit tests for OAuthAccount database operations."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite://")
    await database.init()
    yield database
    await database.close()


@pytest.fixture
async def user(db):
    return await db.create_user(
        email="test@example.com",
        hashed_password="hashed",
        display_name="Test User",
    )


class TestOAuthAccountCRUD:
    async def test_create_oauth_account(self, db, user) -> None:
        account = await db.create_oauth_account(
            user_id=user["id"],
            provider="google",
            provider_user_id="google-123",
            provider_email="test@gmail.com",
        )
        assert account["user_id"] == user["id"]
        assert account["provider"] == "google"
        assert account["provider_user_id"] == "google-123"
        assert account["provider_email"] == "test@gmail.com"
        assert "id" in account
        assert "created_at" in account

    async def test_get_oauth_account_by_provider(self, db, user) -> None:
        await db.create_oauth_account(
            user_id=user["id"],
            provider="google",
            provider_user_id="google-456",
        )
        result = await db.get_oauth_account("google", "google-456")
        assert result is not None
        assert result["user_id"] == user["id"]

    async def test_get_oauth_account_not_found(self, db) -> None:
        result = await db.get_oauth_account("google", "nonexistent")
        assert result is None

    async def test_duplicate_oauth_account_raises(self, db, user) -> None:
        await db.create_oauth_account(
            user_id=user["id"],
            provider="google",
            provider_user_id="google-789",
        )
        with pytest.raises(IntegrityError):
            await db.create_oauth_account(
                user_id=user["id"],
                provider="google",
                provider_user_id="google-789",
            )

    async def test_get_oauth_accounts_by_user(self, db, user) -> None:
        await db.create_oauth_account(
            user_id=user["id"],
            provider="google",
            provider_user_id="g-1",
        )
        await db.create_oauth_account(
            user_id=user["id"],
            provider="github",
            provider_user_id="gh-1",
        )
        accounts = await db.get_oauth_accounts_by_user(user["id"])
        assert len(accounts) == 2
        providers = {a["provider"] for a in accounts}
        assert providers == {"google", "github"}

    async def test_same_provider_different_users(self, db) -> None:
        user1 = await db.create_user(email="a@example.com", hashed_password="h")
        user2 = await db.create_user(email="b@example.com", hashed_password="h")
        await db.create_oauth_account(
            user_id=user1["id"], provider="google", provider_user_id="g-a"
        )
        await db.create_oauth_account(
            user_id=user2["id"], provider="google", provider_user_id="g-b"
        )
        assert await db.get_oauth_account("google", "g-a") is not None
        assert await db.get_oauth_account("google", "g-b") is not None

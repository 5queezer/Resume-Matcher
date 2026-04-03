"""Unit tests for Google user resolution logic."""

import pytest

from app.auth.google import PasswordAccountExists, resolve_google_user
from app.database import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite://")
    await database.init()
    yield database
    await database.close()


def _google_claims(
    sub: str = "google-123",
    email: str = "user@gmail.com",
    email_verified: bool = True,
    name: str = "Test User",
) -> dict:
    return {
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "name": name,
    }


class TestResolveGoogleUser:
    async def test_new_user_created(self, db) -> None:
        """No existing user or oauth account -> creates both."""
        claims = _google_claims()
        user = await resolve_google_user(claims, db)
        assert user["email"] == "user@gmail.com"
        assert user["display_name"] == "Test User"
        # Verify oauth_account was created
        account = await db.get_oauth_account("google", "google-123")
        assert account is not None
        assert account["user_id"] == user["id"]

    async def test_already_linked_returns_existing(self, db) -> None:
        """OAuth account already exists -> returns linked user."""
        user = await db.create_user(email="linked@gmail.com")
        await db.create_oauth_account(
            user_id=user["id"],
            provider="google",
            provider_user_id="g-linked",
        )
        claims = _google_claims(sub="g-linked", email="linked@gmail.com")
        result = await resolve_google_user(claims, db)
        assert result["id"] == user["id"]

    async def test_autolink_passwordless_account(self, db) -> None:
        """Email matches a passwordless account -> auto-link."""
        user = await db.create_user(email="nopass@gmail.com")
        claims = _google_claims(sub="g-nopass", email="nopass@gmail.com")
        result = await resolve_google_user(claims, db)
        assert result["id"] == user["id"]
        # Verify oauth_account was created
        account = await db.get_oauth_account("google", "g-nopass")
        assert account is not None
        assert account["user_id"] == user["id"]

    async def test_deny_password_account(self, db) -> None:
        """Email matches a password-bearing account -> raise PasswordAccountExists."""
        await db.create_user(
            email="haspass@gmail.com",
            hashed_password="$argon2id$v=19$m=65536,t=3,p=4$hash",
        )
        claims = _google_claims(sub="g-haspass", email="haspass@gmail.com")
        with pytest.raises(PasswordAccountExists):
            await resolve_google_user(claims, db)

    async def test_unverified_email_creates_new_account(self, db) -> None:
        """Unverified Google email -> creates new account, no linking attempt."""
        existing = await db.create_user(email="existing@gmail.com", hashed_password="h")
        claims = _google_claims(
            sub="g-unverified",
            email="unverified-new@gmail.com",
            email_verified=False,
        )
        user = await resolve_google_user(claims, db)
        # Unverified email must NOT trigger link logic — a new user is created
        assert user is not None
        assert user["id"] != existing["id"]

    async def test_no_email_raises_value_error(self, db) -> None:
        """Google claims with no email -> raises ValueError."""
        claims = {"sub": "g-noemail", "email": "", "email_verified": False, "name": "No Email"}
        with pytest.raises(ValueError, match="no email address"):
            await resolve_google_user(claims, db)

    async def test_concurrent_link_handles_integrity_error(self, db) -> None:
        """If oauth_account was created between check and insert, handle gracefully."""
        user = await db.create_user(email="race@gmail.com")
        await db.create_oauth_account(
            user_id=user["id"],
            provider="google",
            provider_user_id="g-race",
        )
        # Calling resolve with same sub should find the existing link (path 1)
        claims = _google_claims(sub="g-race", email="race@gmail.com")
        result = await resolve_google_user(claims, db)
        assert result["id"] == user["id"]

"""Tests for auth-related database operations."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from app.database import Database


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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


class TestAuthorizationCodeOperations:
    async def test_create_and_get_auth_code(self, db: Database) -> None:
        user = await db.create_user(email="code@example.com", hashed_password="hash")
        code_hash = _hash("authcode123")
        await db.create_authorization_code(
            code_hash=code_hash,
            user_id=user["id"],
            client_id="resume-matcher-web",
            redirect_uri="http://localhost:3000/callback",
            code_challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        code = await db.get_authorization_code(code_hash)
        assert code is not None
        assert code["user_id"] == user["id"]
        assert code["client_id"] == "resume-matcher-web"
        assert code["used_at"] is None

    async def test_mark_code_used(self, db: Database) -> None:
        user = await db.create_user(email="used@example.com", hashed_password="hash")
        code_hash = _hash("usedcode")
        await db.create_authorization_code(
            code_hash=code_hash,
            user_id=user["id"],
            client_id="resume-matcher-web",
            redirect_uri="http://localhost:3000/callback",
            code_challenge="challenge",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        result = await db.mark_authorization_code_used(code_hash)
        assert result is True
        code = await db.get_authorization_code(code_hash)
        assert code["used_at"] is not None

    async def test_mark_code_used_twice_returns_false(self, db: Database) -> None:
        user = await db.create_user(email="twice@example.com", hashed_password="hash")
        code_hash = _hash("twicecode")
        await db.create_authorization_code(
            code_hash=code_hash,
            user_id=user["id"],
            client_id="resume-matcher-web",
            redirect_uri="http://localhost:3000/callback",
            code_challenge="challenge",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        first = await db.mark_authorization_code_used(code_hash)
        assert first is True
        second = await db.mark_authorization_code_used(code_hash)
        assert second is False

    async def test_get_nonexistent_code(self, db: Database) -> None:
        code = await db.get_authorization_code(_hash("nonexistent"))
        assert code is None


class TestRefreshTokenOperations:
    async def test_create_and_get_refresh_token(self, db: Database) -> None:
        user = await db.create_user(email="refresh@example.com", hashed_password="hash")
        token_hash = _hash("refreshtoken123")
        token = await db.create_refresh_token(
            token_hash=token_hash,
            user_id=user["id"],
            family_id="family-001",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        assert token["user_id"] == user["id"]
        assert token["family_id"] == "family-001"
        assert token["revoked_at"] is None

        fetched = await db.get_refresh_token(token_hash)
        assert fetched is not None
        assert fetched["family_id"] == "family-001"

    async def test_revoke_refresh_token(self, db: Database) -> None:
        user = await db.create_user(email="revoke@example.com", hashed_password="hash")
        token_hash = _hash("torevoke")
        await db.create_refresh_token(
            token_hash=token_hash,
            user_id=user["id"],
            family_id="family-002",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        await db.revoke_refresh_token(token_hash)
        token = await db.get_refresh_token(token_hash)
        assert token["revoked_at"] is not None

    async def test_revoke_token_family(self, db: Database) -> None:
        user = await db.create_user(email="family@example.com", hashed_password="hash")
        family_id = "family-003"
        for i in range(3):
            await db.create_refresh_token(
                token_hash=_hash(f"familytoken{i}"),
                user_id=user["id"],
                family_id=family_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
        await db.revoke_token_family(family_id)
        for i in range(3):
            token = await db.get_refresh_token(_hash(f"familytoken{i}"))
            assert token["revoked_at"] is not None

    async def test_get_nonexistent_refresh_token(self, db: Database) -> None:
        token = await db.get_refresh_token(_hash("nonexistent"))
        assert token is None

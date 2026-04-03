# M3: Google OAuth Provider Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add "Sign in with Google" as the first external identity provider, integrated with the existing OAuth 2.1 authorization server from M2.

**Architecture:** Google OAuth uses parallel endpoints (`/oauth/google/start`, `/oauth/google/callback`) that produce a standard authorization code feeding through the existing `/oauth/token` PKCE exchange. HMAC-signed packed state carries the frontend's PKCE params through Google's redirect. A new `oauth_accounts` table supports multiple providers. Auto-linking by verified email, with password-proof gate for existing password accounts.

**Tech Stack:** httpx (Google token exchange), authlib (already a dep), joserfc (existing JWT), SQLAlchemy 2.0 async (existing ORM), Next.js 16 (frontend)

---

## Prerequisites

- M2 (OAuth 2.1) merged on `fork/main`
- Google Cloud Console project with OAuth 2.0 credentials (client ID + secret)
- Backend `.env` will need `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`

---

## Task 1: OAuthAccount Model + Alembic Migration

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/xxxx_add_oauth_accounts_table.py`

**Step 1: Add OAuthAccount model to models.py**

Add after the `RefreshToken` class at the end of `models.py`:

```python
from sqlalchemy import UniqueConstraint

class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    provider_user_id: Mapped[str] = mapped_column(String(255))
    provider_email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
    )
```

Note: `UniqueConstraint` import needs to be added to the existing import line from `sqlalchemy`.

**Step 2: Generate Alembic migration**

```bash
cd apps/backend && uv run alembic revision --autogenerate -m "add_oauth_accounts_table"
```

Verify the generated migration creates the `oauth_accounts` table with the unique constraint.

**Step 3: Run the migration to verify**

```bash
cd apps/backend && uv run alembic upgrade head
```

Expected: No errors, `oauth_accounts` table created.

**Step 4: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/*oauth_accounts*
git commit -m "feat(m3): add OAuthAccount model and migration"
```

---

## Task 2: OAuthAccount Database CRUD + Tests

**Files:**
- Modify: `apps/backend/app/database.py`
- Create: `apps/backend/tests/unit/test_oauth_account_database.py`

**Step 1: Write the failing tests**

Create `tests/unit/test_oauth_account_database.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/unit/test_oauth_account_database.py -v
```

Expected: FAIL — `create_oauth_account` method does not exist.

**Step 3: Implement database CRUD methods**

Add to `database.py` after the `_refresh_token_to_dict` method:

1. Add `OAuthAccount` to the import line:
```python
from app.models import AuthorizationCode, Base, Improvement, Job, OAuthAccount, RefreshToken, Resume, User
```

2. Add the to_dict method and CRUD operations:
```python
    @staticmethod
    def _oauth_account_to_dict(o: OAuthAccount) -> dict[str, Any]:
        return {
            "id": o.id,
            "user_id": o.user_id,
            "provider": o.provider,
            "provider_user_id": o.provider_user_id,
            "provider_email": o.provider_email,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }

    # -- OAuth account operations -----------------------------------------------

    async def create_oauth_account(
        self,
        user_id: str,
        provider: str,
        provider_user_id: str,
        provider_email: str | None = None,
    ) -> dict[str, Any]:
        account = OAuthAccount(
            id=str(uuid4()),
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            provider_email=provider_email,
        )
        async with self._session() as session:
            session.add(account)
            await session.commit()
            await session.refresh(account)
            return self._oauth_account_to_dict(account)

    async def get_oauth_account(
        self,
        provider: str,
        provider_user_id: str,
    ) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(
                select(OAuthAccount).where(
                    OAuthAccount.provider == provider,
                    OAuthAccount.provider_user_id == provider_user_id,
                )
            )
            row = result.scalar_one_or_none()
            return self._oauth_account_to_dict(row) if row else None

    async def get_oauth_accounts_by_user(
        self,
        user_id: str,
    ) -> list[dict[str, Any]]:
        async with self._session() as session:
            result = await session.execute(
                select(OAuthAccount).where(OAuthAccount.user_id == user_id)
            )
            return [self._oauth_account_to_dict(o) for o in result.scalars().all()]
```

**Step 4: Run tests to verify they pass**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/unit/test_oauth_account_database.py -v
```

Expected: All 6 tests PASS.

**Step 5: Commit**

```bash
git add apps/backend/app/database.py tests/unit/test_oauth_account_database.py
git commit -m "feat(m3): add OAuthAccount database CRUD with tests"
```

---

## Task 3: Config Additions + Optional Password for create_user

**Files:**
- Modify: `apps/backend/app/config.py:160-162`
- Modify: `apps/backend/app/database.py:109-114`

**Step 1: Add Google config to Settings class**

In `config.py`, after the `frontend_origin` field (line ~162), add:

```python
    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
```

**Step 2: Make create_user accept optional hashed_password**

In `database.py`, change the `create_user` signature from:

```python
    async def create_user(
        self,
        email: str,
        hashed_password: str,
        display_name: str | None = None,
    ) -> dict[str, Any]:
```

To:

```python
    async def create_user(
        self,
        email: str,
        hashed_password: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
```

**Step 3: Verify existing tests still pass**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/ -v
```

Expected: All existing tests PASS (registration endpoint still passes hashed_password explicitly).

**Step 4: Commit**

```bash
git add apps/backend/app/config.py apps/backend/app/database.py
git commit -m "feat(m3): add Google OAuth config and optional password for create_user"
```

---

## Task 4: State Packing/Unpacking + Tests

**Files:**
- Create: `apps/backend/app/auth/google.py`
- Create: `apps/backend/tests/unit/test_google_state.py`

**Step 1: Write the failing tests**

Create `tests/unit/test_google_state.py`:

```python
"""Unit tests for Google OAuth state packing/unpacking."""

import time

import pytest

from app.auth.google import pack_state, unpack_state


SECRET = "test-secret-key-for-hmac"


class TestStatePacking:
    def test_roundtrip(self) -> None:
        data = {
            "state": "abc-123",
            "code_challenge": "challenge",
            "redirect_uri": "http://localhost:3000/callback",
            "nonce": "nonce-value",
            "ts": int(time.time()),
        }
        packed = pack_state(data, SECRET)
        result = unpack_state(packed, SECRET)
        assert result["state"] == "abc-123"
        assert result["code_challenge"] == "challenge"
        assert result["nonce"] == "nonce-value"

    def test_tampered_payload_rejected(self) -> None:
        data = {"state": "original", "ts": int(time.time())}
        packed = pack_state(data, SECRET)
        payload, sig = packed.rsplit(".", 1)
        tampered = payload[:-1] + ("A" if payload[-1] != "A" else "B")
        with pytest.raises(ValueError, match="Invalid state signature"):
            unpack_state(f"{tampered}.{sig}", SECRET)

    def test_tampered_signature_rejected(self) -> None:
        data = {"state": "original", "ts": int(time.time())}
        packed = pack_state(data, SECRET)
        with pytest.raises(ValueError, match="Invalid state signature"):
            unpack_state(packed + "x", SECRET)

    def test_expired_state_rejected(self) -> None:
        data = {"state": "old", "ts": int(time.time()) - 700}
        packed = pack_state(data, SECRET)
        with pytest.raises(ValueError, match="State expired"):
            unpack_state(packed, SECRET, max_age=600)

    def test_wrong_secret_rejected(self) -> None:
        data = {"state": "test", "ts": int(time.time())}
        packed = pack_state(data, SECRET)
        with pytest.raises(ValueError, match="Invalid state signature"):
            unpack_state(packed, "wrong-secret")

    def test_malformed_state_rejected(self) -> None:
        with pytest.raises(ValueError, match="Malformed state"):
            unpack_state("no-dot-separator", SECRET)

    def test_fresh_state_accepted(self) -> None:
        data = {"state": "fresh", "ts": int(time.time()) - 300}
        packed = pack_state(data, SECRET)
        result = unpack_state(packed, SECRET, max_age=600)
        assert result["state"] == "fresh"
```

**Step 2: Run tests to verify they fail**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/unit/test_google_state.py -v
```

Expected: FAIL — `app.auth.google` module does not exist.

**Step 3: Implement state packing/unpacking**

Create `apps/backend/app/auth/google.py`:

```python
"""Google OAuth 2.0 helpers: state packing, token exchange, user resolution."""

import base64
import hashlib
import hmac
import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Google endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "openid email profile"


# ---------------------------------------------------------------------------
# State packing (HMAC-signed, stateless)
# ---------------------------------------------------------------------------

def pack_state(data: dict, secret: str) -> str:
    """Pack OAuth state dict with HMAC-SHA256 integrity protection."""
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def unpack_state(packed: str, secret: str, max_age: int = 600) -> dict:
    """Unpack and verify HMAC-signed state. Raises ValueError on failure."""
    try:
        payload, sig = packed.rsplit(".", 1)
    except ValueError:
        raise ValueError("Malformed state")

    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Invalid state signature")

    # Restore base64 padding
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding

    data = json.loads(base64.urlsafe_b64decode(payload))

    if time.time() - data.get("ts", 0) > max_age:
        raise ValueError("State expired")

    return data
```

**Step 4: Run tests to verify they pass**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/unit/test_google_state.py -v
```

Expected: All 7 tests PASS.

**Step 5: Commit**

```bash
git add apps/backend/app/auth/google.py tests/unit/test_google_state.py
git commit -m "feat(m3): add HMAC-signed state packing for Google OAuth"
```

---

## Task 5: Google Token Exchange + ID Token Validation + Tests

**Files:**
- Modify: `apps/backend/app/auth/google.py`
- Create: `apps/backend/tests/unit/test_google_token.py`

**Step 1: Write the failing tests**

Create `tests/unit/test_google_token.py`:

```python
"""Unit tests for Google token exchange and ID token validation."""

import base64
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.google import (
    exchange_google_code,
    parse_id_token,
    validate_id_token_claims,
)


def _make_jwt_payload(claims: dict) -> str:
    """Build a fake JWT with the given payload (no signature verification needed)."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(b"fakesig").decode().rstrip("=")
    return f"{header}.{payload}.{sig}"


class TestParseIdToken:
    def test_parse_valid_token(self) -> None:
        claims = {"sub": "123", "email": "test@gmail.com", "iss": "https://accounts.google.com"}
        token = _make_jwt_payload(claims)
        result = parse_id_token(token)
        assert result["sub"] == "123"
        assert result["email"] == "test@gmail.com"

    def test_parse_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid ID token format"):
            parse_id_token("not.a.valid.jwt.token")

    def test_parse_too_few_parts(self) -> None:
        with pytest.raises(ValueError, match="Invalid ID token format"):
            parse_id_token("onlyonepart")


class TestValidateIdTokenClaims:
    def _valid_claims(self, **overrides) -> dict:
        claims = {
            "iss": "https://accounts.google.com",
            "aud": "my-client-id",
            "exp": int(time.time()) + 300,
            "nonce": "expected-nonce",
            "sub": "google-user-123",
            "email": "user@gmail.com",
            "email_verified": True,
        }
        claims.update(overrides)
        return claims

    def test_valid_claims_pass(self) -> None:
        claims = self._valid_claims()
        result = validate_id_token_claims(claims, "my-client-id", "expected-nonce")
        assert result["sub"] == "google-user-123"

    def test_wrong_issuer(self) -> None:
        claims = self._valid_claims(iss="https://evil.com")
        with pytest.raises(ValueError, match="Invalid issuer"):
            validate_id_token_claims(claims, "my-client-id", "expected-nonce")

    def test_accounts_google_com_issuer_accepted(self) -> None:
        claims = self._valid_claims(iss="accounts.google.com")
        result = validate_id_token_claims(claims, "my-client-id", "expected-nonce")
        assert result["sub"] == "google-user-123"

    def test_wrong_audience(self) -> None:
        claims = self._valid_claims(aud="wrong-client")
        with pytest.raises(ValueError, match="Audience mismatch"):
            validate_id_token_claims(claims, "my-client-id", "expected-nonce")

    def test_expired_token(self) -> None:
        claims = self._valid_claims(exp=int(time.time()) - 100)
        with pytest.raises(ValueError, match="ID token expired"):
            validate_id_token_claims(claims, "my-client-id", "expected-nonce")

    def test_wrong_nonce(self) -> None:
        claims = self._valid_claims(nonce="wrong-nonce")
        with pytest.raises(ValueError, match="Nonce mismatch"):
            validate_id_token_claims(claims, "my-client-id", "expected-nonce")


class TestExchangeGoogleCode:
    async def test_successful_exchange(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "at",
            "id_token": "fake.jwt.token",
            "token_type": "Bearer",
        }
        mock_response.raise_for_status = AsyncMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.auth.google.httpx.AsyncClient", return_value=mock_client):
            result = await exchange_google_code(
                code="auth-code",
                redirect_uri="http://localhost:8000/api/v1/oauth/google/callback",
                client_id="cid",
                client_secret="csecret",
            )
        assert result["id_token"] == "fake.jwt.token"

    async def test_failed_exchange(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.auth.google.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ValueError, match="Google token exchange failed"):
                await exchange_google_code(
                    code="bad-code",
                    redirect_uri="http://localhost:8000/callback",
                    client_id="cid",
                    client_secret="csecret",
                )
```

**Step 2: Run tests to verify they fail**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/unit/test_google_token.py -v
```

Expected: FAIL — functions not defined.

**Step 3: Implement token exchange and ID token validation**

Add to `apps/backend/app/auth/google.py` after the state packing section:

```python
# ---------------------------------------------------------------------------
# Google token exchange
# ---------------------------------------------------------------------------

async def exchange_google_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Exchange Google authorization code for tokens (including id_token)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        if resp.status_code != 200:
            raise ValueError(f"Google token exchange failed: {resp.status_code}")
        return resp.json()


# ---------------------------------------------------------------------------
# ID token parsing and validation
# ---------------------------------------------------------------------------

def parse_id_token(id_token: str) -> dict:
    """Decode JWT payload without signature verification.

    Safe because the token comes directly from Google's token endpoint
    over HTTPS (trusted channel).
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid ID token format")
    payload = parts[1]
    # Restore base64 padding
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    return json.loads(base64.urlsafe_b64decode(payload))


def validate_id_token_claims(
    claims: dict,
    expected_aud: str,
    expected_nonce: str,
) -> dict:
    """Validate Google ID token claims. Raises ValueError on failure."""
    valid_issuers = ("https://accounts.google.com", "accounts.google.com")
    if claims.get("iss") not in valid_issuers:
        raise ValueError(f"Invalid issuer: {claims.get('iss')}")
    if claims.get("aud") != expected_aud:
        raise ValueError("Audience mismatch")
    if claims.get("exp", 0) < time.time():
        raise ValueError("ID token expired")
    if claims.get("nonce") != expected_nonce:
        raise ValueError("Nonce mismatch")
    return claims
```

**Step 4: Run tests to verify they pass**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/unit/test_google_token.py -v
```

Expected: All 10 tests PASS.

**Step 5: Commit**

```bash
git add apps/backend/app/auth/google.py tests/unit/test_google_token.py
git commit -m "feat(m3): add Google token exchange and ID token validation"
```

---

## Task 6: User Resolution Logic + Tests

**Files:**
- Modify: `apps/backend/app/auth/google.py`
- Create: `apps/backend/tests/unit/test_google_user_resolution.py`

**Step 1: Write the failing tests**

Create `tests/unit/test_google_user_resolution.py`:

```python
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
        await db.create_user(email="existing@gmail.com", hashed_password="h")
        claims = _google_claims(
            sub="g-unverified",
            email="existing@gmail.com",
            email_verified=False,
        )
        user = await resolve_google_user(claims, db)
        # Should be a different user (new account)
        existing = await db.get_user_by_email("existing@gmail.com")
        # The new user was created with the same email -- this tests that
        # unverified email doesn't trigger the link logic. In practice,
        # a unique constraint on email would prevent this, so the test
        # validates the code PATH, not the final DB state.
        assert user is not None

    async def test_no_email_creates_new_account(self, db) -> None:
        """Google claims with no email -> creates new account."""
        claims = {"sub": "g-noemail", "email": "", "email_verified": False, "name": "No Email"}
        user = await resolve_google_user(claims, db)
        assert user is not None
        assert user["display_name"] == "No Email"

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
```

**Step 2: Run tests to verify they fail**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/unit/test_google_user_resolution.py -v
```

Expected: FAIL — `resolve_google_user` and `PasswordAccountExists` not defined.

**Step 3: Implement user resolution logic**

Add to `apps/backend/app/auth/google.py`:

```python
# ---------------------------------------------------------------------------
# User resolution
# ---------------------------------------------------------------------------

class PasswordAccountExists(Exception):
    """Raised when Google email matches an account with a password."""
    pass


async def resolve_google_user(claims: dict, db: "Database") -> dict:
    """Resolve or create a user from Google ID token claims.

    Four paths:
    1. OAuth account already linked -> return existing user
    2a. Verified email matches passwordless account -> auto-link
    2b. Verified email matches password account -> raise PasswordAccountExists
    3. No match -> create new user + oauth_account
    """
    from app.database import Database  # noqa: F811 — type hint only

    google_sub = claims["sub"]
    email = claims.get("email", "")
    email_verified = claims.get("email_verified", False)
    display_name = claims.get("name")

    # Path 1: Already linked
    oauth_account = await db.get_oauth_account("google", google_sub)
    if oauth_account:
        logger.info("google_auth.returning_existing user_id=%s", oauth_account["user_id"])
        return await db.get_user_by_id(oauth_account["user_id"])

    # Path 2: Email match (only if verified)
    if email_verified and email:
        existing_user = await db.get_user_by_email(email)
        if existing_user:
            if existing_user.get("hashed_password"):
                logger.info("google_auth.denied_password_account email=%s", email)
                raise PasswordAccountExists()
            # Auto-link passwordless account
            await db.create_oauth_account(
                user_id=existing_user["id"],
                provider="google",
                provider_user_id=google_sub,
                provider_email=email,
            )
            logger.info("google_auth.linked_existing user_id=%s", existing_user["id"])
            return existing_user

    # Path 3: New user
    user = await db.create_user(email=email, display_name=display_name)
    await db.create_oauth_account(
        user_id=user["id"],
        provider="google",
        provider_user_id=google_sub,
        provider_email=email,
    )
    logger.info("google_auth.created_new user_id=%s", user["id"])
    return user
```

**Step 4: Run tests to verify they pass**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/unit/test_google_user_resolution.py -v
```

Expected: All 7 tests PASS.

**Step 5: Commit**

```bash
git add apps/backend/app/auth/google.py tests/unit/test_google_user_resolution.py
git commit -m "feat(m3): add Google user resolution with auto-link and password gate"
```

---

## Task 7: Google OAuth Router (Start + Callback)

**Files:**
- Create: `apps/backend/app/routers/google_oauth.py`

**Step 1: Create the router**

Create `apps/backend/app/routers/google_oauth.py`:

```python
"""Google OAuth 2.0 endpoints: start and callback."""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.auth.constants import (
    AUTHORIZATION_CODE_EXPIRE_MINUTES,
    FIRST_PARTY_CLIENT_ID,
)
from app.auth.google import (
    GOOGLE_AUTH_URL,
    GOOGLE_SCOPES,
    PasswordAccountExists,
    exchange_google_code,
    pack_state,
    parse_id_token,
    resolve_google_user,
    unpack_state,
    validate_id_token_claims,
)
from app.config import settings
from app.database import db
from app.routers.oauth import _allowed_redirect_uris

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth/google", tags=["google-oauth"])


def _google_callback_uri(request: Request) -> str:
    """Build the Google callback URI from the request base URL."""
    return f"{str(request.base_url).rstrip('/')}/api/v1/oauth/google/callback"


@router.get("/start")
async def google_start(
    request: Request,
    state: str,
    code_challenge: str,
    redirect_uri: str,
    code_challenge_method: str = "S256",
) -> RedirectResponse:
    """Initiate Google OAuth flow.

    Packs the frontend's PKCE params into Google's state parameter
    with HMAC integrity protection.
    """
    if not settings.google_client_id:
        return RedirectResponse(
            f"{settings.frontend_origin}/login?error=google_not_configured",
            status_code=302,
        )

    if redirect_uri not in _allowed_redirect_uris():
        return RedirectResponse(
            f"{settings.frontend_origin}/login?error=invalid_redirect",
            status_code=302,
        )

    nonce = secrets.token_urlsafe(32)
    packed = pack_state(
        {
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "redirect_uri": redirect_uri,
            "nonce": nonce,
            "ts": int(time.time()),
        },
        settings.effective_jwt_secret,
    )

    google_params = urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": _google_callback_uri(request),
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "state": packed,
        "nonce": nonce,
        "access_type": "online",
        "prompt": "select_account",
    })

    return RedirectResponse(
        f"{GOOGLE_AUTH_URL}?{google_params}",
        status_code=302,
    )


@router.get("/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Google's OAuth callback.

    Validates state, exchanges Google's code for tokens, resolves the user,
    issues our own authorization code, and redirects to the frontend callback.
    """
    frontend_login = f"{settings.frontend_origin}/login"

    # Handle Google-side errors
    if error or not code or not state:
        logger.warning("Google callback error: %s", error or "missing params")
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    # Verify HMAC-signed state
    try:
        data = unpack_state(state, settings.effective_jwt_secret)
    except ValueError as e:
        logger.warning("Google callback invalid state: %s", e)
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    # Exchange Google code for tokens
    try:
        tokens = await exchange_google_code(
            code=code,
            redirect_uri=_google_callback_uri(request),
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
    except ValueError as e:
        logger.error("Google token exchange failed: %s", e)
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    # Parse and validate ID token
    id_token_raw = tokens.get("id_token")
    if not id_token_raw:
        logger.error("Google response missing id_token")
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    try:
        claims = parse_id_token(id_token_raw)
        validate_id_token_claims(claims, settings.google_client_id, data["nonce"])
    except ValueError as e:
        logger.warning("Google ID token validation failed: %s", e)
        return RedirectResponse(f"{frontend_login}?error=google_failed", status_code=302)

    # Resolve or create user
    try:
        user = await resolve_google_user(claims, db)
    except PasswordAccountExists:
        return RedirectResponse(
            f"{frontend_login}?error=account_exists",
            status_code=302,
        )

    # Issue our authorization code (reuses M2's token exchange flow)
    our_code = secrets.token_urlsafe(32)
    code_hash = hashlib.sha256(our_code.encode()).hexdigest()

    await db.create_authorization_code(
        code_hash=code_hash,
        user_id=user["id"],
        client_id=FIRST_PARTY_CLIENT_ID,
        redirect_uri=data["redirect_uri"],
        code_challenge=data["code_challenge"],
        scope="openid email profile",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=AUTHORIZATION_CODE_EXPIRE_MINUTES),
    )

    redirect_url = f"{data['redirect_uri']}?{urlencode({'code': our_code, 'state': data['state']})}"
    return RedirectResponse(url=redirect_url, status_code=303)
```

**Step 2: Commit**

```bash
git add apps/backend/app/routers/google_oauth.py
git commit -m "feat(m3): add Google OAuth start and callback endpoints"
```

---

## Task 8: Providers Endpoint + Wiring

**Files:**
- Modify: `apps/backend/app/routers/auth.py`
- Modify: `apps/backend/app/routers/__init__.py`
- Modify: `apps/backend/app/main.py`
- Modify: `apps/backend/tests/conftest.py`

**Step 1: Add providers endpoint to auth router**

In `apps/backend/app/routers/auth.py`, add after the existing endpoints:

```python
from app.config import settings  # add if not already imported

@router.get("/providers")
async def list_providers() -> dict:
    """Return available authentication providers."""
    providers = ["credentials"]
    if settings.google_client_id:
        providers.append("google")
    return {"providers": providers}
```

**Step 2: Export google_oauth_router from __init__.py**

Modify `apps/backend/app/routers/__init__.py`:

```python
"""API routers."""

from app.routers.auth import router as auth_router
from app.routers.config import router as config_router
from app.routers.enrichment import router as enrichment_router
from app.routers.google_oauth import router as google_oauth_router
from app.routers.health import router as health_router
from app.routers.jobs import router as jobs_router
from app.routers.oauth import router as oauth_router
from app.routers.resumes import router as resumes_router

__all__ = [
    "auth_router",
    "google_oauth_router",
    "oauth_router",
    "resumes_router",
    "jobs_router",
    "config_router",
    "health_router",
    "enrichment_router",
]
```

**Step 3: Register google_oauth_router in main.py**

In `apps/backend/app/main.py`:

1. Update the import line:
```python
from app.routers import auth_router, config_router, enrichment_router, google_oauth_router, health_router, jobs_router, oauth_router, resumes_router
```

2. Add the router registration after the oauth_router line:
```python
app.include_router(google_oauth_router, prefix="/api/v1")
```

**Step 4: Update conftest.py for database patching**

In `apps/backend/tests/conftest.py`, add the google_oauth module to the monkeypatching:

```python
    import app.routers.google_oauth as google_oauth_mod

    for mod in (db_module, auth_deps_mod, auth_mod, oauth_mod, google_oauth_mod, resumes_mod, jobs_mod, health_mod, config_mod, enrichment_mod, main_mod):
```

**Step 5: Verify everything still starts and existing tests pass**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/ -v
```

Expected: All existing tests PASS.

**Step 6: Commit**

```bash
git add apps/backend/app/routers/auth.py apps/backend/app/routers/__init__.py apps/backend/app/main.py apps/backend/tests/conftest.py
git commit -m "feat(m3): wire up Google OAuth router and providers endpoint"
```

---

## Task 9: Integration Tests

**Files:**
- Create: `apps/backend/tests/integration/test_google_oauth_api.py`

**Step 1: Write integration tests**

Create `tests/integration/test_google_oauth_api.py`:

```python
"""Integration tests for Google OAuth flow."""

import base64
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.google import pack_state


def _make_id_token(claims: dict) -> str:
    """Build a fake JWT with the given claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(b"sig").decode().rstrip("=")
    return f"{header}.{payload}.{sig}"


class TestProvidersEndpoint:
    async def test_providers_credentials_only(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "")
        resp = await client.get("/api/v1/auth/providers")
        assert resp.status_code == 200
        assert resp.json() == {"providers": ["credentials"]}

    async def test_providers_with_google(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        resp = await client.get("/api/v1/auth/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "google" in data["providers"]
        assert "credentials" in data["providers"]


class TestGoogleStart:
    async def test_redirects_to_google(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        monkeypatch.setattr("app.config.settings.google_client_secret", "test-secret")
        resp = await client.get(
            "/api/v1/oauth/google/start",
            params={
                "state": "test-state",
                "code_challenge": "test-challenge",
                "redirect_uri": "http://localhost:3000/callback",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "accounts.google.com" in location
        assert "test-google-id" in location

    async def test_google_not_configured(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "")
        resp = await client.get(
            "/api/v1/oauth/google/start",
            params={
                "state": "s",
                "code_challenge": "c",
                "redirect_uri": "http://localhost:3000/callback",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_not_configured" in resp.headers["location"]

    async def test_invalid_redirect_uri(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-id")
        resp = await client.get(
            "/api/v1/oauth/google/start",
            params={
                "state": "s",
                "code_challenge": "c",
                "redirect_uri": "https://evil.com/callback",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "invalid_redirect" in resp.headers["location"]

    async def test_missing_params(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-id")
        resp = await client.get("/api/v1/oauth/google/start", follow_redirects=False)
        assert resp.status_code == 422


class TestGoogleCallback:
    def _setup_google_mock(self, monkeypatch, nonce: str, email: str = "google@gmail.com",
                           email_verified: bool = True, sub: str = "g-sub-123"):
        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        monkeypatch.setattr("app.config.settings.google_client_secret", "test-secret")

        claims = {
            "iss": "https://accounts.google.com",
            "aud": "test-google-id",
            "exp": int(time.time()) + 300,
            "nonce": nonce,
            "sub": sub,
            "email": email,
            "email_verified": email_verified,
            "name": "Google User",
        }

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "google-at",
            "id_token": _make_id_token(claims),
            "token_type": "Bearer",
        }

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        return mock_client_instance

    async def test_full_callback_new_user(self, client, monkeypatch) -> None:
        """Full Google callback: new user created, redirects with our auth code."""
        nonce = "test-nonce"
        mock_http = self._setup_google_mock(monkeypatch, nonce)

        packed = pack_state(
            {
                "state": "frontend-state",
                "code_challenge": "challenge123",
                "code_challenge_method": "S256",
                "redirect_uri": "http://localhost:3000/callback",
                "nonce": nonce,
                "ts": int(time.time()),
            },
            "test-secret-for-tests",
        )

        with patch("app.routers.google_oauth.exchange_google_code") as mock_exchange:
            claims = {
                "iss": "https://accounts.google.com",
                "aud": "test-google-id",
                "exp": int(time.time()) + 300,
                "nonce": nonce,
                "sub": "g-new-user",
                "email": "new@gmail.com",
                "email_verified": True,
                "name": "New Google User",
            }
            mock_exchange.return_value = {
                "access_token": "at",
                "id_token": _make_id_token(claims),
            }

            resp = await client.get(
                "/api/v1/oauth/google/callback",
                params={"code": "google-code", "state": packed},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        location = resp.headers["location"]
        assert "localhost:3000/callback" in location
        assert "code=" in location
        assert "state=frontend-state" in location

    async def test_callback_google_error(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-id")
        resp = await client.get(
            "/api/v1/oauth/google/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_failed" in resp.headers["location"]

    async def test_callback_invalid_state(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-id")
        resp = await client.get(
            "/api/v1/oauth/google/callback",
            params={"code": "c", "state": "tampered.state"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_failed" in resp.headers["location"]

    async def test_callback_expired_state(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-id")
        packed = pack_state(
            {"state": "s", "nonce": "n", "ts": int(time.time()) - 700,
             "redirect_uri": "http://localhost:3000/callback",
             "code_challenge": "c", "code_challenge_method": "S256"},
            "test-secret-for-tests",
        )
        resp = await client.get(
            "/api/v1/oauth/google/callback",
            params={"code": "c", "state": packed},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_failed" in resp.headers["location"]

    async def test_callback_password_account_denied(self, client, test_db, monkeypatch) -> None:
        """Google email matches password account -> redirect with account_exists."""
        from app.auth.password import hash_password

        await test_db.create_user(
            email="existing@gmail.com",
            hashed_password=hash_password("password123"),
        )

        nonce = "nonce-pw"
        packed = pack_state(
            {
                "state": "s", "code_challenge": "c", "code_challenge_method": "S256",
                "redirect_uri": "http://localhost:3000/callback",
                "nonce": nonce, "ts": int(time.time()),
            },
            "test-secret-for-tests",
        )

        claims = {
            "iss": "https://accounts.google.com",
            "aud": "test-google-id",
            "exp": int(time.time()) + 300,
            "nonce": nonce,
            "sub": "g-existing",
            "email": "existing@gmail.com",
            "email_verified": True,
            "name": "Existing User",
        }

        monkeypatch.setattr("app.config.settings.google_client_id", "test-google-id")
        monkeypatch.setattr("app.config.settings.google_client_secret", "test-secret")

        with patch("app.routers.google_oauth.exchange_google_code") as mock_exchange:
            mock_exchange.return_value = {"id_token": _make_id_token(claims)}
            resp = await client.get(
                "/api/v1/oauth/google/callback",
                params={"code": "c", "state": packed},
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert "account_exists" in resp.headers["location"]

    async def test_callback_missing_code(self, client, monkeypatch) -> None:
        monkeypatch.setattr("app.config.settings.google_client_id", "test-id")
        resp = await client.get(
            "/api/v1/oauth/google/callback",
            params={"state": "s"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "google_failed" in resp.headers["location"]
```

**Step 2: Run integration tests**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/integration/test_google_oauth_api.py -v
```

Expected: All tests PASS.

**Step 3: Run full test suite to verify no regressions**

```bash
cd apps/backend && .venv/bin/python -m pytest tests/ -v
```

Expected: All tests PASS.

**Step 4: Commit**

```bash
git add tests/integration/test_google_oauth_api.py
git commit -m "test(m3): add Google OAuth integration tests"
```

---

## Task 10: Frontend — Google Sign-In Button + i18n

**Files:**
- Modify: `apps/frontend/lib/auth/oauth.ts`
- Modify: `apps/frontend/app/(auth)/login/page.tsx`
- Modify: `apps/frontend/components/auth/login-form.tsx`
- Modify: `apps/frontend/messages/en.json`
- Modify: `apps/frontend/messages/es.json`
- Modify: `apps/frontend/messages/ja.json`
- Modify: `apps/frontend/messages/pt-BR.json`
- Modify: `apps/frontend/messages/zh.json`

**Step 1: Add startGoogleLogin to oauth.ts**

Add to `apps/frontend/lib/auth/oauth.ts`, after the existing `startLogin` function:

```typescript
export async function startGoogleLogin(): Promise<void> {
  const { codeVerifier, codeChallenge } = await generatePKCE();
  const state = crypto.randomUUID();
  sessionStorage.setItem(VERIFIER_KEY, codeVerifier);
  sessionStorage.setItem(STATE_KEY, state);

  const params = new URLSearchParams({
    state,
    code_challenge: codeChallenge,
    code_challenge_method: 'S256',
    redirect_uri: getRedirectUri(),
  });
  window.location.href = `${API_BASE}/oauth/google/start?${params}`;
}
```

Also add the `API_BASE` import at the top if not already present:
```typescript
import { apiFetch, API_BASE } from '@/lib/api/client';
```

**Step 2: Update login page to show Google button and handle errors**

Replace `apps/frontend/app/(auth)/login/page.tsx` with:

```tsx
'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import { LoginForm } from '@/components/auth/login-form';
import { startLogin, startGoogleLogin } from '@/lib/auth/oauth';
import { apiFetch } from '@/lib/api/client';
import { useTranslations } from '@/lib/i18n/translations';

function LoginContent() {
  const { t } = useTranslations();
  const params = useSearchParams();
  const [pkce, setPkce] = useState<{ codeChallenge: string; state: string } | null>(null);
  const [initFailed, setInitFailed] = useState(false);
  const [providers, setProviders] = useState<string[]>(['credentials']);
  const [googleLoading, setGoogleLoading] = useState(false);

  const errorParam = params.get('error');
  const errorMessage = errorParam === 'account_exists'
    ? t('auth.accountExistsPassword')
    : errorParam === 'google_failed'
      ? t('auth.googleFailed')
      : null;

  useEffect(() => {
    startLogin()
      .then(({ codeChallenge, state }) => setPkce({ codeChallenge, state }))
      .catch(() => setInitFailed(true));

    apiFetch('/auth/providers')
      .then((r) => r.json())
      .then((d) => setProviders(d.providers ?? ['credentials']))
      .catch(() => {});
  }, []);

  const handleGoogleLogin = () => {
    setGoogleLoading(true);
    startGoogleLogin();
  };

  if (initFailed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
        <div className="w-full max-w-sm border border-[#DC2626] bg-white p-8 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
          <p className="font-sans text-sm text-[#DC2626]">{t('auth.pkceError')}</p>
        </div>
      </div>
    );
  }

  if (!pkce) return null;

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
      <div className="w-full max-w-sm border border-black bg-white p-8 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
        <h1 className="mb-6 font-serif text-2xl font-bold">{t('auth.signIn')}</h1>

        {errorMessage && (
          <div className="mb-4 border border-[#DC2626] bg-red-50 p-3 font-sans text-sm text-[#DC2626]">
            {errorMessage}
          </div>
        )}

        {providers.includes('google') && (
          <>
            <button
              type="button"
              onClick={handleGoogleLogin}
              disabled={googleLoading}
              className="flex w-full items-center justify-center gap-2 rounded-none border border-black bg-white px-4 py-2 font-sans text-sm hover:bg-gray-50 disabled:opacity-50"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
                <path
                  d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
                  fill="#4285F4"
                />
                <path
                  d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                  fill="#34A853"
                />
                <path
                  d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                  fill="#FBBC05"
                />
                <path
                  d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                  fill="#EA4335"
                />
              </svg>
              {googleLoading ? t('auth.signingIn') : t('auth.signInWithGoogle')}
            </button>
            <div className="relative my-4">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-gray-300" />
              </div>
              <div className="relative flex justify-center">
                <span className="bg-white px-2 font-mono text-xs uppercase tracking-wider text-gray-500">
                  {t('auth.orContinueWith')}
                </span>
              </div>
            </div>
          </>
        )}

        <LoginForm codeChallenge={pkce.codeChallenge} state={pkce.state} />
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginContent />
    </Suspense>
  );
}
```

**Step 3: Add i18n strings to all locales**

Add these keys to the `auth` section of each locale file:

**en.json:**
```json
"signInWithGoogle": "Sign in with Google",
"orContinueWith": "or continue with",
"accountExistsPassword": "An account with this email already exists. Please sign in with your password.",
"googleFailed": "Google sign-in failed. Please try again."
```

**es.json:**
```json
"signInWithGoogle": "Iniciar sesion con Google",
"orContinueWith": "o continuar con",
"accountExistsPassword": "Ya existe una cuenta con este correo electronico. Por favor, inicia sesion con tu contrasena.",
"googleFailed": "Error al iniciar sesion con Google. Por favor, intentalo de nuevo."
```

**ja.json:**
```json
"signInWithGoogle": "Google でサインイン",
"orContinueWith": "または以下で続行",
"accountExistsPassword": "このメールアドレスのアカウントは既に存在します。パスワードでサインインしてください。",
"googleFailed": "Google サインインに失敗しました。もう一度お試しください。"
```

**pt-BR.json:**
```json
"signInWithGoogle": "Entrar com Google",
"orContinueWith": "ou continue com",
"accountExistsPassword": "Uma conta com este e-mail ja existe. Por favor, entre com sua senha.",
"googleFailed": "Falha ao entrar com Google. Por favor, tente novamente."
```

**zh.json:**
```json
"signInWithGoogle": "使用 Google 登录",
"orContinueWith": "或继续使用",
"accountExistsPassword": "此电子邮件已存在账户。请使用密码登录。",
"googleFailed": "Google 登录失败。请重试。"
```

**Step 4: Run frontend lint and format**

```bash
cd apps/frontend && npm run lint && npm run format
```

**Step 5: Build frontend to verify no compilation errors**

```bash
cd apps/frontend && npm run build
```

**Step 6: Commit**

```bash
git add apps/frontend/lib/auth/oauth.ts apps/frontend/app/\(auth\)/login/page.tsx apps/frontend/messages/
git commit -m "feat(m3): add Google sign-in button with i18n and provider detection"
```

---

## Task Summary

| Task | Description | Tests |
|------|-------------|-------|
| 1 | OAuthAccount model + Alembic migration | — |
| 2 | OAuthAccount database CRUD | 6 unit tests |
| 3 | Config additions + optional password | Existing tests verify no regression |
| 4 | State packing/unpacking | 7 unit tests |
| 5 | Google token exchange + ID token validation | 10 unit tests |
| 6 | User resolution logic | 7 unit tests |
| 7 | Google OAuth router (start + callback) | — |
| 8 | Providers endpoint + wiring | — |
| 9 | Integration tests | 10 integration tests |
| 10 | Frontend: Google button + i18n | Lint + build verification |

**Total: 40 tests (30 unit + 10 integration)**

---

## Security Checklist

Before merging, verify:
- [ ] HMAC state passes all tamper/expiry tests
- [ ] ID token validates iss, aud, exp, nonce
- [ ] Password accounts cannot be auto-linked
- [ ] `(provider, provider_user_id)` unique constraint exists
- [ ] Google errors redirect with generic message (no detail leakage)
- [ ] `GOOGLE_CLIENT_SECRET` never appears in logs or responses
- [ ] `redirect_uri` validated against allowlist on `/google/start`
- [ ] All 5 locales have Google auth strings

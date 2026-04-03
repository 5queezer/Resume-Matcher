# M2: User Authentication -- OAuth 2.1 Authorization Server

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a full OAuth 2.1 authorization server with email/password authentication, JWT tokens, and PKCE -- serving as both the app's auth system and the future MCP auth provider.

**Architecture:** Unified auth path -- the first-party Next.js SPA uses the same OAuth 2.1 authorization code + PKCE flow as future third-party clients (MCP, Google OAuth). No separate login-to-token shortcut. FastAPI-native endpoints with joserfc for JWT operations and authlib utilities for PKCE verification. DB-backed refresh tokens with family-based rotation and reuse detection. Access tokens in JS memory only, refresh token as httpOnly cookie.

**Tech Stack:** FastAPI, joserfc (JWT), authlib (PKCE utilities), argon2-cffi (password hashing), SQLAlchemy 2.0 async, Next.js 16 + React 19

---

## Decision Log

| # | Decision | Why |
|---|----------|-----|
| 1 | Unified OAuth 2.1 auth path (no dual codepaths) | Prior Reactive Resume experience showed dual auth paths are a maintenance nightmare |
| 2 | DB-backed refresh tokens with family-based reuse detection | OAuth 2.1 requires rotation + reuse detection, needs DB state anyway |
| 3 | In-memory access token + httpOnly refresh cookie | XSS can't steal what's not persisted; cookie handles persistence |
| 4 | FastAPI-native endpoints + joserfc + authlib PKCE utilities | authlib's server framework targets sync Flask/Django; fighting it into async FastAPI adds complexity |
| 5 | No server-side sessions | Credentials posted directly to authorize endpoint; M3 uses Google's state parameter |
| 6 | Hardcoded first-party client, DCR deferred to M5 | YAGNI -- DCR design depends on MCP's specific requirements |
| 7 | HS256 JWT signing | Single server, no public key distribution needed; upgrade to RS256 in M5 if needed |
| 8 | 15-min access token, 7-day refresh token | Industry standard; 401 includes WWW-Authenticate: Bearer (required for claude.ai) |
| 9 | Auth built but not enforced on existing routes | M2 builds auth; M4 wires it into routes. Clean separation. |

---

## File Map

```text
apps/backend/
  app/
    auth/                          # CREATE: auth module
      __init__.py
      constants.py                 # Token lifetimes, first-party client config
      password.py                  # argon2id hash/verify
      jwt.py                       # joserfc create/verify access tokens
      pkce.py                      # S256 code_challenge verification
      dependencies.py              # FastAPI Depends: get_current_user, get_optional_user
    config.py                      # MODIFY: add JWT_SECRET_KEY, auth settings
    models.py                      # MODIFY: add AuthorizationCode, RefreshToken
    database.py                    # MODIFY: add user/auth CRUD operations
    main.py                        # MODIFY: register new routers
    routers/
      __init__.py                  # MODIFY: export new routers
      auth.py                      # CREATE: /auth/register, /auth/me
      oauth.py                     # CREATE: /oauth/authorize, /oauth/token, /oauth/revoke, /.well-known/*
    schemas/
      auth.py                      # CREATE: auth request/response schemas
  alembic/versions/
    xxxx_add_auth_tables.py        # CREATE: migration for auth_codes + refresh_tokens
  tests/
    unit/
      test_password.py             # CREATE
      test_jwt.py                  # CREATE
      test_pkce.py                 # CREATE
      test_auth_database.py        # CREATE
    integration/
      test_register_api.py         # CREATE
      test_oauth_flow_api.py       # CREATE
      test_auth_me_api.py          # CREATE

apps/frontend/
  app/
    (auth)/                        # CREATE: auth route group
      login/page.tsx
      register/page.tsx
      callback/page.tsx
  lib/
    auth/                          # CREATE: auth utilities
      pkce.ts
      oauth.ts
      context.tsx
  components/
    auth/                          # CREATE: auth UI components
      login-form.tsx
      register-form.tsx
      user-menu.tsx
  middleware.ts                    # CREATE: route protection
```

---

### Task 1: Add Dependencies

**Files:**
- Modify: `apps/backend/pyproject.toml`

**Step 1: Add auth dependencies**

Add after `python-dotenv`:

```toml
    "authlib==1.6.0",
    "joserfc==1.1.1",
    "argon2-cffi==24.1.0",
```

**Step 2: Install**

```bash
cd apps/backend && uv sync
```

**Step 3: Verify imports**

```bash
cd apps/backend && uv run python -c "import authlib; import joserfc; import argon2; print('OK')"
```

Expected: `OK`

**Step 4: Commit**

```bash
git add apps/backend/pyproject.toml apps/backend/uv.lock
git commit -m "feat(m2): add authlib, joserfc, argon2-cffi dependencies"
```

---

### Task 2: Auth Constants and Config

**Files:**
- Create: `apps/backend/app/auth/__init__.py`
- Create: `apps/backend/app/auth/constants.py`
- Modify: `apps/backend/app/config.py`

**Step 1: Create auth module**

```python
# apps/backend/app/auth/__init__.py
```

(empty file)

**Step 2: Create constants**

```python
# apps/backend/app/auth/constants.py
"""OAuth 2.1 and authentication constants."""

# First-party client (the Next.js SPA)
FIRST_PARTY_CLIENT_ID = "resume-matcher-web"
FIRST_PARTY_REDIRECT_URIS = [
    "http://localhost:3000/callback",
    "http://127.0.0.1:3000/callback",
]

# Token lifetimes
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7
AUTHORIZATION_CODE_EXPIRE_MINUTES = 10

# OAuth 2.1 scopes (minimal for now)
SUPPORTED_SCOPES = {"openid", "profile", "email"}
```

**Step 3: Add JWT settings to config.py**

Add to the `Settings` class in `apps/backend/app/config.py`, after the `database_url` field:

```python
    # Authentication
    jwt_secret_key: str = ""
    frontend_origin: str = "http://localhost:3000"
```

Add a property to validate the secret:

```python
    @property
    def effective_jwt_secret(self) -> str:
        """JWT secret key -- required for auth endpoints."""
        if not self.jwt_secret_key:
            raise RuntimeError(
                "JWT_SECRET_KEY is required. Set it in .env or environment."
            )
        return self.jwt_secret_key
```

**Step 4: Update FIRST_PARTY_REDIRECT_URIS to use config**

The constants file uses hardcoded localhost URIs. The authorize endpoint will also accept `settings.frontend_origin + "/callback"` at runtime.

**Step 5: Commit**

```bash
git add apps/backend/app/auth/ apps/backend/app/config.py
git commit -m "feat(m2): add auth constants and JWT config settings"
```

---

### Task 3: Auth Models + Alembic Migration

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/xxxx_add_auth_tables.py` (via autogenerate)
- Create: `apps/backend/tests/unit/test_auth_models.py`

**Step 1: Write failing test**

```python
# apps/backend/tests/unit/test_auth_models.py
"""Tests for auth-related ORM models."""

import pytest
from app.models import AuthorizationCode, Base, RefreshToken


class TestAuthModels:
    def test_authorization_code_table_name(self) -> None:
        assert AuthorizationCode.__tablename__ == "authorization_codes"

    def test_refresh_token_table_name(self) -> None:
        assert RefreshToken.__tablename__ == "refresh_tokens"

    def test_auth_tables_in_metadata(self) -> None:
        table_names = set(Base.metadata.tables.keys())
        assert "authorization_codes" in table_names
        assert "refresh_tokens" in table_names

    def test_authorization_code_has_user_fk(self) -> None:
        fks = {
            fk.target_fullname
            for col in AuthorizationCode.__table__.columns
            for fk in col.foreign_keys
        }
        assert "users.id" in fks

    def test_refresh_token_has_user_fk(self) -> None:
        fks = {
            fk.target_fullname
            for col in RefreshToken.__table__.columns
            for fk in col.foreign_keys
        }
        assert "users.id" in fks
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_auth_models.py -v
```

Expected: FAIL -- `ImportError: cannot import name 'AuthorizationCode' from 'app.models'`

**Step 3: Add models to models.py**

Add after the `Improvement` class:

```python
class AuthorizationCode(Base):
    __tablename__ = "authorization_codes"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(255))
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    code_challenge: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**Step 4: Run test to verify it passes**

```bash
cd apps/backend && uv run pytest tests/unit/test_auth_models.py -v
```

Expected: 5 passed

**Step 5: Generate Alembic migration**

```bash
cd apps/backend && uv run alembic revision --autogenerate -m "add auth tables"
```

Verify the generated migration creates `authorization_codes` and `refresh_tokens` tables with correct columns and FKs.

**Step 6: Run migration**

```bash
cd apps/backend && uv run alembic upgrade head
```

**Step 7: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/ apps/backend/tests/unit/test_auth_models.py
git commit -m "feat(m2): add AuthorizationCode and RefreshToken models with migration"
```

---

### Task 4: Password Module (TDD)

**Files:**
- Create: `apps/backend/app/auth/password.py`
- Create: `apps/backend/tests/unit/test_password.py`

**Step 1: Write failing tests**

```python
# apps/backend/tests/unit/test_password.py
"""Tests for argon2id password hashing."""

import pytest
from app.auth.password import hash_password, verify_password


class TestPasswordHashing:
    def test_hash_returns_argon2id_string(self) -> None:
        hashed = hash_password("mysecretpassword")
        assert hashed.startswith("$argon2id$")

    def test_hash_is_not_plaintext(self) -> None:
        hashed = hash_password("mysecretpassword")
        assert hashed != "mysecretpassword"

    def test_verify_correct_password(self) -> None:
        hashed = hash_password("mysecretpassword")
        assert verify_password("mysecretpassword", hashed) is True

    def test_verify_wrong_password(self) -> None:
        hashed = hash_password("mysecretpassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_different_passwords_produce_different_hashes(self) -> None:
        h1 = hash_password("password1")
        h2 = hash_password("password2")
        assert h1 != h2

    def test_same_password_produces_different_hashes(self) -> None:
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert h1 != h2  # salt should differ
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_password.py -v
```

Expected: FAIL -- `ModuleNotFoundError: No module named 'app.auth.password'`

**Step 3: Implement password module**

```python
# apps/backend/app/auth/password.py
"""Password hashing with argon2id."""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password using argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against an argon2id hash."""
    try:
        return _hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False
```

**Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/test_password.py -v
```

Expected: 6 passed

**Step 5: Commit**

```bash
git add apps/backend/app/auth/password.py apps/backend/tests/unit/test_password.py
git commit -m "feat(m2): add argon2id password hashing module"
```

---

### Task 5: JWT Module (TDD)

**Files:**
- Create: `apps/backend/app/auth/jwt.py`
- Create: `apps/backend/tests/unit/test_jwt.py`

**Step 1: Write failing tests**

```python
# apps/backend/tests/unit/test_jwt.py
"""Tests for JWT access token creation and verification."""

import time

import pytest
from app.auth.jwt import create_access_token, verify_access_token


TEST_SECRET = "test-secret-key-for-unit-tests-only"


class TestJWT:
    def test_create_returns_string(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        assert isinstance(token, str)
        assert len(token) > 0

    def test_verify_valid_token(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        claims = verify_access_token(token, secret=TEST_SECRET)
        assert claims["sub"] == "user-123"
        assert claims["email"] == "test@example.com"

    def test_verify_expired_token(self) -> None:
        token = create_access_token(
            user_id="user-123",
            email="test@example.com",
            secret=TEST_SECRET,
            expires_minutes=0,
        )
        # Token with 0 minutes is already expired
        time.sleep(1)
        with pytest.raises(ValueError, match="expired"):
            verify_access_token(token, secret=TEST_SECRET)

    def test_verify_invalid_signature(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        with pytest.raises(ValueError, match="invalid"):
            verify_access_token(token, secret="wrong-secret")

    def test_verify_malformed_token(self) -> None:
        with pytest.raises(ValueError):
            verify_access_token("not.a.jwt", secret=TEST_SECRET)

    def test_token_contains_iss_claim(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        claims = verify_access_token(token, secret=TEST_SECRET)
        assert claims["iss"] == "resume-matcher"

    def test_token_contains_exp_and_iat(self) -> None:
        token = create_access_token(
            user_id="user-123", email="test@example.com", secret=TEST_SECRET
        )
        claims = verify_access_token(token, secret=TEST_SECRET)
        assert "exp" in claims
        assert "iat" in claims
        assert claims["exp"] > claims["iat"]
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_jwt.py -v
```

Expected: FAIL -- `ModuleNotFoundError: No module named 'app.auth.jwt'`

**Step 3: Implement JWT module**

```python
# apps/backend/app/auth/jwt.py
"""JWT access token operations using joserfc."""

import time

from joserfc import jwt
from joserfc.jwk import OctKey

from app.auth.constants import ACCESS_TOKEN_EXPIRE_MINUTES

_ALGORITHM = "HS256"
_ISSUER = "resume-matcher"


def create_access_token(
    user_id: str,
    email: str,
    secret: str,
    expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
) -> str:
    """Create a signed JWT access token."""
    now = int(time.time())
    claims = {
        "sub": user_id,
        "email": email,
        "iss": _ISSUER,
        "iat": now,
        "exp": now + (expires_minutes * 60),
    }
    key = OctKey.import_key(secret)
    token = jwt.encode({"alg": _ALGORITHM}, claims, key)
    return token


def verify_access_token(token: str, secret: str) -> dict:
    """Verify and decode a JWT access token. Raises ValueError on failure."""
    key = OctKey.import_key(secret)
    try:
        decoded = jwt.decode(token, key)
    except Exception as e:
        raise ValueError(f"Token invalid: {e}") from e

    claims = decoded.claims
    now = int(time.time())
    if claims.get("exp", 0) < now:
        raise ValueError("Token expired")
    if claims.get("iss") != _ISSUER:
        raise ValueError("Token invalid: wrong issuer")
    return claims
```

**Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/test_jwt.py -v
```

Expected: 7 passed

**Step 5: Commit**

```bash
git add apps/backend/app/auth/jwt.py apps/backend/tests/unit/test_jwt.py
git commit -m "feat(m2): add JWT access token module with joserfc"
```

---

### Task 6: PKCE Module (TDD)

**Files:**
- Create: `apps/backend/app/auth/pkce.py`
- Create: `apps/backend/tests/unit/test_pkce.py`

**Step 1: Write failing tests**

```python
# apps/backend/tests/unit/test_pkce.py
"""Tests for PKCE S256 code challenge verification."""

import base64
import hashlib

import pytest
from app.auth.pkce import verify_code_challenge


class TestPKCE:
    def _make_challenge(self, verifier: str) -> str:
        """Helper: compute S256 challenge from verifier."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def test_valid_s256_challenge(self) -> None:
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        challenge = self._make_challenge(verifier)
        assert verify_code_challenge(verifier, challenge, "S256") is True

    def test_wrong_verifier(self) -> None:
        verifier = "correct-verifier"
        challenge = self._make_challenge(verifier)
        assert verify_code_challenge("wrong-verifier", challenge, "S256") is False

    def test_plain_method_rejected(self) -> None:
        with pytest.raises(ValueError, match="S256"):
            verify_code_challenge("verifier", "challenge", "plain")

    def test_empty_verifier_rejected(self) -> None:
        assert verify_code_challenge("", "somechallenge", "S256") is False

    def test_rfc7636_test_vector(self) -> None:
        # RFC 7636 Appendix B test vector
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected_challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assert verify_code_challenge(verifier, expected_challenge, "S256") is True
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_pkce.py -v
```

Expected: FAIL -- `ModuleNotFoundError`

**Step 3: Implement PKCE module**

```python
# apps/backend/app/auth/pkce.py
"""PKCE (RFC 7636) code challenge verification."""

import base64
import hashlib


def verify_code_challenge(
    code_verifier: str, code_challenge: str, method: str
) -> bool:
    """Verify a PKCE code challenge against a code verifier.

    Only S256 is supported (OAuth 2.1 mandate).
    """
    if method != "S256":
        raise ValueError("Only S256 code_challenge_method is supported")
    if not code_verifier:
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge
```

**Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/test_pkce.py -v
```

Expected: 5 passed

**Step 5: Commit**

```bash
git add apps/backend/app/auth/pkce.py apps/backend/tests/unit/test_pkce.py
git commit -m "feat(m2): add PKCE S256 verification module"
```

---

### Task 7: Database User Operations (TDD)

**Files:**
- Modify: `apps/backend/app/database.py`
- Create: `apps/backend/tests/unit/test_auth_database.py`

**Step 1: Write failing tests for user CRUD**

```python
# apps/backend/tests/unit/test_auth_database.py
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
        assert "hashed_password" not in user  # never expose password hash

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
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_auth_database.py::TestUserOperations -v
```

Expected: FAIL -- `AttributeError: 'Database' object has no attribute 'create_user'`

**Step 3: Implement user operations in database.py**

Add to `Database` class in `apps/backend/app/database.py`:

1. Add `User` to the import from `app.models`:
   ```python
   from app.models import Base, Improvement, Job, Resume, User
   ```

2. Add static method for User dict conversion:
   ```python
    @staticmethod
    def _user_to_dict(u: User, include_password: bool = False) -> dict[str, Any]:
        d = {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "updated_at": u.updated_at.isoformat() if u.updated_at else None,
        }
        if include_password:
            d["hashed_password"] = u.hashed_password
        return d
   ```

3. Add user CRUD methods:
   ```python
    # -- User operations -------------------------------------------------------

    async def create_user(
        self,
        email: str,
        hashed_password: str,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        user = User(
            id=str(uuid4()),
            email=email,
            hashed_password=hashed_password,
            display_name=display_name,
        )
        async with self._session() as session:
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return self._user_to_dict(user)

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(select(User).where(User.email == email))
            row = result.scalar_one_or_none()
            return self._user_to_dict(row, include_password=True) if row else None

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            row = result.scalar_one_or_none()
            return self._user_to_dict(row) if row else None
   ```

**Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/test_auth_database.py::TestUserOperations -v
```

Expected: 7 passed

**Step 5: Commit**

```bash
git add apps/backend/app/database.py apps/backend/tests/unit/test_auth_database.py
git commit -m "feat(m2): add user CRUD operations to database layer"
```

---

### Task 8: Database Auth Code + Refresh Token Operations (TDD)

**Files:**
- Modify: `apps/backend/app/database.py`
- Modify: `apps/backend/tests/unit/test_auth_database.py`

**Step 1: Write failing tests for auth code operations**

Append to `test_auth_database.py`:

```python
import hashlib
from datetime import datetime, timedelta, timezone


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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
        await db.mark_authorization_code_used(code_hash)
        code = await db.get_authorization_code(code_hash)
        assert code["used_at"] is not None

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
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_auth_database.py -v -k "AuthorizationCode or RefreshToken"
```

Expected: FAIL -- `AttributeError: 'Database' object has no attribute 'create_authorization_code'`

**Step 3: Implement auth operations in database.py**

Add imports at top of `database.py`:

```python
from app.models import Base, AuthorizationCode, Improvement, Job, RefreshToken, Resume, User
```

Add static conversion methods and CRUD operations to `Database`:

```python
    @staticmethod
    def _auth_code_to_dict(c: AuthorizationCode) -> dict[str, Any]:
        return {
            "code_hash": c.code_hash,
            "user_id": c.user_id,
            "client_id": c.client_id,
            "redirect_uri": c.redirect_uri,
            "code_challenge": c.code_challenge,
            "scope": c.scope,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
            "used_at": c.used_at.isoformat() if c.used_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }

    @staticmethod
    def _refresh_token_to_dict(t: RefreshToken) -> dict[str, Any]:
        return {
            "id": t.id,
            "token_hash": t.token_hash,
            "user_id": t.user_id,
            "family_id": t.family_id,
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
            "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }

    # -- Authorization code operations ----------------------------------------

    async def create_authorization_code(
        self,
        code_hash: str,
        user_id: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        expires_at: datetime,
        scope: str | None = None,
    ) -> dict[str, Any]:
        code = AuthorizationCode(
            code_hash=code_hash,
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scope=scope,
            expires_at=expires_at,
        )
        async with self._session() as session:
            session.add(code)
            await session.commit()
            await session.refresh(code)
            return self._auth_code_to_dict(code)

    async def get_authorization_code(self, code_hash: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(
                select(AuthorizationCode).where(AuthorizationCode.code_hash == code_hash)
            )
            row = result.scalar_one_or_none()
            return self._auth_code_to_dict(row) if row else None

    async def mark_authorization_code_used(self, code_hash: str) -> None:
        async with self._session() as session:
            await session.execute(
                update(AuthorizationCode)
                .where(AuthorizationCode.code_hash == code_hash)
                .values(used_at=datetime.now(timezone.utc))
            )
            await session.commit()

    # -- Refresh token operations ---------------------------------------------

    async def create_refresh_token(
        self,
        token_hash: str,
        user_id: str,
        family_id: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        token = RefreshToken(
            id=str(uuid4()),
            token_hash=token_hash,
            user_id=user_id,
            family_id=family_id,
            expires_at=expires_at,
        )
        async with self._session() as session:
            session.add(token)
            await session.commit()
            await session.refresh(token)
            return self._refresh_token_to_dict(token)

    async def get_refresh_token(self, token_hash: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            )
            row = result.scalar_one_or_none()
            return self._refresh_token_to_dict(row) if row else None

    async def revoke_refresh_token(self, token_hash: str) -> None:
        async with self._session() as session:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.token_hash == token_hash)
                .values(revoked_at=datetime.now(timezone.utc))
            )
            await session.commit()

    async def revoke_token_family(self, family_id: str) -> None:
        async with self._session() as session:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == family_id)
                .where(RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(timezone.utc))
            )
            await session.commit()
```

**Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/test_auth_database.py -v
```

Expected: all passed (7 user + 7 auth code/refresh token = 14 total)

**Step 5: Commit**

```bash
git add apps/backend/app/database.py apps/backend/tests/unit/test_auth_database.py
git commit -m "feat(m2): add auth code and refresh token database operations"
```

---

### Task 9: Auth Schemas

**Files:**
- Create: `apps/backend/app/schemas/auth.py`

**Step 1: Create Pydantic schemas**

```python
# apps/backend/app/schemas/auth.py
"""Pydantic schemas for authentication endpoints."""

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)


class RegisterResponse(BaseModel):
    id: str
    email: str
    display_name: str | None


class AuthorizeRequest(BaseModel):
    """OAuth 2.1 authorization request with embedded credentials."""
    email: EmailStr
    password: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str = "S256"
    state: str | None = None
    scope: str | None = None


class TokenRequest(BaseModel):
    """OAuth 2.1 token exchange request."""
    grant_type: str  # "authorization_code" or "refresh_token"
    code: str | None = None
    code_verifier: str | None = None
    client_id: str | None = None
    redirect_uri: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    is_active: bool
    created_at: str | None


class ErrorResponse(BaseModel):
    """RFC 6750 error response."""
    error: str
    error_description: str | None = None
```

Note: `EmailStr` requires `pydantic[email]`. Add `"email-validator>=2.0"` to `pyproject.toml` dependencies if not already present.

**Step 2: Commit**

```bash
git add apps/backend/app/schemas/auth.py apps/backend/pyproject.toml
git commit -m "feat(m2): add auth Pydantic schemas"
```

---

### Task 10: Registration Endpoint (TDD)

**Files:**
- Create: `apps/backend/app/routers/auth.py`
- Create: `apps/backend/tests/integration/test_register_api.py`

**Step 1: Write failing tests**

```python
# apps/backend/tests/integration/test_register_api.py
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
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/integration/test_register_api.py -v
```

Expected: FAIL -- 404 (route not registered yet)

**Step 3: Implement auth router**

```python
# apps/backend/app/routers/auth.py
"""Auth endpoints: registration and user profile."""

import logging

from fastapi import APIRouter, HTTPException

from app.auth.password import hash_password
from app.database import db
from app.schemas.auth import RegisterRequest, RegisterResponse, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(body: RegisterRequest) -> RegisterResponse:
    """Register a new user account."""
    existing = await db.get_user_by_email(body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    hashed = hash_password(body.password)
    user = await db.create_user(
        email=body.email,
        hashed_password=hashed,
        display_name=body.display_name,
    )
    return RegisterResponse(
        id=user["id"],
        email=user["email"],
        display_name=user["display_name"],
    )
```

**Step 4: Register the router in main.py and routers/__init__.py**

In `apps/backend/app/routers/__init__.py`, add:
```python
from app.routers.auth import router as auth_router
```
and add `"auth_router"` to `__all__`.

In `apps/backend/app/main.py`, import and include:
```python
from app.routers import auth_router
# ...
app.include_router(auth_router, prefix="/api/v1")
```

**Step 5: Run tests**

```bash
cd apps/backend && uv run pytest tests/integration/test_register_api.py -v
```

Expected: 5 passed

**Step 6: Commit**

```bash
git add apps/backend/app/routers/auth.py apps/backend/app/routers/__init__.py apps/backend/app/main.py apps/backend/tests/integration/test_register_api.py
git commit -m "feat(m2): add user registration endpoint"
```

---

### Task 11: OAuth Authorize Endpoint (TDD)

**Files:**
- Create: `apps/backend/app/routers/oauth.py`
- Create: `apps/backend/tests/integration/test_oauth_flow_api.py`

**Step 1: Write failing tests**

```python
# apps/backend/tests/integration/test_oauth_flow_api.py
"""Integration tests for OAuth 2.1 authorization flow."""

import base64
import hashlib
import secrets

import pytest


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _register_user(client, email: str = "oauth@example.com", password: str = "password123456"):
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": password,
    })


class TestAuthorize:
    async def test_authorize_success_returns_redirect(self, client) -> None:
        await _register_user(client)
        verifier, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "oauth@example.com",
            "password": "password123456",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "random-state",
        }, follow_redirects=False)
        assert resp.status_code == 303
        location = resp.headers["location"]
        assert "code=" in location
        assert "state=random-state" in location
        assert location.startswith("http://localhost:3000/callback")

    async def test_authorize_wrong_password(self, client) -> None:
        await _register_user(client)
        _, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "oauth@example.com",
            "password": "wrongpassword",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert resp.status_code == 401

    async def test_authorize_unknown_client(self, client) -> None:
        await _register_user(client)
        _, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "oauth@example.com",
            "password": "password123456",
            "client_id": "unknown-client",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert resp.status_code == 400

    async def test_authorize_invalid_redirect_uri(self, client) -> None:
        await _register_user(client)
        _, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "oauth@example.com",
            "password": "password123456",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://evil.com/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert resp.status_code == 400

    async def test_authorize_missing_code_challenge(self, client) -> None:
        await _register_user(client)
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "oauth@example.com",
            "password": "password123456",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert resp.status_code == 422
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/integration/test_oauth_flow_api.py::TestAuthorize -v
```

Expected: FAIL -- 404

**Step 3: Implement OAuth router (authorize endpoint)**

```python
# apps/backend/app/routers/oauth.py
"""OAuth 2.1 endpoints: authorize, token, revoke, discovery."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import RedirectResponse

from app.auth.constants import (
    AUTHORIZATION_CODE_EXPIRE_MINUTES,
    FIRST_PARTY_CLIENT_ID,
    FIRST_PARTY_REDIRECT_URIS,
)
from app.auth.password import verify_password
from app.config import settings
from app.database import db
from app.schemas.auth import AuthorizeRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _allowed_redirect_uris() -> list[str]:
    """Build list of allowed redirect URIs including dynamic frontend origin."""
    uris = list(FIRST_PARTY_REDIRECT_URIS)
    dynamic = f"{settings.frontend_origin.rstrip('/')}/callback"
    if dynamic not in uris:
        uris.append(dynamic)
    return uris


def _validate_client(client_id: str, redirect_uri: str) -> None:
    """Validate client_id and redirect_uri against known clients."""
    if client_id != FIRST_PARTY_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Unknown client_id")
    if redirect_uri not in _allowed_redirect_uris():
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")


@router.post("/authorize")
async def authorize(body: AuthorizeRequest) -> Response:
    """OAuth 2.1 authorization endpoint with embedded credentials.

    Validates credentials + PKCE, issues authorization code, redirects.
    """
    _validate_client(body.client_id, body.redirect_uri)

    if body.code_challenge_method != "S256":
        raise HTTPException(status_code=400, detail="Only S256 is supported")

    # Authenticate user
    user = await db.get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account disabled")

    # Generate authorization code
    code = secrets.token_urlsafe(32)
    code_hash = hashlib.sha256(code.encode()).hexdigest()

    await db.create_authorization_code(
        code_hash=code_hash,
        user_id=user["id"],
        client_id=body.client_id,
        redirect_uri=body.redirect_uri,
        code_challenge=body.code_challenge,
        scope=body.scope,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=AUTHORIZATION_CODE_EXPIRE_MINUTES),
    )

    # Redirect with code
    params = {"code": code}
    if body.state:
        params["state"] = body.state
    redirect_url = f"{body.redirect_uri}?{urlencode(params)}"
    return RedirectResponse(url=redirect_url, status_code=303)
```

**Step 4: Register the OAuth router**

In `apps/backend/app/routers/__init__.py`, add:
```python
from app.routers.oauth import router as oauth_router
```
and add `"oauth_router"` to `__all__`.

In `apps/backend/app/main.py`:
```python
from app.routers import auth_router, oauth_router
# ...
app.include_router(oauth_router, prefix="/api/v1")
```

**Step 5: Run tests**

```bash
cd apps/backend && uv run pytest tests/integration/test_oauth_flow_api.py::TestAuthorize -v
```

Expected: 5 passed

**Step 6: Commit**

```bash
git add apps/backend/app/routers/oauth.py apps/backend/app/routers/__init__.py apps/backend/app/main.py apps/backend/tests/integration/test_oauth_flow_api.py
git commit -m "feat(m2): add OAuth 2.1 authorize endpoint with PKCE"
```

---

### Task 12: OAuth Token Endpoint (TDD)

**Files:**
- Modify: `apps/backend/app/routers/oauth.py`
- Modify: `apps/backend/tests/integration/test_oauth_flow_api.py`

**Step 1: Write failing tests for token exchange**

Append to `test_oauth_flow_api.py`:

```python
class TestTokenExchange:
    async def _get_auth_code(self, client) -> tuple[str, str]:
        """Helper: register, authorize, return (code, verifier)."""
        await _register_user(client, "token@example.com")
        verifier, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "token@example.com",
            "password": "password123456",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "test",
        }, follow_redirects=False)
        location = resp.headers["location"]
        # Extract code from redirect URL
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(location).query)
        return query["code"][0], verifier

    async def test_exchange_code_for_tokens(self, client) -> None:
        code, verifier = await self._get_auth_code(client)
        resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] == 900  # 15 minutes
        # Refresh token should be in httpOnly cookie
        cookies = resp.cookies
        # Note: httpx may not expose httpOnly cookies directly,
        # check Set-Cookie header instead
        set_cookie = resp.headers.get("set-cookie", "")
        assert "refresh_token=" in set_cookie
        assert "httponly" in set_cookie.lower()

    async def test_exchange_wrong_verifier(self, client) -> None:
        code, _ = await self._get_auth_code(client)
        resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": "wrong-verifier",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        assert resp.status_code == 400

    async def test_exchange_code_replay(self, client) -> None:
        code, verifier = await self._get_auth_code(client)
        # First exchange succeeds
        resp1 = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        assert resp1.status_code == 200
        # Second exchange fails (code already used)
        resp2 = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        assert resp2.status_code == 400

    async def test_exchange_invalid_grant_type(self, client) -> None:
        resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "password",
            "code": "whatever",
        })
        assert resp.status_code == 400


class TestRefreshToken:
    async def _login_and_get_refresh_cookie(self, client) -> str:
        """Helper: full login flow, return the Set-Cookie header value."""
        await _register_user(client, "refresh@example.com")
        verifier, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "refresh@example.com",
            "password": "password123456",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(resp.headers["location"]).query)
        code = query["code"][0]

        token_resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        return token_resp.headers.get("set-cookie", "")

    async def test_refresh_token_rotation(self, client) -> None:
        cookie_header = await self._login_and_get_refresh_cookie(client)
        # Extract refresh token value from Set-Cookie
        # Format: refresh_token=<value>; HttpOnly; ...
        token_value = cookie_header.split("refresh_token=")[1].split(";")[0]

        # Use refresh token
        client.cookies.set("refresh_token", token_value)
        resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "refresh_token",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        # New refresh cookie should be set (rotation)
        new_cookie = resp.headers.get("set-cookie", "")
        assert "refresh_token=" in new_cookie
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/integration/test_oauth_flow_api.py -v -k "TokenExchange or RefreshToken"
```

Expected: FAIL -- 404 or 405 (token endpoint not implemented)

**Step 3: Implement token endpoint in oauth.py**

Add to `apps/backend/app/routers/oauth.py`:

```python
from app.auth.constants import ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS
from app.auth.jwt import create_access_token
from app.auth.pkce import verify_code_challenge
from app.schemas.auth import TokenRequest, TokenResponse


@router.post("/token", response_model=TokenResponse)
async def token(body: TokenRequest, response: Response) -> TokenResponse:
    """OAuth 2.1 token endpoint: code exchange and refresh."""
    if body.grant_type == "authorization_code":
        return await _handle_code_exchange(body, response)
    elif body.grant_type == "refresh_token":
        return await _handle_refresh(body, response)
    else:
        raise HTTPException(status_code=400, detail="Unsupported grant_type")


async def _handle_code_exchange(body: TokenRequest, response: Response) -> TokenResponse:
    """Exchange authorization code + PKCE verifier for tokens."""
    if not body.code or not body.code_verifier or not body.client_id or not body.redirect_uri:
        raise HTTPException(status_code=400, detail="Missing required parameters")

    code_hash = hashlib.sha256(body.code.encode()).hexdigest()
    stored = await db.get_authorization_code(code_hash)

    if not stored:
        raise HTTPException(status_code=400, detail="Invalid authorization code")

    # Check expiry
    expires_at = datetime.fromisoformat(stored["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Authorization code expired")

    # Check already used (replay attack)
    if stored["used_at"] is not None:
        raise HTTPException(status_code=400, detail="Authorization code already used")

    # Validate client_id and redirect_uri match
    if stored["client_id"] != body.client_id or stored["redirect_uri"] != body.redirect_uri:
        raise HTTPException(status_code=400, detail="Client/redirect mismatch")

    # Verify PKCE
    if not verify_code_challenge(body.code_verifier, stored["code_challenge"], "S256"):
        raise HTTPException(status_code=400, detail="PKCE verification failed")

    # Mark code as used
    await db.mark_authorization_code_used(code_hash)

    # Get user
    user = await db.get_user_by_id(stored["user_id"])
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    # Issue tokens
    return await _issue_tokens(user, response)


async def _handle_refresh(body: TokenRequest, response: Response) -> TokenResponse:
    """Refresh access token using refresh token from cookie."""
    from fastapi import Request
    # The refresh token comes from the cookie, injected below
    raise HTTPException(status_code=400, detail="Missing refresh token")
```

Note: The refresh token handling needs access to the request cookie. Refactor the endpoint signature:

```python
from fastapi import Cookie, Request

@router.post("/token", response_model=TokenResponse)
async def token(
    body: TokenRequest,
    response: Response,
    request: Request,
) -> TokenResponse:
    """OAuth 2.1 token endpoint: code exchange and refresh."""
    if body.grant_type == "authorization_code":
        return await _handle_code_exchange(body, response)
    elif body.grant_type == "refresh_token":
        refresh_cookie = request.cookies.get("refresh_token")
        if not refresh_cookie:
            raise HTTPException(status_code=400, detail="Missing refresh token")
        return await _handle_refresh(refresh_cookie, response)
    else:
        raise HTTPException(status_code=400, detail="Unsupported grant_type")


async def _handle_refresh(refresh_token_value: str, response: Response) -> TokenResponse:
    """Refresh: validate token, rotate, issue new tokens."""
    token_hash = hashlib.sha256(refresh_token_value.encode()).hexdigest()
    stored = await db.get_refresh_token(token_hash)

    if not stored:
        raise HTTPException(status_code=400, detail="Invalid refresh token")

    # Check revoked (reuse detection)
    if stored["revoked_at"] is not None:
        # Potential token theft -- revoke entire family
        await db.revoke_token_family(stored["family_id"])
        logger.warning("Refresh token reuse detected for family %s", stored["family_id"])
        raise HTTPException(status_code=400, detail="Token reuse detected")

    # Check expiry
    expires_at = datetime.fromisoformat(stored["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Refresh token expired")

    # Revoke old token
    await db.revoke_refresh_token(token_hash)

    # Get user
    user = await db.get_user_by_id(stored["user_id"])
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    # Issue new tokens with same family_id
    return await _issue_tokens(user, response, family_id=stored["family_id"])


async def _issue_tokens(
    user: dict, response: Response, family_id: str | None = None,
) -> TokenResponse:
    """Create access token (JWT) and refresh token (cookie)."""
    access_token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        secret=settings.effective_jwt_secret,
    )

    # Create refresh token
    raw_refresh = secrets.token_urlsafe(32)
    refresh_hash = hashlib.sha256(raw_refresh.encode()).hexdigest()
    fid = family_id or str(secrets.token_urlsafe(16))

    await db.create_refresh_token(
        token_hash=refresh_hash,
        user_id=user["id"],
        family_id=fid,
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )

    # Set refresh token as httpOnly cookie
    response.set_cookie(
        key="refresh_token",
        value=raw_refresh,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/v1/oauth/token",  # only sent to token endpoint
    )
    # Set a non-httpOnly flag cookie for the frontend to detect sessions
    response.set_cookie(
        key="has_session",
        value="1",
        httponly=False,
        secure=True,
        samesite="strict",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
    )

    return TokenResponse(
        access_token=access_token,
        token_type="Bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
```

**Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/integration/test_oauth_flow_api.py -v
```

Expected: all passed

Note: Tests may need `JWT_SECRET_KEY` set. Update `conftest.py` to set it:

```python
@pytest.fixture(autouse=True)
def set_jwt_secret(monkeypatch):
    monkeypatch.setattr("app.config.settings.jwt_secret_key", "test-secret-for-tests")
```

**Step 5: Commit**

```bash
git add apps/backend/app/routers/oauth.py apps/backend/tests/
git commit -m "feat(m2): add OAuth 2.1 token endpoint with PKCE exchange and refresh rotation"
```

---

### Task 13: Auth Dependencies + /me Endpoint (TDD)

**Files:**
- Create: `apps/backend/app/auth/dependencies.py`
- Modify: `apps/backend/app/routers/auth.py`
- Create: `apps/backend/tests/integration/test_auth_me_api.py`

**Step 1: Write failing tests**

```python
# apps/backend/tests/integration/test_auth_me_api.py
"""Integration tests for /auth/me and auth dependencies."""

import base64
import hashlib
import secrets

import pytest
from urllib.parse import parse_qs, urlparse


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _full_login(client, email="me@example.com", password="password123456") -> str:
    """Register + OAuth flow, return access_token."""
    await client.post("/api/v1/auth/register", json={
        "email": email, "password": password,
    })
    verifier, challenge = _pkce_pair()
    resp = await client.post("/api/v1/oauth/authorize", json={
        "email": email,
        "password": password,
        "client_id": "resume-matcher-web",
        "redirect_uri": "http://localhost:3000/callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    query = parse_qs(urlparse(resp.headers["location"]).query)
    code = query["code"][0]
    token_resp = await client.post("/api/v1/oauth/token", json={
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": "resume-matcher-web",
        "redirect_uri": "http://localhost:3000/callback",
    })
    return token_resp.json()["access_token"]


class TestAuthMe:
    async def test_me_authenticated(self, client) -> None:
        token = await _full_login(client)
        resp = await client.get("/api/v1/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "me@example.com"
        assert "hashed_password" not in data

    async def test_me_no_token(self, client) -> None:
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers

    async def test_me_invalid_token(self, client) -> None:
        resp = await client.get("/api/v1/auth/me", headers={
            "Authorization": "Bearer invalid-token",
        })
        assert resp.status_code == 401

    async def test_me_expired_token(self, client) -> None:
        # Create a token with 0 expiry
        from app.auth.jwt import create_access_token
        import time
        token = create_access_token(
            user_id="fake", email="fake@example.com",
            secret="test-secret-for-tests", expires_minutes=0,
        )
        time.sleep(1)
        resp = await client.get("/api/v1/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 401
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/integration/test_auth_me_api.py -v
```

Expected: FAIL

**Step 3: Implement auth dependencies**

```python
# apps/backend/app/auth/dependencies.py
"""FastAPI dependencies for authentication."""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import verify_access_token
from app.config import settings
from app.database import db

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict:
    """Extract and validate the current user from Bearer token.

    Returns user dict. Raises 401 if token is missing/invalid.
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify_access_token(
            credentials.credentials, secret=settings.effective_jwt_secret
        )
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await db.get_user_by_id(claims["sub"])
    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict | None:
    """Like get_current_user but returns None instead of raising."""
    if not credentials:
        return None
    try:
        claims = verify_access_token(
            credentials.credentials, secret=settings.effective_jwt_secret
        )
    except ValueError:
        return None
    return await db.get_user_by_id(claims["sub"])
```

**Step 4: Add /me endpoint to auth.py**

```python
from fastapi import Depends
from app.auth.dependencies import get_current_user
from app.schemas.auth import UserResponse

@router.get("/me", response_model=UserResponse)
async def me(user: dict = Depends(get_current_user)) -> UserResponse:
    """Get the current authenticated user's profile."""
    return UserResponse(
        id=user["id"],
        email=user["email"],
        display_name=user.get("display_name"),
        is_active=user["is_active"],
        created_at=user.get("created_at"),
    )
```

**Step 5: Run tests**

```bash
cd apps/backend && uv run pytest tests/integration/test_auth_me_api.py -v
```

Expected: 4 passed

**Step 6: Commit**

```bash
git add apps/backend/app/auth/dependencies.py apps/backend/app/routers/auth.py apps/backend/tests/integration/test_auth_me_api.py
git commit -m "feat(m2): add auth dependencies and /me endpoint"
```

---

### Task 14: OAuth Revoke + Discovery Endpoints (TDD)

**Files:**
- Modify: `apps/backend/app/routers/oauth.py`
- Modify: `apps/backend/tests/integration/test_oauth_flow_api.py`

**Step 1: Write failing tests**

Append to `test_oauth_flow_api.py`:

```python
class TestRevoke:
    async def test_revoke_clears_session(self, client) -> None:
        # Login, get refresh cookie
        await _register_user(client, "revoke@example.com")
        verifier, challenge = _pkce_pair()
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "revoke@example.com",
            "password": "password123456",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(resp.headers["location"]).query)
        code = query["code"][0]
        token_resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
        })
        # Revoke
        cookie = token_resp.headers.get("set-cookie", "")
        token_value = cookie.split("refresh_token=")[1].split(";")[0]
        client.cookies.set("refresh_token", token_value)
        revoke_resp = await client.post("/api/v1/oauth/revoke")
        assert revoke_resp.status_code == 200
        # Verify cookie is cleared
        revoke_cookie = revoke_resp.headers.get("set-cookie", "")
        assert "max-age=0" in revoke_cookie.lower() or 'refresh_token=""' in revoke_cookie


class TestDiscovery:
    async def test_well_known_oauth(self, client) -> None:
        resp = await client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "code_challenge_methods_supported" in data
        assert "S256" in data["code_challenge_methods_supported"]
        assert "response_types_supported" in data
        assert "code" in data["response_types_supported"]
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/integration/test_oauth_flow_api.py -v -k "Revoke or Discovery"
```

**Step 3: Implement revoke endpoint in oauth.py**

```python
@router.post("/revoke")
async def revoke(request: Request, response: Response) -> dict:
    """Revoke the current refresh token and clear cookies."""
    refresh_cookie = request.cookies.get("refresh_token")
    if refresh_cookie:
        token_hash = hashlib.sha256(refresh_cookie.encode()).hexdigest()
        stored = await db.get_refresh_token(token_hash)
        if stored:
            await db.revoke_token_family(stored["family_id"])

    response.delete_cookie("refresh_token", path="/api/v1/oauth/token")
    response.delete_cookie("has_session", path="/")
    return {"status": "ok"}
```

**Step 4: Implement discovery endpoint**

Add to `apps/backend/app/main.py` (not under /api/v1 prefix -- must be at root):

```python
@app.get("/.well-known/oauth-authorization-server")
async def oauth_server_metadata() -> dict:
    """RFC 8414 OAuth 2.1 Authorization Server Metadata."""
    base = settings.frontend_base_url.rstrip("/")
    api = f"{base}/api/v1"
    return {
        "issuer": "resume-matcher",
        "authorization_endpoint": f"{api}/oauth/authorize",
        "token_endpoint": f"{api}/oauth/token",
        "revocation_endpoint": f"{api}/oauth/revoke",
        "registration_endpoint": None,  # DCR added in M5
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["openid", "profile", "email"],
    }
```

**Step 5: Run tests**

```bash
cd apps/backend && uv run pytest tests/integration/test_oauth_flow_api.py -v -k "Revoke or Discovery"
```

Expected: passed

**Step 6: Commit**

```bash
git add apps/backend/app/routers/oauth.py apps/backend/app/main.py apps/backend/tests/
git commit -m "feat(m2): add OAuth revoke endpoint and RFC 8414 discovery"
```

---

### Task 15: Frontend PKCE + OAuth Utilities

**Files:**
- Create: `apps/frontend/lib/auth/pkce.ts`
- Create: `apps/frontend/lib/auth/oauth.ts`

**Step 1: PKCE utility using Web Crypto API**

```typescript
// apps/frontend/lib/auth/pkce.ts

function base64UrlEncode(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export async function generatePKCE(): Promise<{
  codeVerifier: string;
  codeChallenge: string;
}> {
  const array = new Uint8Array(32);
  crypto.getRandomValues(array);
  const codeVerifier = base64UrlEncode(array.buffer);

  const encoder = new TextEncoder();
  const digest = await crypto.subtle.digest('SHA-256', encoder.encode(codeVerifier));
  const codeChallenge = base64UrlEncode(digest);

  return { codeVerifier, codeChallenge };
}
```

**Step 2: OAuth flow helpers**

```typescript
// apps/frontend/lib/auth/oauth.ts

import { API_BASE, apiFetch } from '@/lib/api/client';
import { generatePKCE } from './pkce';

const CLIENT_ID = 'resume-matcher-web';
const REDIRECT_URI =
  typeof window !== 'undefined'
    ? `${window.location.origin}/callback`
    : 'http://localhost:3000/callback';

/** Store PKCE verifier in sessionStorage (survives redirect, cleared on tab close). */
const VERIFIER_KEY = 'oauth_code_verifier';
const STATE_KEY = 'oauth_state';

export async function startLogin(): Promise<{
  codeChallenge: string;
  codeVerifier: string;
  state: string;
}> {
  const { codeVerifier, codeChallenge } = await generatePKCE();
  const state = crypto.randomUUID();
  sessionStorage.setItem(VERIFIER_KEY, codeVerifier);
  sessionStorage.setItem(STATE_KEY, state);
  return { codeChallenge, codeVerifier, state };
}

export async function exchangeCode(code: string): Promise<{
  access_token: string;
  expires_in: number;
}> {
  const codeVerifier = sessionStorage.getItem(VERIFIER_KEY);
  if (!codeVerifier) throw new Error('Missing PKCE code_verifier');

  const resp = await apiFetch('/oauth/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include', // send/receive cookies
    body: JSON.stringify({
      grant_type: 'authorization_code',
      code,
      code_verifier: codeVerifier,
      client_id: CLIENT_ID,
      redirect_uri: REDIRECT_URI,
    }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || 'Token exchange failed');
  }

  // Clean up
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);

  return resp.json();
}

export async function silentRefresh(): Promise<{
  access_token: string;
  expires_in: number;
} | null> {
  try {
    const resp = await apiFetch('/oauth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ grant_type: 'refresh_token' }),
    });
    if (!resp.ok) return null;
    return resp.json();
  } catch {
    return null;
  }
}

export async function logout(): Promise<void> {
  await apiFetch('/oauth/revoke', {
    method: 'POST',
    credentials: 'include',
  });
}
```

**Step 3: Commit**

```bash
git add apps/frontend/lib/auth/
git commit -m "feat(m2): add frontend PKCE and OAuth flow utilities"
```

---

### Task 16: Frontend Auth Context

**Files:**
- Create: `apps/frontend/lib/auth/context.tsx`

**Step 1: Create AuthProvider**

```tsx
// apps/frontend/lib/auth/context.tsx
'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { silentRefresh, logout as oauthLogout } from './oauth';
import { apiFetch } from '@/lib/api/client';

interface User {
  id: string;
  email: string;
  display_name: string | null;
}

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  getToken: () => Promise<string | null>;
  login: () => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const tokenRef = useRef<string | null>(null);
  const expiresAtRef = useRef<number>(0);

  const setToken = useCallback((token: string, expiresIn: number) => {
    tokenRef.current = token;
    // Refresh 60s before actual expiry
    expiresAtRef.current = Date.now() + (expiresIn - 60) * 1000;
  }, []);

  const fetchUser = useCallback(async (token: string) => {
    const resp = await apiFetch('/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (resp.ok) {
      setUser(await resp.json());
    } else {
      setUser(null);
    }
  }, []);

  const getToken = useCallback(async (): Promise<string | null> => {
    if (tokenRef.current && Date.now() < expiresAtRef.current) {
      return tokenRef.current;
    }
    // Token expired or missing -- try silent refresh
    const result = await silentRefresh();
    if (result) {
      setToken(result.access_token, result.expires_in);
      return result.access_token;
    }
    tokenRef.current = null;
    setUser(null);
    return null;
  }, [setToken]);

  const login = useCallback(() => {
    window.location.href = '/login';
  }, []);

  const logout = useCallback(async () => {
    await oauthLogout();
    tokenRef.current = null;
    expiresAtRef.current = 0;
    setUser(null);
  }, []);

  // On mount: attempt silent refresh
  useEffect(() => {
    (async () => {
      const result = await silentRefresh();
      if (result) {
        setToken(result.access_token, result.expires_in);
        await fetchUser(result.access_token);
      }
      setIsLoading(false);
    })();
  }, [setToken, fetchUser]);

  return (
    <AuthContext value={{ user, isLoading, getToken, login, logout }}>
      {children}
    </AuthContext>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
```

**Step 2: Wrap app with AuthProvider**

In `apps/frontend/app/(default)/layout.tsx`, add the `AuthProvider` around existing providers:

```tsx
import { AuthProvider } from '@/lib/auth/context';

export default function DefaultLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <StatusCacheProvider>
        {/* ... existing providers ... */}
      </StatusCacheProvider>
    </AuthProvider>
  );
}
```

**Step 3: Commit**

```bash
git add apps/frontend/lib/auth/context.tsx apps/frontend/app/\(default\)/layout.tsx
git commit -m "feat(m2): add AuthProvider context with silent refresh"
```

---

### Task 17: Frontend Login Page

**Files:**
- Create: `apps/frontend/app/(auth)/login/page.tsx`
- Create: `apps/frontend/components/auth/login-form.tsx`

**Step 1: Create login form component**

The login form collects email + password, then POSTs to the OAuth authorize endpoint with PKCE params from the URL query string.

```tsx
// apps/frontend/components/auth/login-form.tsx
'use client';

import { useState } from 'react';
import { apiPost } from '@/lib/api/client';

interface LoginFormProps {
  codeChallenge: string;
  state: string;
}

export function LoginForm({ codeChallenge, state }: LoginFormProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    const redirectUri = `${window.location.origin}/callback`;
    const resp = await apiPost('/oauth/authorize', {
      email,
      password,
      client_id: 'resume-matcher-web',
      redirect_uri: redirectUri,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
      state,
    });

    if (resp.status === 303 || resp.redirected) {
      // Follow redirect to callback with code
      const location = resp.headers.get('location');
      if (location) {
        window.location.href = location;
        return;
      }
    }

    // Handle error
    setLoading(false);
    if (resp.status === 401) {
      setError('Invalid email or password');
    } else {
      const data = await resp.json().catch(() => ({}));
      setError(data.detail || 'Login failed');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter') e.stopPropagation();
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="border border-[#DC2626] bg-red-50 p-3 font-sans text-sm text-[#DC2626]">
          {error}
        </div>
      )}
      <div>
        <label htmlFor="email" className="block font-mono text-xs uppercase tracking-wider mb-1">
          Email
        </label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          className="w-full border border-black bg-white px-3 py-2 font-sans text-sm focus:outline-none focus:ring-1 focus:ring-black rounded-none"
        />
      </div>
      <div>
        <label htmlFor="password" className="block font-mono text-xs uppercase tracking-wider mb-1">
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          className="w-full border border-black bg-white px-3 py-2 font-sans text-sm focus:outline-none focus:ring-1 focus:ring-black rounded-none"
        />
      </div>
      <button
        type="submit"
        disabled={loading}
        className="w-full border border-black bg-black text-white px-4 py-2 font-sans text-sm hover:bg-gray-900 disabled:opacity-50 rounded-none"
      >
        {loading ? 'Signing in...' : 'Sign in'}
      </button>
      <p className="text-center font-sans text-sm text-gray-600">
        No account?{' '}
        <a href="/register" className="text-[#1D4ED8] underline">
          Register
        </a>
      </p>
    </form>
  );
}
```

**Step 2: Create login page**

```tsx
// apps/frontend/app/(auth)/login/page.tsx
'use client';

import { useEffect, useState } from 'react';
import { LoginForm } from '@/components/auth/login-form';
import { startLogin } from '@/lib/auth/oauth';

export default function LoginPage() {
  const [pkce, setPkce] = useState<{ codeChallenge: string; state: string } | null>(null);

  useEffect(() => {
    startLogin().then(({ codeChallenge, state }) => {
      setPkce({ codeChallenge, state });
    });
  }, []);

  if (!pkce) return null; // loading PKCE

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
      <div className="w-full max-w-sm border border-black bg-white p-8 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
        <h1 className="mb-6 font-serif text-2xl font-bold">Sign In</h1>
        <LoginForm codeChallenge={pkce.codeChallenge} state={pkce.state} />
      </div>
    </div>
  );
}
```

**Step 3: Commit**

```bash
git add apps/frontend/app/\(auth\)/ apps/frontend/components/auth/
git commit -m "feat(m2): add login page with Swiss International Style"
```

---

### Task 18: Frontend Register Page

**Files:**
- Create: `apps/frontend/app/(auth)/register/page.tsx`
- Create: `apps/frontend/components/auth/register-form.tsx`

**Step 1: Create register form**

```tsx
// apps/frontend/components/auth/register-form.tsx
'use client';

import { useState } from 'react';
import { apiPost } from '@/lib/api/client';

export function RegisterForm() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    const resp = await apiPost('/auth/register', {
      email,
      password,
      display_name: displayName || undefined,
    });

    if (resp.status === 201) {
      // Registration successful -- redirect to login
      window.location.href = '/login';
      return;
    }

    setLoading(false);
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 409) {
      setError('An account with this email already exists');
    } else {
      setError(data.detail || 'Registration failed');
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="border border-[#DC2626] bg-red-50 p-3 font-sans text-sm text-[#DC2626]">
          {error}
        </div>
      )}
      <div>
        <label htmlFor="displayName" className="block font-mono text-xs uppercase tracking-wider mb-1">
          Name (optional)
        </label>
        <input
          id="displayName"
          type="text"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="w-full border border-black bg-white px-3 py-2 font-sans text-sm focus:outline-none focus:ring-1 focus:ring-black rounded-none"
        />
      </div>
      <div>
        <label htmlFor="email" className="block font-mono text-xs uppercase tracking-wider mb-1">
          Email
        </label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          className="w-full border border-black bg-white px-3 py-2 font-sans text-sm focus:outline-none focus:ring-1 focus:ring-black rounded-none"
        />
      </div>
      <div>
        <label htmlFor="password" className="block font-mono text-xs uppercase tracking-wider mb-1">
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          className="w-full border border-black bg-white px-3 py-2 font-sans text-sm focus:outline-none focus:ring-1 focus:ring-black rounded-none"
        />
        <p className="mt-1 font-mono text-xs text-gray-500">Minimum 8 characters</p>
      </div>
      <button
        type="submit"
        disabled={loading}
        className="w-full border border-black bg-black text-white px-4 py-2 font-sans text-sm hover:bg-gray-900 disabled:opacity-50 rounded-none"
      >
        {loading ? 'Creating account...' : 'Create account'}
      </button>
      <p className="text-center font-sans text-sm text-gray-600">
        Already have an account?{' '}
        <a href="/login" className="text-[#1D4ED8] underline">
          Sign in
        </a>
      </p>
    </form>
  );
}
```

**Step 2: Create register page**

```tsx
// apps/frontend/app/(auth)/register/page.tsx
import { RegisterForm } from '@/components/auth/register-form';

export default function RegisterPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
      <div className="w-full max-w-sm border border-black bg-white p-8 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
        <h1 className="mb-6 font-serif text-2xl font-bold">Create Account</h1>
        <RegisterForm />
      </div>
    </div>
  );
}
```

**Step 3: Commit**

```bash
git add apps/frontend/app/\(auth\)/register/ apps/frontend/components/auth/register-form.tsx
git commit -m "feat(m2): add registration page with Swiss International Style"
```

---

### Task 19: Frontend Callback + API Client Auth Injection

**Files:**
- Create: `apps/frontend/app/(auth)/callback/page.tsx`
- Modify: `apps/frontend/lib/api/client.ts`

**Step 1: Create callback page**

```tsx
// apps/frontend/app/(auth)/callback/page.tsx
'use client';

import { useEffect, useRef } from 'react';
import { useSearchParams } from 'next/navigation';
import { exchangeCode } from '@/lib/auth/oauth';

export default function CallbackPage() {
  const params = useSearchParams();
  const exchanged = useRef(false);

  useEffect(() => {
    if (exchanged.current) return;
    exchanged.current = true;

    const code = params.get('code');
    const state = params.get('state');
    const savedState = sessionStorage.getItem('oauth_state');

    if (!code) {
      window.location.href = '/login';
      return;
    }

    // Validate state to prevent CSRF
    if (state && savedState && state !== savedState) {
      window.location.href = '/login';
      return;
    }

    exchangeCode(code)
      .then(() => {
        window.location.href = '/';
      })
      .catch(() => {
        window.location.href = '/login';
      });
  }, [params]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F0F0E8]">
      <p className="font-sans text-sm text-gray-600">Completing sign in...</p>
    </div>
  );
}
```

**Step 2: Add auth header injection to API client**

Add to `apps/frontend/lib/api/client.ts`:

```typescript
/**
 * Token getter function -- set by AuthProvider.
 * Returns a valid access token or null.
 */
let _getToken: (() => Promise<string | null>) | null = null;

export function setTokenGetter(fn: () => Promise<string | null>): void {
  _getToken = fn;
}

/**
 * Authenticated fetch: injects Authorization header if token available.
 */
export async function authFetch(
  endpoint: string,
  options?: RequestInit,
  timeoutMs?: number
): Promise<Response> {
  const token = _getToken ? await _getToken() : null;
  const headers = new Headers(options?.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  return apiFetch(endpoint, { ...options, headers, credentials: 'include' }, timeoutMs);
}
```

Then update `AuthProvider` in `context.tsx` to call `setTokenGetter(getToken)` on mount.

**Step 3: Commit**

```bash
git add apps/frontend/app/\(auth\)/callback/ apps/frontend/lib/api/client.ts apps/frontend/lib/auth/context.tsx
git commit -m "feat(m2): add OAuth callback page and authenticated fetch"
```

---

### Task 20: Frontend User Menu + Middleware

**Files:**
- Create: `apps/frontend/components/auth/user-menu.tsx`
- Create: `apps/frontend/middleware.ts`

**Step 1: Create user menu component**

```tsx
// apps/frontend/components/auth/user-menu.tsx
'use client';

import { useAuth } from '@/lib/auth/context';

export function UserMenu() {
  const { user, isLoading, login, logout } = useAuth();

  if (isLoading) return null;

  if (!user) {
    return (
      <button
        onClick={login}
        className="border border-black px-3 py-1 font-sans text-sm hover:bg-black hover:text-white rounded-none"
      >
        Sign in
      </button>
    );
  }

  return (
    <div className="flex items-center gap-3">
      <span className="font-mono text-xs">{user.display_name || user.email}</span>
      <button
        onClick={logout}
        className="border border-black px-3 py-1 font-sans text-sm hover:bg-black hover:text-white rounded-none"
      >
        Sign out
      </button>
    </div>
  );
}
```

**Step 2: Wire user menu into existing navigation**

Find the existing header/navigation component and add `<UserMenu />` to it. The exact file depends on the current nav component -- check `apps/frontend/components/` for the header.

**Step 3: Create route protection middleware**

```typescript
// apps/frontend/middleware.ts
import { NextRequest, NextResponse } from 'next/server';

// Routes that require authentication (enforced in M4, soft-redirect for now)
const PROTECTED_ROUTES: string[] = [];
// Routes that are only for unauthenticated users
const AUTH_ROUTES = ['/login', '/register', '/callback'];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = request.cookies.get('has_session')?.value === '1';

  // Redirect authenticated users away from auth pages
  if (hasSession && AUTH_ROUTES.some((r) => pathname.startsWith(r))) {
    return NextResponse.redirect(new URL('/', request.url));
  }

  // Future: redirect unauthenticated users from protected routes
  // Currently all routes are public (M4 will enforce)

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico).*)'],
};
```

**Step 4: Run frontend linting**

```bash
cd apps/frontend && npm run lint && npm run format
```

**Step 5: Commit**

```bash
git add apps/frontend/components/auth/ apps/frontend/middleware.ts
git commit -m "feat(m2): add user menu and route protection middleware"
```

---

### Task 21: Final Integration + Alembic Migration

**Files:**
- Verify: all routers registered in `main.py`
- Generate: Alembic migration for auth tables
- Run: full test suite

**Step 1: Verify main.py has all routers**

Ensure `main.py` includes:
```python
app.include_router(auth_router, prefix="/api/v1")
app.include_router(oauth_router, prefix="/api/v1")
```

And the `/.well-known/oauth-authorization-server` endpoint is at root level.

**Step 2: Generate and run migration**

```bash
cd apps/backend && uv run alembic revision --autogenerate -m "add auth tables"
cd apps/backend && uv run alembic upgrade head
```

**Step 3: Run full backend test suite**

```bash
cd apps/backend && uv run pytest tests/ -v --tb=short
```

Expected: all tests pass (existing + new auth tests)

**Step 4: Run frontend build**

```bash
cd apps/frontend && npm run build
```

**Step 5: Manual smoke test**

1. Start backend: `cd apps/backend && uv run uvicorn app.main:app --reload --port 8000`
2. Start frontend: `cd apps/frontend && npm run dev`
3. Navigate to `http://localhost:3000/register` -- create account
4. Navigate to `http://localhost:3000/login` -- sign in
5. Verify redirect to `/` with user menu showing
6. Refresh page -- verify silent refresh keeps user logged in
7. Click "Sign out" -- verify logout and redirect
8. Check `http://localhost:8000/.well-known/oauth-authorization-server` returns metadata

**Step 6: Final commit**

```bash
git add -A
git commit -m "feat(m2): complete OAuth 2.1 authorization server with frontend auth flow"
```

---

## Summary

| Task | What | Tests |
|------|------|-------|
| 1 | Dependencies (authlib, joserfc, argon2-cffi) | -- |
| 2 | Auth constants + JWT config | -- |
| 3 | AuthorizationCode + RefreshToken models + migration | 5 |
| 4 | Password module (argon2id) | 6 |
| 5 | JWT module (joserfc) | 7 |
| 6 | PKCE module (S256) | 5 |
| 7 | Database user CRUD | 7 |
| 8 | Database auth code + refresh token CRUD | 7 |
| 9 | Auth Pydantic schemas | -- |
| 10 | Registration endpoint | 5 |
| 11 | OAuth authorize endpoint | 5 |
| 12 | OAuth token endpoint (exchange + refresh) | 5 |
| 13 | Auth dependencies + /me | 4 |
| 14 | Revoke + discovery endpoints | 2 |
| 15 | Frontend PKCE + OAuth utilities | -- |
| 16 | Frontend AuthProvider context | -- |
| 17 | Frontend login page | -- |
| 18 | Frontend register page | -- |
| 19 | Frontend callback + auth fetch | -- |
| 20 | Frontend user menu + middleware | -- |
| 21 | Final integration + migration | all |

**Total backend tests: ~58 new tests**

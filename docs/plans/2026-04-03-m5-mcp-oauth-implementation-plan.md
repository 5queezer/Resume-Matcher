# M5: MCP OAuth 2.1 (Claude Integration) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable claude.ai to authenticate against Resume Matcher via OAuth 2.1 and use it as an MCP tool server.

**Architecture:** Migrate JWT signing from HS256 to RS256 (asymmetric keys for public verification). Add JWKS, RFC 9728, and Dynamic Client Registration endpoints. Implement MCP protocol handler (JSON-RPC 2.0 over HTTP) with tool definitions that call existing service functions. Root-level proxy endpoints for claude.ai web compatibility.

**Tech Stack:** joserfc (RSAKey for RS256), FastAPI, SQLAlchemy 2.0 async, Alembic.

---

## Context for Implementer

### What exists (from M2/M3/M4):
- `app/auth/jwt.py` -- HS256 JWT with `create_access_token(user_id, email, secret)` and `verify_access_token(token, secret)`
- `app/auth/dependencies.py` -- `get_current_user` extracts Bearer token, verifies with `settings.effective_jwt_secret`
- `app/auth/constants.py` -- `FIRST_PARTY_CLIENT_ID = "resume-matcher-web"`, redirect URIs, token lifetimes
- `app/auth/pkce.py` -- PKCE S256 verification
- `app/routers/oauth.py` -- `/oauth/authorize`, `/oauth/token`, `/oauth/revoke`; `_validate_client()` checks hardcoded client ID
- `app/routers/google_oauth.py` -- Uses `settings.effective_jwt_secret` for HMAC state packing (NOT JWT; this stays on HMAC)
- `app/main.py:86-102` -- RFC 8414 metadata at `/.well-known/oauth-authorization-server`
- `app/models.py` -- User, Resume, Job, Improvement, AuthorizationCode, RefreshToken, OAuthAccount
- `app/database.py` -- Async SQLAlchemy CRUD for all models
- `app/config.py` -- `jwt_secret_key`, `effective_jwt_secret` property
- `tests/conftest.py` -- `jwt_secret`, `rsa_keys`, `auth_user_a/b`, `auth_headers_a/b`, `client` fixtures
- Latest Alembic revision: `a1b2c3d4e5f6`

### Key principle:
- `jwt_secret_key` / `effective_jwt_secret` continues to be used for HMAC operations (Google OAuth state packing)
- RSA keys are a NEW, separate concern used only for JWT signing/verification
- All existing tests must continue to pass after RS256 migration

---

## Task 1: RSA Key Management Module

**Files:**
- Create: `apps/backend/app/auth/keys.py`
- Modify: `apps/backend/app/config.py` (add RSA settings)
- Create: `apps/backend/tests/unit/test_rsa_keys.py`

### Step 1: Write tests for RSA key management

Create `apps/backend/tests/unit/test_rsa_keys.py`:

```python
"""Tests for RSA key management."""

import json
import tempfile
from pathlib import Path

import pytest

from app.auth.keys import (
    compute_kid,
    get_jwks,
    get_kid,
    get_private_key,
    get_public_key,
    load_rsa_keys,
    reset_keys,
)


@pytest.fixture(autouse=True)
def _clean_keys():
    """Reset key cache before and after each test."""
    reset_keys()
    yield
    reset_keys()


class TestKeyGeneration:
    def test_generate_keys_when_no_source(self):
        """Auto-generates RSA key pair when no PEM or file provided."""
        priv, pub, kid = load_rsa_keys()
        assert priv is not None
        assert pub is not None
        assert isinstance(kid, str)
        assert len(kid) > 0

    def test_generated_key_is_2048_bit(self):
        priv, _, _ = load_rsa_keys()
        # RSA 2048-bit key has modulus of 256 bytes
        d = priv.as_dict(private=True)
        import base64
        n_bytes = base64.urlsafe_b64decode(d["n"] + "==")
        assert len(n_bytes) == 256

    def test_keys_cached_after_first_load(self):
        priv1, pub1, kid1 = load_rsa_keys()
        priv2, pub2, kid2 = load_rsa_keys()
        assert kid1 == kid2

    def test_reset_clears_cache(self):
        load_rsa_keys()
        reset_keys()
        with pytest.raises(RuntimeError, match="not loaded"):
            get_private_key()


class TestKeyLoading:
    def test_load_from_pem_string(self):
        from joserfc.jwk import RSAKey
        key = RSAKey.generate_key(2048)
        pem = key.as_pem(private=True)
        priv, pub, kid = load_rsa_keys(pem_data=pem)
        assert priv.as_dict(private=False)["n"] == key.as_dict(private=False)["n"]

    def test_load_from_file(self, tmp_path):
        from joserfc.jwk import RSAKey
        key = RSAKey.generate_key(2048)
        pem_file = tmp_path / "test_key.pem"
        pem_file.write_text(key.as_pem(private=True))
        priv, pub, kid = load_rsa_keys(key_file=pem_file)
        assert priv.as_dict(private=False)["n"] == key.as_dict(private=False)["n"]

    def test_auto_generate_saves_to_file(self, tmp_path):
        pem_file = tmp_path / "auto_key.pem"
        assert not pem_file.exists()
        load_rsa_keys(key_file=pem_file)
        assert pem_file.exists()
        assert "BEGIN RSA PRIVATE KEY" in pem_file.read_text() or "BEGIN PRIVATE KEY" in pem_file.read_text()

    def test_pem_data_takes_priority_over_file(self, tmp_path):
        from joserfc.jwk import RSAKey
        key1 = RSAKey.generate_key(2048)
        key2 = RSAKey.generate_key(2048)
        pem_file = tmp_path / "key.pem"
        pem_file.write_text(key2.as_pem(private=True))
        priv, _, _ = load_rsa_keys(pem_data=key1.as_pem(private=True), key_file=pem_file)
        assert priv.as_dict(private=False)["n"] == key1.as_dict(private=False)["n"]


class TestJWKS:
    def test_jwks_format(self):
        load_rsa_keys()
        jwks = get_jwks()
        assert "keys" in jwks
        assert len(jwks["keys"]) == 1
        key = jwks["keys"][0]
        assert key["kty"] == "RSA"
        assert key["use"] == "sig"
        assert key["alg"] == "RS256"
        assert "kid" in key
        assert "n" in key
        assert "e" in key
        # Must NOT include private key components
        assert "d" not in key
        assert "p" not in key
        assert "q" not in key

    def test_kid_is_deterministic(self):
        from joserfc.jwk import RSAKey
        key = RSAKey.generate_key(2048)
        pem = key.as_pem(private=True)
        load_rsa_keys(pem_data=pem)
        kid1 = get_kid()
        reset_keys()
        load_rsa_keys(pem_data=pem)
        kid2 = get_kid()
        assert kid1 == kid2


class TestAccessors:
    def test_get_private_key_raises_before_load(self):
        with pytest.raises(RuntimeError):
            get_private_key()

    def test_get_public_key_raises_before_load(self):
        with pytest.raises(RuntimeError):
            get_public_key()

    def test_get_kid_raises_before_load(self):
        with pytest.raises(RuntimeError):
            get_kid()

    def test_accessors_work_after_load(self):
        load_rsa_keys()
        assert get_private_key() is not None
        assert get_public_key() is not None
        assert get_kid() is not None
```

### Step 2: Run tests to verify they fail

```bash
cd apps/backend && uv run pytest tests/unit/test_rsa_keys.py -v
```
Expected: ImportError -- `app.auth.keys` does not exist yet.

### Step 3: Implement RSA key management

Create `apps/backend/app/auth/keys.py`:

```python
"""RSA key management for JWT signing (RS256)."""

import base64
import hashlib
import json
import logging
from pathlib import Path

from joserfc.jwk import RSAKey

logger = logging.getLogger(__name__)

_cached_keys: tuple[RSAKey, RSAKey, str] | None = None


def compute_kid(public_key: RSAKey) -> str:
    """Compute key ID as JWK Thumbprint (RFC 7638) of the public key."""
    d = public_key.as_dict(private=False)
    # Required members for RSA in lexicographic order
    canonical = json.dumps(
        {"e": d["e"], "kty": "RSA", "n": d["n"]},
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def load_rsa_keys(
    pem_data: str | None = None,
    key_file: Path | None = None,
) -> tuple[RSAKey, RSAKey, str]:
    """Load or generate RSA key pair. Call once at app startup.

    Priority: pem_data > key_file > auto-generate (saved to key_file if given).
    """
    global _cached_keys
    if _cached_keys is not None:
        return _cached_keys

    if pem_data:
        private_key = RSAKey.import_key(pem_data)
        logger.info("Loaded RSA key from PEM data")
    elif key_file and key_file.exists():
        private_key = RSAKey.import_key(key_file.read_text())
        logger.info("Loaded RSA key from %s", key_file)
    else:
        private_key = RSAKey.generate_key(2048)
        logger.info("Generated new 2048-bit RSA key pair")
        if key_file:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(private_key.as_pem(private=True))
            logger.info("Saved RSA private key to %s", key_file)

    public_key = RSAKey.import_key(private_key.as_dict(private=False))
    kid = compute_kid(public_key)

    _cached_keys = (private_key, public_key, kid)
    return _cached_keys


def get_private_key() -> RSAKey:
    """Get the cached RSA private key. Raises RuntimeError if not loaded."""
    if _cached_keys is None:
        raise RuntimeError("RSA keys not loaded. Call load_rsa_keys() first.")
    return _cached_keys[0]


def get_public_key() -> RSAKey:
    """Get the cached RSA public key. Raises RuntimeError if not loaded."""
    if _cached_keys is None:
        raise RuntimeError("RSA keys not loaded. Call load_rsa_keys() first.")
    return _cached_keys[1]


def get_kid() -> str:
    """Get the cached key ID. Raises RuntimeError if not loaded."""
    if _cached_keys is None:
        raise RuntimeError("RSA keys not loaded. Call load_rsa_keys() first.")
    return _cached_keys[2]


def get_jwks() -> dict:
    """Return the public key in JWKS format (RFC 7517)."""
    pub = get_public_key()
    key_dict = pub.as_dict(private=False)
    key_dict["kid"] = get_kid()
    key_dict["use"] = "sig"
    key_dict["alg"] = "RS256"
    return {"keys": [key_dict]}


def reset_keys() -> None:
    """Reset cached keys. For testing only."""
    global _cached_keys
    _cached_keys = None
```

### Step 4: Add RSA config settings

Modify `apps/backend/app/config.py`. Add these fields to the `Settings` class (after the Google OAuth section, around line 165):

```python
    # RSA Key Configuration (for RS256 JWT signing)
    rsa_private_key_pem: str = ""
    rsa_key_file: str = ""

    @property
    def effective_rsa_key_file(self) -> Path:
        """Path to RSA private key file."""
        if self.rsa_key_file:
            return Path(self.rsa_key_file)
        return self.data_dir / "jwt_rsa_private.pem"
```

### Step 5: Run tests

```bash
cd apps/backend && uv run pytest tests/unit/test_rsa_keys.py -v
```
Expected: All tests pass.

### Step 6: Commit

```bash
git add apps/backend/app/auth/keys.py apps/backend/app/config.py apps/backend/tests/unit/test_rsa_keys.py
git commit -m "feat(m5): add RSA key management module for RS256 JWT signing"
```

---

## Task 2: RS256 JWT Migration

**Files:**
- Modify: `apps/backend/app/auth/jwt.py`
- Modify: `apps/backend/app/auth/dependencies.py`
- Modify: `apps/backend/app/routers/oauth.py`
- Modify: `apps/backend/app/main.py` (lifespan)
- Modify: `apps/backend/tests/conftest.py`
- Create: `apps/backend/tests/unit/test_jwt_rs256.py`

### Step 1: Write tests for RS256 JWT

Create `apps/backend/tests/unit/test_jwt_rs256.py`:

```python
"""Tests for RS256 JWT token creation and verification."""

import time

import pytest

from app.auth.jwt import create_access_token, verify_access_token
from app.auth.keys import load_rsa_keys, reset_keys


@pytest.fixture(autouse=True)
def _rsa_keys():
    reset_keys()
    load_rsa_keys()
    yield
    reset_keys()


class TestRS256Tokens:
    def test_create_and_verify_roundtrip(self):
        token = create_access_token(user_id="user-123", email="test@example.com")
        claims = verify_access_token(token)
        assert claims["sub"] == "user-123"
        assert claims["email"] == "test@example.com"
        assert claims["iss"] == "resume-matcher"

    def test_token_contains_kid_header(self):
        from joserfc import jwt as jose_jwt
        from app.auth.keys import get_kid, get_public_key

        token = create_access_token(user_id="u1", email="e@x.com")
        # Decode without verification to inspect header
        obj = jose_jwt.decode(token, get_public_key())
        assert obj.header.get("kid") == get_kid()
        assert obj.header.get("alg") == "RS256"

    def test_expired_token_rejected(self):
        token = create_access_token(user_id="u1", email="e@x.com", expires_minutes=-1)
        with pytest.raises(ValueError, match="expired"):
            verify_access_token(token)

    def test_wrong_key_rejected(self):
        from joserfc.jwk import RSAKey
        token = create_access_token(user_id="u1", email="e@x.com")
        # Verify with a different key should fail
        other_key = RSAKey.generate_key(2048)
        reset_keys()
        load_rsa_keys(pem_data=other_key.as_pem(private=True))
        with pytest.raises(ValueError, match="invalid"):
            verify_access_token(token)

    def test_tampered_token_rejected(self):
        token = create_access_token(user_id="u1", email="e@x.com")
        # Tamper with the token
        parts = token.split(".")
        parts[1] = parts[1][:-4] + "XXXX"
        tampered = ".".join(parts)
        with pytest.raises(ValueError):
            verify_access_token(tampered)
```

### Step 2: Run tests to verify they fail

```bash
cd apps/backend && uv run pytest tests/unit/test_jwt_rs256.py -v
```
Expected: FAIL -- `create_access_token` still expects `secret` parameter.

### Step 3: Update jwt.py for RS256

Replace the entire content of `apps/backend/app/auth/jwt.py`:

```python
"""JWT access token operations using joserfc (RS256)."""

import time

from joserfc import jwt
from joserfc.jwk import RSAKey

from app.auth.constants import ACCESS_TOKEN_EXPIRE_MINUTES
from app.auth.keys import get_kid, get_private_key, get_public_key

_ALGORITHM = "RS256"
_ISSUER = "resume-matcher"


def create_access_token(
    user_id: str,
    email: str,
    expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
) -> str:
    """Create an RS256-signed JWT access token."""
    now = int(time.time())
    claims = {
        "sub": user_id,
        "email": email,
        "iss": _ISSUER,
        "iat": now,
        "exp": now + (expires_minutes * 60),
    }
    key = get_private_key()
    return jwt.encode({"alg": _ALGORITHM, "kid": get_kid()}, claims, key)


def verify_access_token(token: str) -> dict:
    """Verify and decode an RS256 JWT access token. Raises ValueError on failure."""
    key = get_public_key()
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

### Step 4: Update dependencies.py

In `apps/backend/app/auth/dependencies.py`, remove the `secret` parameter from `verify_access_token` calls:

Replace line 25-27:
```python
        claims = verify_access_token(
            credentials.credentials, secret=settings.effective_jwt_secret
        )
```
With:
```python
        claims = verify_access_token(credentials.credentials)
```

And replace line 52-54 (in `get_optional_user`):
```python
        claims = verify_access_token(
            credentials.credentials, secret=settings.effective_jwt_secret
        )
```
With:
```python
        claims = verify_access_token(credentials.credentials)
```

Remove the `from app.config import settings` import if it's no longer used (check if anything else uses it in that file -- it should now be unused).

### Step 5: Update oauth.py `_issue_tokens`

In `apps/backend/app/routers/oauth.py`, update `_issue_tokens` (around line 170):

Replace:
```python
    access_token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        secret=settings.effective_jwt_secret,
    )
```
With:
```python
    access_token = create_access_token(
        user_id=user["id"],
        email=user["email"],
    )
```

Remove `settings` from the import line if it's no longer used by any other code in oauth.py. Check first -- `settings.frontend_origin` is used in `_allowed_redirect_uris()`, so keep the import.

### Step 6: Update main.py lifespan to load RSA keys

In `apps/backend/app/main.py`, add the key loading to the lifespan function.

Add import at top (around line 7):
```python
from app.auth.keys import load_rsa_keys
```

In the `lifespan` function (around line 35), add key loading after db init:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await db.init()
    load_rsa_keys(
        pem_data=settings.rsa_private_key_pem or None,
        key_file=settings.effective_rsa_key_file,
    )
    yield
    # ... existing cleanup ...
```

### Step 7: Update test fixtures

In `apps/backend/tests/conftest.py`, add the `rsa_keys` fixture and update dependent fixtures.

Add import at top:
```python
from app.auth.keys import load_rsa_keys, reset_keys
```

Add `rsa_keys` fixture (after the `jwt_secret` fixture):
```python
@pytest.fixture
def rsa_keys():
    """Generate and load test RSA keys for JWT signing."""
    from joserfc.jwk import RSAKey
    reset_keys()
    key = RSAKey.generate_key(2048)
    load_rsa_keys(pem_data=key.as_pem(private=True))
    yield
    reset_keys()
```

Update `auth_user_a` -- change dependency from `jwt_secret` to `rsa_keys`, remove `secret` parameter:
```python
@pytest.fixture
async def auth_user_a(test_db, rsa_keys):
    """Create user A and return (user_dict, bearer_token)."""
    user = await test_db.create_user(email="alice@test.com", hashed_password="hash_a", display_name="Alice")
    token = create_access_token(user_id=user["id"], email=user["email"])
    return user, token
```

Update `auth_user_b` the same way:
```python
@pytest.fixture
async def auth_user_b(test_db, rsa_keys):
    """Create user B and return (user_dict, bearer_token)."""
    user = await test_db.create_user(email="bob@test.com", hashed_password="hash_b", display_name="Bob")
    token = create_access_token(user_id=user["id"], email=user["email"])
    return user, token
```

Update `client` fixture -- add `rsa_keys` dependency:
```python
@pytest.fixture
async def client(test_db, jwt_secret, rsa_keys, monkeypatch):
```
The `jwt_secret` stays because Google OAuth tests need HMAC; `rsa_keys` is needed for JWT operations.

### Step 8: Run ALL tests

```bash
cd apps/backend && uv run pytest -x -v
```
Expected: All ~296 tests pass. If any tests still pass `secret=` to `create_access_token`, fix them.

### Step 9: Commit

```bash
git add -A
git commit -m "feat(m5): migrate JWT signing from HS256 to RS256

Replaces symmetric HS256 signing with asymmetric RS256 key pair.
RSA keys auto-generated on first startup, persisted to data/jwt_rsa_private.pem.
HMAC operations (Google OAuth state packing) continue using jwt_secret_key."
```

---

## Task 3: JWKS + RFC 9728 Metadata Endpoints

**Files:**
- Modify: `apps/backend/app/main.py`
- Create: `apps/backend/tests/integration/test_well_known_endpoints.py`

### Step 1: Write tests

Create `apps/backend/tests/integration/test_well_known_endpoints.py`:

```python
"""Tests for .well-known metadata endpoints."""

import pytest


class TestJWKS:
    @pytest.mark.anyio
    async def test_jwks_returns_public_key(self, client):
        resp = await client.get("/.well-known/jwks.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        assert len(data["keys"]) == 1
        key = data["keys"][0]
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"
        assert key["use"] == "sig"
        assert "kid" in key
        assert "n" in key
        assert "e" in key
        # Must not expose private key
        assert "d" not in key
        assert "p" not in key

    @pytest.mark.anyio
    async def test_jwks_kid_matches_token_kid(self, client, auth_headers_a):
        """JWKS kid should match the kid in issued tokens."""
        import base64, json
        jwks = (await client.get("/.well-known/jwks.json")).json()
        jwks_kid = jwks["keys"][0]["kid"]

        # Get a token by checking its header
        _, token = (await client.get("/api/v1/auth/me", headers=auth_headers_a)), None
        # Actually use the token from auth_headers_a
        token_str = auth_headers_a["Authorization"].split(" ")[1]
        header_b64 = token_str.split(".")[0]
        # Add padding
        header_b64 += "=" * (4 - len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64))
        assert header["kid"] == jwks_kid


class TestProtectedResourceMetadata:
    @pytest.mark.anyio
    async def test_returns_required_fields(self, client):
        resp = await client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.json()
        assert "resource" in data
        assert "authorization_servers" in data
        assert isinstance(data["authorization_servers"], list)
        assert len(data["authorization_servers"]) >= 1
        assert data["bearer_methods_supported"] == ["header"]

    @pytest.mark.anyio
    async def test_resource_matches_base_url(self, client):
        resp = await client.get("/.well-known/oauth-protected-resource")
        data = resp.json()
        assert data["resource"] == "http://test"

    @pytest.mark.anyio
    async def test_authorization_server_is_self(self, client):
        resp = await client.get("/.well-known/oauth-protected-resource")
        data = resp.json()
        assert data["authorization_servers"][0] == "http://test"


class TestOAuthServerMetadata:
    @pytest.mark.anyio
    async def test_includes_registration_endpoint(self, client):
        resp = await client.get("/.well-known/oauth-authorization-server")
        data = resp.json()
        assert data["registration_endpoint"] is not None
        assert "register" in data["registration_endpoint"]

    @pytest.mark.anyio
    async def test_includes_jwks_uri(self, client):
        resp = await client.get("/.well-known/oauth-authorization-server")
        data = resp.json()
        assert data["jwks_uri"] is not None
        assert "jwks" in data["jwks_uri"]
```

### Step 2: Run tests to verify they fail

```bash
cd apps/backend && uv run pytest tests/integration/test_well_known_endpoints.py -v
```
Expected: FAIL -- endpoints don't exist yet.

### Step 3: Add endpoints to main.py

In `apps/backend/app/main.py`, add the import:
```python
from app.auth.keys import get_jwks
```

Add after the existing `/.well-known/oauth-authorization-server` endpoint (after line 102):

```python
@app.get("/.well-known/jwks.json")
async def jwks_endpoint() -> dict:
    """RFC 7517 JSON Web Key Set -- public key for token verification."""
    return get_jwks()


@app.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request: Request) -> dict:
    """RFC 9728 OAuth 2.0 Protected Resource Metadata."""
    base = str(request.base_url).rstrip("/")
    return {
        "resource": base,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "resource_name": "Resume Matcher",
    }
```

Update the existing RFC 8414 metadata to include `registration_endpoint` and `jwks_uri`:
```python
@app.get("/.well-known/oauth-authorization-server")
async def oauth_server_metadata(request: Request) -> dict:
    """RFC 8414 OAuth 2.1 Authorization Server Metadata."""
    base = str(request.base_url).rstrip("/")
    api_base = f"{base}/api/v1"
    return {
        "issuer": base,
        "authorization_endpoint": f"{api_base}/oauth/authorize",
        "token_endpoint": f"{api_base}/oauth/token",
        "revocation_endpoint": f"{api_base}/oauth/revoke",
        "registration_endpoint": f"{api_base}/oauth/register",
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["openid", "profile", "email"],
    }
```

### Step 4: Run tests

```bash
cd apps/backend && uv run pytest tests/integration/test_well_known_endpoints.py -v
```
Expected: All pass.

### Step 5: Commit

```bash
git add -A
git commit -m "feat(m5): add JWKS, RFC 9728, and update RFC 8414 metadata endpoints"
```

---

## Task 4: OAuthClient Model + Alembic Migration

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/<hash>_add_oauth_clients_table.py`
- Modify: `apps/backend/app/database.py`
- Create: `apps/backend/tests/unit/test_oauth_clients_db.py`

### Step 1: Write tests for OAuthClient DB operations

Create `apps/backend/tests/unit/test_oauth_clients_db.py`:

```python
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
        assert client["redirect_uris"] == ["http://localhost:3000/callback"]
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
    async def test_get_nonexistent_client_returns_none(self, test_db):
        result = await test_db.get_oauth_client("nonexistent-id")
        assert result is None

    @pytest.mark.anyio
    async def test_create_client_with_custom_fields(self, test_db):
        client = await test_db.create_oauth_client(
            client_name="Claude",
            redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )
        assert client["grant_types"] == ["authorization_code", "refresh_token"]
        assert client["response_types"] == ["code"]

    @pytest.mark.anyio
    async def test_create_client_with_explicit_id(self, test_db):
        """Used for seeding the first-party client."""
        client = await test_db.create_oauth_client(
            client_id="resume-matcher-web",
            client_name="Resume Matcher Web",
            redirect_uris=["http://localhost:3000/callback"],
        )
        assert client["client_id"] == "resume-matcher-web"
```

### Step 2: Run tests to verify they fail

```bash
cd apps/backend && uv run pytest tests/unit/test_oauth_clients_db.py -v
```
Expected: FAIL -- `create_oauth_client` method does not exist.

### Step 3: Add OAuthClient model

In `apps/backend/app/models.py`, add after the `OAuthAccount` class (before the end of the file):

```python
class OAuthClient(Base):
    """Dynamically registered OAuth clients (RFC 7591)."""
    __tablename__ = "oauth_clients"
    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    client_name: Mapped[str | None] = mapped_column(String(255))
    redirect_uris: Mapped[list] = mapped_column(JSON, default=list)
    grant_types: Mapped[list] = mapped_column(JSON, default=lambda: ["authorization_code"])
    response_types: Mapped[list] = mapped_column(JSON, default=lambda: ["code"])
    token_endpoint_auth_method: Mapped[str] = mapped_column(String(50), default="none")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

Add `OAuthClient` to the import in `apps/backend/app/database.py` (line 13):
```python
from app.models import AuthorizationCode, Base, Improvement, Job, OAuthAccount, OAuthClient, RefreshToken, Resume, User
```

### Step 4: Add DB CRUD methods

In `apps/backend/app/database.py`, add after the OAuth account operations section:

```python
    # -- OAuth client operations ------------------------------------------------

    @staticmethod
    def _oauth_client_to_dict(c: OAuthClient) -> dict[str, Any]:
        return {
            "client_id": c.client_id,
            "client_name": c.client_name,
            "redirect_uris": c.redirect_uris or [],
            "grant_types": c.grant_types or ["authorization_code"],
            "response_types": c.response_types or ["code"],
            "token_endpoint_auth_method": c.token_endpoint_auth_method or "none",
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }

    async def create_oauth_client(
        self,
        client_name: str | None = None,
        redirect_uris: list[str] | None = None,
        grant_types: list[str] | None = None,
        response_types: list[str] | None = None,
        token_endpoint_auth_method: str = "none",
        client_id: str | None = None,
    ) -> dict[str, Any]:
        client = OAuthClient(
            client_id=client_id or str(uuid4()),
            client_name=client_name,
            redirect_uris=redirect_uris or [],
            grant_types=grant_types or ["authorization_code"],
            response_types=response_types or ["code"],
            token_endpoint_auth_method=token_endpoint_auth_method,
        )
        async with self._session() as session:
            session.add(client)
            await session.commit()
            await session.refresh(client)
            return self._oauth_client_to_dict(client)

    async def get_oauth_client(self, client_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(
                select(OAuthClient).where(OAuthClient.client_id == client_id)
            )
            row = result.scalar_one_or_none()
            return self._oauth_client_to_dict(row) if row else None
```

### Step 5: Create Alembic migration

Run:
```bash
cd apps/backend && uv run alembic revision --autogenerate -m "add oauth_clients table"
```

Then edit the generated migration to add first-party client seed. The migration should look like:

```python
"""add oauth_clients table

Revision ID: <auto-generated>
Revises: a1b2c3d4e5f6
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = '<auto-generated>'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'oauth_clients',
        sa.Column('client_id', sa.String(255), primary_key=True),
        sa.Column('client_name', sa.String(255), nullable=True),
        sa.Column('redirect_uris', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('grant_types', sa.JSON(), nullable=False, server_default='["authorization_code"]'),
        sa.Column('response_types', sa.JSON(), nullable=False, server_default='["code"]'),
        sa.Column('token_endpoint_auth_method', sa.String(50), nullable=False, server_default='none'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Seed first-party client
    op.execute(
        sa.text(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, grant_types, response_types, token_endpoint_auth_method, is_active) "
            "VALUES (:cid, :name, :uris, :grants, :types, :method, 1)"
        ).bindparams(
            cid="resume-matcher-web",
            name="Resume Matcher Web",
            uris='["http://localhost:3000/callback", "http://127.0.0.1:3000/callback"]',
            grants='["authorization_code", "refresh_token"]',
            types='["code"]',
            method="none",
        )
    )


def downgrade() -> None:
    op.drop_table('oauth_clients')
```

### Step 6: Run tests

```bash
cd apps/backend && uv run pytest tests/unit/test_oauth_clients_db.py -v
```
Expected: All pass.

### Step 7: Commit

```bash
git add -A
git commit -m "feat(m5): add OAuthClient model, migration, and DB operations for DCR"
```

---

## Task 5: Dynamic Client Registration + DB Client Validation

**Files:**
- Modify: `apps/backend/app/schemas/auth.py`
- Modify: `apps/backend/app/routers/oauth.py`
- Create: `apps/backend/tests/integration/test_dcr.py`

### Step 1: Write tests

Create `apps/backend/tests/integration/test_dcr.py`:

```python
"""Tests for Dynamic Client Registration (RFC 7591)."""

import pytest


class TestDCR:
    @pytest.mark.anyio
    async def test_register_new_client(self, client):
        resp = await client.post("/api/v1/oauth/register", json={
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "client_name": "Claude",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "client_id" in data
        assert data["client_name"] == "Claude"
        assert data["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
        assert data["token_endpoint_auth_method"] == "none"
        assert "client_id_issued_at" in data

    @pytest.mark.anyio
    async def test_register_without_redirect_uris_fails(self, client):
        resp = await client.post("/api/v1/oauth/register", json={
            "client_name": "Bad Client",
        })
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_register_defaults_auth_method_to_none(self, client):
        """claude.ai omits token_endpoint_auth_method -- must default to 'none'."""
        resp = await client.post("/api/v1/oauth/register", json={
            "redirect_uris": ["https://example.com/callback"],
        })
        assert resp.status_code == 201
        assert resp.json()["token_endpoint_auth_method"] == "none"

    @pytest.mark.anyio
    async def test_registered_client_can_authorize(self, client, test_db, rsa_keys, jwt_secret):
        """Full flow: register -> create user -> authorize -> token."""
        # Register client
        reg = await client.post("/api/v1/oauth/register", json={
            "redirect_uris": ["http://localhost:9999/callback"],
            "client_name": "Test MCP Client",
        })
        assert reg.status_code == 201
        client_id = reg.json()["client_id"]

        # Create a user
        await client.post("/api/v1/auth/register", json={
            "email": "dcr@test.com",
            "password": "testpassword123",
            "display_name": "DCR User",
        })

        # Authorize with the DCR client
        from app.auth.pkce import verify_code_challenge
        import hashlib, base64, secrets
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()

        auth_resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "dcr@test.com",
            "password": "testpassword123",
            "client_id": client_id,
            "redirect_uri": "http://localhost:9999/callback",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert auth_resp.status_code == 303

        # Extract code from redirect
        from urllib.parse import urlparse, parse_qs
        redirect_url = auth_resp.headers["location"]
        code = parse_qs(urlparse(redirect_url).query)["code"][0]

        # Exchange for token
        token_resp = await client.post("/api/v1/oauth/token", json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "redirect_uri": "http://localhost:9999/callback",
        })
        assert token_resp.status_code == 200
        assert "access_token" in token_resp.json()


class TestDBClientValidation:
    @pytest.mark.anyio
    async def test_first_party_client_works(self, client, test_db, rsa_keys, jwt_secret):
        """Seeded first-party client should work with authorize."""
        # Seed the first-party client (normally done by migration)
        await test_db.create_oauth_client(
            client_id="resume-matcher-web",
            client_name="Resume Matcher Web",
            redirect_uris=["http://localhost:3000/callback"],
        )
        await client.post("/api/v1/auth/register", json={
            "email": "fp@test.com",
            "password": "testpassword123",
        })

        import hashlib, base64, secrets
        cv = secrets.token_urlsafe(32)
        cc = base64.urlsafe_b64encode(hashlib.sha256(cv.encode()).digest()).rstrip(b"=").decode()

        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "fp@test.com",
            "password": "testpassword123",
            "client_id": "resume-matcher-web",
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": cc,
        }, follow_redirects=False)
        assert resp.status_code == 303

    @pytest.mark.anyio
    async def test_unknown_client_rejected(self, client):
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "x@test.com",
            "password": "pass",
            "client_id": "unknown-client",
            "redirect_uri": "http://evil.com/callback",
            "code_challenge": "abc",
        })
        assert resp.status_code == 400
        assert "Unknown client_id" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_wrong_redirect_uri_rejected(self, client, test_db):
        await test_db.create_oauth_client(
            client_id="test-client",
            redirect_uris=["http://legit.com/callback"],
        )
        resp = await client.post("/api/v1/oauth/authorize", json={
            "email": "x@test.com",
            "password": "pass",
            "client_id": "test-client",
            "redirect_uri": "http://evil.com/callback",
            "code_challenge": "abc",
        })
        assert resp.status_code == 400
        assert "redirect_uri" in resp.json()["detail"].lower()
```

### Step 2: Run tests to verify they fail

```bash
cd apps/backend && uv run pytest tests/integration/test_dcr.py -v
```

### Step 3: Add Pydantic schemas

In `apps/backend/app/schemas/auth.py`, add:

```python
class ClientRegistrationRequest(BaseModel):
    """RFC 7591 Dynamic Client Registration request."""
    redirect_uris: list[str] = Field(min_length=1)
    client_name: str | None = None
    token_endpoint_auth_method: str = "none"
    grant_types: list[str] = Field(default=["authorization_code"])
    response_types: list[str] = Field(default=["code"])


class ClientRegistrationResponse(BaseModel):
    """RFC 7591 Dynamic Client Registration response."""
    client_id: str
    client_name: str | None
    redirect_uris: list[str]
    grant_types: list[str]
    response_types: list[str]
    token_endpoint_auth_method: str
    client_id_issued_at: int
```

### Step 4: Add DCR endpoint and update client validation

In `apps/backend/app/routers/oauth.py`:

Add imports:
```python
from app.schemas.auth import ClientRegistrationRequest, ClientRegistrationResponse
```

Replace `_validate_client` (synchronous) with async DB-based validation:
```python
async def _validate_client(client_id: str, redirect_uri: str) -> None:
    """Validate client_id and redirect_uri against registered clients."""
    oauth_client = await db.get_oauth_client(client_id)
    if not oauth_client or not oauth_client["is_active"]:
        raise HTTPException(status_code=400, detail="Unknown client_id")

    allowed = list(oauth_client["redirect_uris"])
    # First-party client also accepts dynamic frontend origin
    if client_id == FIRST_PARTY_CLIENT_ID:
        dynamic = f"{settings.frontend_origin.rstrip('/')}/callback"
        if dynamic not in allowed:
            allowed.append(dynamic)

    if redirect_uri not in allowed:
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")
```

Update `authorize` endpoint to use `await _validate_client(...)`:
```python
@router.post("/authorize")
async def authorize(body: AuthorizeRequest) -> Response:
    await _validate_client(body.client_id, body.redirect_uri)
    # ... rest unchanged
```

Also update the token exchange to use async validation (in `_handle_code_exchange`, around line 125):
```python
    # Validate client_id matches
    oauth_client = await db.get_oauth_client(body.client_id)
    if not oauth_client or stored["client_id"] != body.client_id or stored["redirect_uri"] != body.redirect_uri:
        raise HTTPException(status_code=400, detail="Client/redirect mismatch")
```

Remove `_allowed_redirect_uris()` function (no longer needed for OAuth authorize -- still needed by `google_oauth.py` though, so check before removing. If google_oauth imports it, keep it or move to a shared location).

Add the DCR endpoint:
```python
@router.post("/register", response_model=ClientRegistrationResponse, status_code=201)
async def register_client(body: ClientRegistrationRequest) -> ClientRegistrationResponse:
    """RFC 7591 Dynamic Client Registration."""
    import time

    client = await db.create_oauth_client(
        client_name=body.client_name,
        redirect_uris=body.redirect_uris,
        grant_types=body.grant_types,
        response_types=body.response_types,
        token_endpoint_auth_method=body.token_endpoint_auth_method,
    )

    return ClientRegistrationResponse(
        client_id=client["client_id"],
        client_name=client["client_name"],
        redirect_uris=client["redirect_uris"],
        grant_types=client["grant_types"],
        response_types=client["response_types"],
        token_endpoint_auth_method=client["token_endpoint_auth_method"],
        client_id_issued_at=int(time.time()),
    )
```

### Step 5: Fix existing tests

Existing OAuth tests that use `client_id="resume-matcher-web"` will need the first-party client seeded in the DB. Update relevant test fixtures or add a helper that seeds it. The simplest approach: add a `seed_first_party_client` fixture:

In `apps/backend/tests/conftest.py`:
```python
@pytest.fixture
async def first_party_client(test_db):
    """Seed the first-party OAuth client (normally done by migration)."""
    await test_db.create_oauth_client(
        client_id="resume-matcher-web",
        client_name="Resume Matcher Web",
        redirect_uris=[
            "http://localhost:3000/callback",
            "http://127.0.0.1:3000/callback",
        ],
        grant_types=["authorization_code", "refresh_token"],
    )
```

Add `first_party_client` as a dependency to tests that use the OAuth authorize/token flow.

### Step 6: Run tests

```bash
cd apps/backend && uv run pytest -x -v
```
Expected: All pass.

### Step 7: Commit

```bash
git add -A
git commit -m "feat(m5): add Dynamic Client Registration (RFC 7591) and DB-based client validation"
```

---

## Task 6: claude.ai Compatibility Endpoints

**Files:**
- Modify: `apps/backend/app/main.py`
- Create: `apps/backend/tests/integration/test_claude_compat.py`

### Step 1: Write tests

Create `apps/backend/tests/integration/test_claude_compat.py`:

```python
"""Tests for claude.ai compatibility proxy endpoints.

claude.ai web ignores AS metadata URLs and appends /authorize, /token,
/register to the server root. These proxy endpoints handle that.
"""

import pytest


class TestClaudeCompat:
    @pytest.mark.anyio
    async def test_root_register_proxies_to_api(self, client):
        """POST /register should work like POST /api/v1/oauth/register."""
        resp = await client.post("/register", json={
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "client_name": "Claude via root",
        })
        assert resp.status_code == 201
        assert "client_id" in resp.json()

    @pytest.mark.anyio
    async def test_root_authorize_redirects(self, client):
        """GET /authorize should redirect to /api/v1/oauth/authorize."""
        resp = await client.get(
            "/authorize",
            params={"response_type": "code", "client_id": "test"},
            follow_redirects=False,
        )
        assert resp.status_code == 307
        assert "/api/v1/oauth/authorize" in resp.headers["location"]

    @pytest.mark.anyio
    async def test_root_token_proxies_to_api(self, client, test_db):
        """POST /token should work like POST /api/v1/oauth/token."""
        resp = await client.post("/token", json={
            "grant_type": "authorization_code",
            "code": "invalid",
            "code_verifier": "test",
            "client_id": "test",
            "redirect_uri": "http://localhost/callback",
        })
        # Should reach the real token endpoint (400 because code is invalid, not 404)
        assert resp.status_code == 400
```

### Step 2: Add proxy endpoints to main.py

In `apps/backend/app/main.py`, add after the well-known endpoints:

```python
# -- claude.ai compatibility proxies ------------------------------------------
# claude.ai web appends /authorize, /token, /register to the server root,
# ignoring the URLs in AS metadata. These thin proxies handle that quirk.

@app.api_route("/register", methods=["POST"])
async def root_register(request: Request):
    """Proxy POST /register -> POST /api/v1/oauth/register."""
    from app.schemas.auth import ClientRegistrationRequest
    body = await request.json()
    from app.routers.oauth import register_client
    return await register_client(ClientRegistrationRequest(**body))


@app.get("/authorize")
async def root_authorize(request: Request):
    """Redirect GET /authorize -> GET /api/v1/oauth/authorize with query params."""
    qs = str(request.query_params)
    target = f"/api/v1/oauth/authorize"
    if qs:
        target = f"{target}?{qs}"
    return RedirectResponse(url=target, status_code=307)


@app.api_route("/token", methods=["POST"])
async def root_token(request: Request):
    """Proxy POST /token -> POST /api/v1/oauth/token."""
    from app.schemas.auth import TokenRequest
    body = await request.json()
    from app.routers.oauth import token as token_endpoint
    return await token_endpoint(TokenRequest(**body), request, Response())
```

Note: The token proxy needs to handle the Response for cookie setting. This may need refinement -- the simplest approach is to forward the request using `httpx` or just call the handler directly. Test and adjust.

### Step 3: Run tests

```bash
cd apps/backend && uv run pytest tests/integration/test_claude_compat.py -v
```

### Step 4: Commit

```bash
git add -A
git commit -m "feat(m5): add root-level proxy endpoints for claude.ai web compatibility"
```

---

## Task 7: MCP Endpoint with Tools

**Files:**
- Create: `apps/backend/app/routers/mcp.py`
- Modify: `apps/backend/app/routers/__init__.py`
- Modify: `apps/backend/app/main.py`
- Create: `apps/backend/tests/integration/test_mcp.py`

### Step 1: Write tests

Create `apps/backend/tests/integration/test_mcp.py`:

```python
"""Tests for MCP endpoint (JSON-RPC 2.0 over Streamable HTTP)."""

import pytest


def _jsonrpc(method: str, params: dict | None = None, msg_id: int = 1) -> dict:
    msg = {"jsonrpc": "2.0", "method": method, "id": msg_id}
    if params:
        msg["params"] = params
    return msg


class TestMCPInitialize:
    @pytest.mark.anyio
    async def test_initialize_without_auth(self, client):
        """initialize must work without Bearer token."""
        resp = await client.post("/mcp", json=_jsonrpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        }))
        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert "result" in data
        result = data["result"]
        assert "serverInfo" in result
        assert "capabilities" in result
        assert result["capabilities"].get("tools") is not None

    @pytest.mark.anyio
    async def test_initialize_returns_session_id(self, client):
        resp = await client.post("/mcp", json=_jsonrpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        }))
        assert "mcp-session-id" in resp.headers


class TestMCPAuth:
    @pytest.mark.anyio
    async def test_tools_list_requires_auth(self, client):
        resp = await client.post("/mcp", json=_jsonrpc("tools/list"))
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32600

    @pytest.mark.anyio
    async def test_tools_list_with_auth(self, client, auth_headers_a):
        resp = await client.post("/mcp", json=_jsonrpc("tools/list"), headers=auth_headers_a)
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        tools = data["result"]["tools"]
        assert len(tools) > 0
        tool_names = [t["name"] for t in tools]
        assert "list_resumes" in tool_names


class TestMCPToolCalls:
    @pytest.mark.anyio
    async def test_list_resumes_empty(self, client, auth_headers_a):
        resp = await client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "list_resumes",
            "arguments": {},
        }), headers=auth_headers_a)
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        # Result content should be a text block with empty list
        content = data["result"]["content"]
        assert isinstance(content, list)

    @pytest.mark.anyio
    async def test_list_resumes_returns_user_data(self, client, auth_headers_a, auth_user_a, test_db, sample_resume):
        """List resumes should return only the authenticated user's resumes."""
        import json
        user_a, _ = auth_user_a
        await test_db.create_resume(content=json.dumps(sample_resume), user_id=user_a["id"], title="My Resume")

        resp = await client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "list_resumes",
            "arguments": {},
        }), headers=auth_headers_a)
        data = resp.json()
        content = data["result"]["content"]
        assert len(content) == 1
        assert "text" in content[0]
        assert "My Resume" in content[0]["text"]

    @pytest.mark.anyio
    async def test_tool_call_isolation(self, client, auth_headers_a, auth_headers_b, auth_user_a, auth_user_b, test_db, sample_resume):
        """User A's data should not be visible to User B via MCP."""
        import json
        user_a, _ = auth_user_a
        await test_db.create_resume(content=json.dumps(sample_resume), user_id=user_a["id"], title="A's Resume")

        resp = await client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "list_resumes",
            "arguments": {},
        }), headers=auth_headers_b)
        data = resp.json()
        content = data["result"]["content"]
        # User B should see empty list
        assert "A's Resume" not in str(content)

    @pytest.mark.anyio
    async def test_unknown_tool_returns_error(self, client, auth_headers_a):
        resp = await client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "nonexistent_tool",
            "arguments": {},
        }), headers=auth_headers_a)
        data = resp.json()
        assert "error" in data
```

### Step 2: Implement MCP router

Create `apps/backend/app/routers/mcp.py`:

```python
"""MCP (Model Context Protocol) endpoint -- JSON-RPC 2.0 over Streamable HTTP."""

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth.jwt import verify_access_token
from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

# Protocol version we support
_PROTOCOL_VERSION = "2025-06-18"
_SERVER_NAME = "resume-matcher"
_SERVER_VERSION = "1.0.0"


# -- Tool definitions ---------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_resumes",
        "description": "List all resumes belonging to the authenticated user.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_resume",
        "description": "Get a specific resume by ID, including its processed content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "resume_id": {"type": "string", "description": "The resume UUID"},
            },
            "required": ["resume_id"],
        },
    },
    {
        "name": "get_status",
        "description": "Get dashboard stats: resume count, job count, improvements count.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "upload_job_description",
        "description": "Upload a job description text. Optionally link to a resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The job description text"},
                "resume_id": {"type": "string", "description": "Optional resume ID to link"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "set_master_resume",
        "description": "Set a resume as the master (source of truth) resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "resume_id": {"type": "string", "description": "The resume UUID to set as master"},
            },
            "required": ["resume_id"],
        },
    },
]


# -- Tool handlers ------------------------------------------------------------

async def _tool_list_resumes(user: dict, _args: dict) -> str:
    resumes = await db.list_resumes(user["id"])
    if not resumes:
        return "No resumes found."
    lines = []
    for r in resumes:
        master = " [MASTER]" if r.get("is_master") else ""
        title = r.get("title") or r.get("filename") or "Untitled"
        lines.append(f"- {title} (id: {r['resume_id']}){master}")
    return "\n".join(lines)


async def _tool_get_resume(user: dict, args: dict) -> str:
    resume_id = args.get("resume_id")
    if not resume_id:
        return "Error: resume_id is required"
    resume = await db.get_resume(resume_id, user["id"])
    if not resume:
        return "Resume not found."
    return json.dumps(resume, indent=2, default=str)


async def _tool_get_status(user: dict, _args: dict) -> str:
    stats = await db.get_stats(user["id"])
    return json.dumps(stats, indent=2)


async def _tool_upload_job(user: dict, args: dict) -> str:
    content = args.get("content")
    if not content:
        return "Error: content is required"
    resume_id = args.get("resume_id")
    if resume_id:
        resume = await db.get_resume(resume_id, user["id"])
        if not resume:
            return "Error: resume not found"
    job = await db.create_job(content=content, user_id=user["id"], resume_id=resume_id)
    return f"Job created: {job['job_id']}"


async def _tool_set_master(user: dict, args: dict) -> str:
    resume_id = args.get("resume_id")
    if not resume_id:
        return "Error: resume_id is required"
    ok = await db.set_master_resume(resume_id, user["id"])
    if ok:
        return f"Resume {resume_id} set as master."
    return "Error: resume not found."


_TOOL_HANDLERS: dict[str, Any] = {
    "list_resumes": _tool_list_resumes,
    "get_resume": _tool_get_resume,
    "get_status": _tool_get_status,
    "upload_job_description": _tool_upload_job,
    "set_master_resume": _tool_set_master,
}


# -- JSON-RPC helpers ---------------------------------------------------------

def _jsonrpc_result(msg_id: int | str | None, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_error(msg_id: int | str | None, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# -- Auth helper --------------------------------------------------------------

async def _resolve_user(request: Request) -> dict | None:
    """Extract Bearer token and resolve user. Returns None if not authenticated."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[7:]
    try:
        claims = verify_access_token(token)
    except ValueError:
        return None
    return await db.get_user_by_id(claims["sub"])


# -- Main handler -------------------------------------------------------------

@router.post("/mcp")
async def mcp_handler(request: Request) -> JSONResponse:
    """MCP Streamable HTTP endpoint (JSON-RPC 2.0)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            content=_jsonrpc_error(None, -32700, "Parse error"),
            status_code=200,
        )

    # Handle single message (not batch for now)
    if isinstance(body, list):
        # Batch not supported yet
        return JSONResponse(
            content=_jsonrpc_error(None, -32600, "Batch requests not supported"),
            status_code=200,
        )

    method = body.get("method")
    msg_id = body.get("id")
    params = body.get("params", {})

    # -- initialize (no auth required) --
    if method == "initialize":
        result = {
            "protocolVersion": _PROTOCOL_VERSION,
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            "capabilities": {
                "tools": {"listChanged": False},
            },
        }
        session_id = str(uuid4())
        return JSONResponse(
            content=_jsonrpc_result(msg_id, result),
            headers={"mcp-session-id": session_id},
        )

    # -- notifications (no response) --
    if method == "notifications/initialized":
        return JSONResponse(content=None, status_code=204)

    # -- Auth required for all other methods --
    user = await _resolve_user(request)
    if not user:
        return JSONResponse(
            content=_jsonrpc_error(msg_id, -32600, "Unauthorized -- Bearer token required"),
            status_code=200,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # -- tools/list --
    if method == "tools/list":
        return JSONResponse(content=_jsonrpc_result(msg_id, {"tools": TOOLS}))

    # -- tools/call --
    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})

        handler = _TOOL_HANDLERS.get(tool_name)
        if not handler:
            return JSONResponse(
                content=_jsonrpc_error(msg_id, -32602, f"Unknown tool: {tool_name}"),
            )

        try:
            text_result = await handler(user, tool_args)
        except Exception as e:
            logger.error("MCP tool %s failed: %s", tool_name, e, exc_info=True)
            return JSONResponse(
                content=_jsonrpc_error(msg_id, -32603, f"Tool execution failed: {e}"),
            )

        return JSONResponse(content=_jsonrpc_result(msg_id, {
            "content": [{"type": "text", "text": text_result}],
        }))

    return JSONResponse(content=_jsonrpc_error(msg_id, -32601, f"Method not found: {method}"))
```

### Step 3: Register the router

In `apps/backend/app/routers/__init__.py`, add:
```python
from app.routers.mcp import router as mcp_router
```
And add `"mcp_router"` to `__all__`.

In `apps/backend/app/main.py`, add import and router:
```python
from app.routers import ..., mcp_router
```
```python
app.include_router(mcp_router)  # No prefix -- mounted at /mcp directly
```

Also add `mcp` module to the `client` fixture's DB patching list in `conftest.py`:
```python
import app.routers.mcp as mcp_mod
# Add mcp_mod to the patching loop
```

### Step 4: Run tests

```bash
cd apps/backend && uv run pytest tests/integration/test_mcp.py -v
```

### Step 5: Run full test suite

```bash
cd apps/backend && uv run pytest -x -v
```

### Step 6: Commit

```bash
git add -A
git commit -m "feat(m5): add MCP endpoint with JSON-RPC handler and tool definitions

Implements MCP Streamable HTTP protocol at /mcp with tools:
list_resumes, get_resume, get_status, upload_job_description, set_master_resume.
Auth required for tools/list and tools/call; initialize works without auth."
```

---

## Post-Implementation Checklist

After all tasks are complete:

1. **Run full test suite**: `cd apps/backend && uv run pytest -v`
2. **Lint**: `cd apps/frontend && npm run lint` (frontend unchanged but verify)
3. **Manual smoke test** (optional):
   - Start backend: `cd apps/backend && uv run uvicorn app.main:app --reload --port 8000`
   - Check `GET http://localhost:8000/.well-known/oauth-protected-resource`
   - Check `GET http://localhost:8000/.well-known/jwks.json`
   - Check `GET http://localhost:8000/.well-known/oauth-authorization-server` (should have `registration_endpoint` and `jwks_uri`)
   - POST to `http://localhost:8000/api/v1/oauth/register` with `{"redirect_uris": ["http://localhost:9999/callback"]}`
   - POST to `http://localhost:8000/mcp` with `{"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}}`
4. **Create PR** against `fork/main`

---

## Files Changed Summary

| Action | File |
|--------|------|
| Create | `apps/backend/app/auth/keys.py` |
| Modify | `apps/backend/app/auth/jwt.py` |
| Modify | `apps/backend/app/auth/dependencies.py` |
| Modify | `apps/backend/app/config.py` |
| Modify | `apps/backend/app/models.py` |
| Modify | `apps/backend/app/database.py` |
| Modify | `apps/backend/app/routers/oauth.py` |
| Modify | `apps/backend/app/main.py` |
| Modify | `apps/backend/app/routers/__init__.py` |
| Modify | `apps/backend/app/schemas/auth.py` |
| Create | `apps/backend/app/routers/mcp.py` |
| Create | `apps/backend/alembic/versions/*_add_oauth_clients_table.py` |
| Modify | `apps/backend/tests/conftest.py` |
| Create | `apps/backend/tests/unit/test_rsa_keys.py` |
| Create | `apps/backend/tests/unit/test_jwt_rs256.py` |
| Create | `apps/backend/tests/unit/test_oauth_clients_db.py` |
| Create | `apps/backend/tests/integration/test_well_known_endpoints.py` |
| Create | `apps/backend/tests/integration/test_dcr.py` |
| Create | `apps/backend/tests/integration/test_claude_compat.py` |
| Create | `apps/backend/tests/integration/test_mcp.py` |

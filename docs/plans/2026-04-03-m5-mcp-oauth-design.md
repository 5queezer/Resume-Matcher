# M5: MCP OAuth 2.1 (Claude Integration) -- Design Document

**Goal:** Make Resume Matcher usable as an MCP server by claude.ai, with RS256 JWT signing, Dynamic Client Registration, JWKS, RFC 9728 metadata, and MCP tool endpoints.

**Architecture:** Full RS256 migration (replaces HS256 for JWT). Simple JSON-RPC handler for MCP protocol (no SDK dependency). OAuth 2.1 infrastructure extended with DCR + JWKS. claude.ai compatibility proxies at root level.

**Tech Stack:** joserfc (RSA key management + JWT), FastAPI, SQLAlchemy, Alembic.

---

## Understanding Summary

- **What:** RS256 JWT migration, JWKS endpoint, RFC 9728 metadata, Dynamic Client Registration (RFC 7591), MCP tool endpoints with OAuth auth, claude.ai compatibility layer
- **Why:** Enable claude.ai (and future MCP clients) to authenticate and use Resume Matcher as an AI tool server
- **Who:** claude.ai as primary MCP client; any MCP-compatible AI assistant
- **Key constraints:** Build on M2 auth (joserfc, SQLAlchemy, FastAPI); handle claude.ai DCR quirk (omits `token_endpoint_auth_method`); single-server deployment
- **Non-goals:** API key fallback, fine-grained OAuth scopes, consent screen, key rotation, rate limiting on DCR, MCP SDK dependency

## Assumptions

1. No production users exist -- breaking JWT format change (HS256 to RS256) is safe
2. Single RS256 key pair is sufficient (no rotation needed yet)
3. claude.ai is the only expected MCP client (but DCR is open for others)
4. HMAC operations (Google OAuth state packing) continue using `jwt_secret_key`
5. MCP protocol implementation is JSON-RPC 2.0 over HTTP (no MCP SDK needed)
6. claude.ai web quirk: ignores AS metadata URLs, appends `/authorize`, `/token`, `/register` to server root

## Decision Log

| # | Decision | Alternatives | Rationale |
|---|----------|-------------|-----------|
| 1 | Full RS256 migration | Dual HS256/RS256 | No production users; one signing path is simpler |
| 2 | Drop API key fallback | Bearer + API key | No existing API key users; YAGNI |
| 3 | No fine-grained scopes | MCP-specific scopes | Same approach as Reactive Resume; add later if needed |
| 4 | No consent screen | Per-client consent | Only expected client is claude.ai; YAGNI |
| 5 | PEM file + env var for RSA key | DB-stored keys, cloud KMS | Single server; file/env is simplest |
| 6 | No MCP SDK dependency | Use `mcp` Python package | MCP is just JSON-RPC; direct handler is simpler, fewer deps |
| 7 | Root-level proxy endpoints for claude.ai | Move all OAuth to root | Proxy keeps existing `/api/v1` structure intact |
| 8 | Seed first-party client into `oauth_clients` table | Keep hardcoded constant | One validation path for all clients |
| 9 | Streamable HTTP (POST) not legacy SSE | SSE transport | Per-request auth; SSE deprecated in MCP spec |
| 10 | `token_endpoint_auth_method` defaults to `none` | Reject missing value | Known claude.ai quirk |
| 11 | Keep `jwt_secret_key` for HMAC operations | Derive from RSA key | Clean separation; Google OAuth state packing unchanged |
| 12 | Module-level key cache in `keys.py` | Settings property, DI container | Simple, testable via `reset_keys()` |

---

## Design

### 1. RSA Key Management (`app/auth/keys.py`)

Module-level cache. Keys loaded once at app startup via `load_rsa_keys()`, accessed via `get_private_key()`, `get_public_key()`, `get_kid()`, `get_jwks()`. `reset_keys()` for test cleanup.

Key loading priority: env var `RSA_PRIVATE_KEY_PEM` > file `data/jwt_rsa_private.pem` > auto-generate.

`kid` = SHA-256 JWK thumbprint of public key (deterministic, stable).

### 2. RS256 JWT Migration

- `jwt.py`: `OctKey` -> `RSAKey`, `HS256` -> `RS256`. Remove `secret` parameter; use module-level keys.
- `dependencies.py`: Remove `settings.effective_jwt_secret` from verify calls.
- `oauth.py`: Remove `settings.effective_jwt_secret` from sign calls.
- `main.py` lifespan: Call `load_rsa_keys()` at startup.
- HMAC operations (Google OAuth `pack_state`/`unpack_state`) continue using `settings.effective_jwt_secret`.

### 3. JWKS Endpoint

`GET /.well-known/jwks.json` in `main.py`. Returns public key via `get_jwks()`.

### 4. RFC 9728 Protected Resource Metadata

`GET /.well-known/oauth-protected-resource` in `main.py`. Returns `resource`, `authorization_servers` (self), `bearer_methods_supported`.

### 5. OAuthClient Model + Migration

New model in `models.py`. Alembic migration creates table and seeds `resume-matcher-web` with existing redirect URIs. CRUD methods in `database.py`.

### 6. Dynamic Client Registration

`POST /api/v1/oauth/register` in `oauth.py`. Accepts `redirect_uris`, `client_name`, `token_endpoint_auth_method` (default `"none"`), `grant_types`, `response_types`. Returns `client_id` (UUID) + echoed metadata.

### 7. DB-Based Client Validation

`_validate_client()` in `oauth.py` queries `oauth_clients` table instead of hardcoded constant. Validates `redirect_uri` against client's registered URIs.

### 8. claude.ai Compatibility Endpoints

Root-level in `main.py`:
- `GET /authorize` -> 302 to `/api/v1/oauth/authorize`
- `POST /token` -> forward to `/api/v1/oauth/token`
- `POST /register` -> forward to `/api/v1/oauth/register`

### 9. MCP Endpoint

`app/routers/mcp.py` -- JSON-RPC 2.0 handler at `/mcp`.

Methods:
- `initialize` -> capabilities (no auth)
- `notifications/initialized` -> 204 (no auth)
- `tools/list` -> tool definitions (auth required)
- `tools/call` -> execute tool (auth required)

Tools: `list_resumes`, `get_resume`, `get_status`, `upload_job_description`, `set_master_resume`

Auth: Extract Bearer token, verify RS256 JWT, resolve user, pass to tool handler.

### 10. Testing

- Unit: RSA key gen/load, JWT RS256 roundtrip, JWKS format, DCR validation
- Integration: Full MCP auth flow, metadata endpoints, claude.ai compat proxies
- Isolation: MCP tools respect user scoping

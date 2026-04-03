# M4: Multi-User Data Isolation — Design Document

**Goal:** Add authentication and user-scoped data isolation to all data endpoints so each user sees only their own resumes, jobs, and tailoring results.

**Architecture:** Bottom-up approach — change database method signatures to require `user_id`, then update routers to inject it via `get_current_user`. The DB layer enforces the invariant; application code cannot skip it.

**Tech Stack:** FastAPI dependencies, SQLAlchemy queries, Alembic migration, existing JWT auth from M2.

---

## Understanding Summary

- **What:** Add authentication and user-scoped data isolation to all data endpoints (resumes, jobs, enrichment, status)
- **Why:** Resume Matcher is transitioning from single-user local app to multi-user server — user data must not leak across accounts
- **Who:** All authenticated users — every user sees only their own resumes, jobs, and tailoring results
- **Key constraints:** No production data to migrate; frontend already has auth from M2; auth infrastructure (`get_current_user`) exists but is unused in data routers
- **Non-goals:** Config endpoints (deferred), admin roles (deferred), row-level security / tenant sharding (unnecessary at current scale)

## Assumptions

1. No production data exists — destructive migration (NOT NULL) is safe
2. Frontend already sends Bearer tokens (M2) — just needs to include them on all API calls
3. `get_current_user` dependency works correctly (tested in M2)
4. All existing integration tests will break and need auth fixtures — this is expected
5. `get_optional_user` will not be used for data endpoints — strict auth only

---

## Decision Log

| # | Decision | Alternatives | Rationale |
|---|----------|-------------|-----------|
| 1 | Config endpoints deferred to later milestone | Admin-only now, remove entirely | Keeps M4 focused on data isolation |
| 2 | Make user_id NOT NULL on Resume, Job, Improvement | Keep nullable, two-phase migration | No prod data; DB enforces invariant |
| 3 | Per-user master resume via partial unique index on `(user_id) WHERE is_master = TRUE` | FK on User model, remove concept entirely | Minimal change, strong DB-level guarantee |
| 4 | Always 404 for cross-user access | 403, mixed 404/403 | Prevents enumeration attacks, simpler single query |
| 5 | `/status` scoped to authenticated user | Remove it, keep public/anonymous | Natural multi-user evolution of dashboard summary |
| 6 | Enrichment endpoints require auth + independent ownership check | Rely on downstream DB scoping | Defense in depth — every entry point validates |
| 7 | Bottom-up approach (DB layer first, then routers) | Top-down (routers first), middleware | DB method signatures enforce invariant; callers cannot skip |

---

## Design

### 1. Alembic Migration

Single migration that:
1. Deletes all rows from `resumes`, `jobs`, `improvements` (no production data)
2. Makes `user_id` NOT NULL on all three tables
3. Adds partial unique index: `CREATE UNIQUE INDEX ix_resumes_user_master ON resumes (user_id) WHERE is_master = TRUE`

### 2. Database Layer Changes

Every CRUD method gains a `user_id: str` parameter. Every query adds `& (Model.user_id == user_id)` to the WHERE clause.

| Method | Current Signature | New Signature |
|--------|-------------------|---------------|
| `create_resume(...)` | No user_id | `create_resume(..., user_id: str)` |
| `create_resume_atomic_master(...)` | No user_id | `create_resume_atomic_master(..., user_id: str)` |
| `get_resume(resume_id)` | By ID only | `get_resume(resume_id, user_id: str)` |
| `get_master_resume()` | Global singleton | `get_master_resume(user_id: str)` |
| `update_resume(resume_id, updates)` | By ID only | `update_resume(resume_id, user_id: str, updates)` |
| `delete_resume(resume_id)` | By ID only | `delete_resume(resume_id, user_id: str)` |
| `list_resumes()` | All resumes | `list_resumes(user_id: str)` |
| `set_master_resume(resume_id)` | Global | `set_master_resume(resume_id, user_id: str)` |
| `create_job(content, resume_id)` | No user_id | `create_job(content, resume_id, user_id: str)` |
| `get_job(job_id)` | By ID only | `get_job(job_id, user_id: str)` |
| `update_job(job_id, updates)` | By ID only | `update_job(job_id, user_id: str, updates)` |
| `create_improvement(...)` | No user_id | `create_improvement(..., user_id: str)` |
| `get_improvement_by_tailored_resume(id)` | By ID only | `get_improvement_by_tailored_resume(id, user_id: str)` |
| `get_stats()` | Global | `get_stats(user_id: str)` |

Methods that find no matching row return `None`; routers raise 404.

### 3. Router Layer Changes

25 endpoints gain `Depends(get_current_user)`:

| Router | Endpoints | Count |
|--------|-----------|-------|
| `resumes.py` | All 17 endpoints | 17 |
| `jobs.py` | Both endpoints | 2 |
| `enrichment.py` | All 5 endpoints | 5 |
| `health.py` | `get_status` only | 1 |
| **Total** | | **25** |

Endpoints that stay unauthenticated:
- `GET /health` — public health check
- `GET /auth/providers` — public feature detection
- `POST /auth/register` — public registration
- `GET /auth/me` — already has auth
- All `/oauth/*` endpoints — handle their own auth flow
- All `/config/*` endpoints — deferred (Decision 1)

Service functions called by routers (improve_resume, generate_cover_letter, etc.) also receive `user_id` and pass it to all DB calls.

### 4. Frontend Changes

Switch data API calls from `apiFetch` to `authFetch` (which injects Bearer token):

| File | Change |
|------|--------|
| `lib/api/resume.ts` | All ~15 calls switch to `authFetch` |
| `lib/api/enrichment.ts` | All ~5 calls switch to `authFetch` |
| `lib/api/config.ts` | Only `/status` call switches; config calls stay unauthenticated (deferred) |

Calls that stay unauthenticated (correctly):
- `oauth.ts` — token exchange, refresh, revoke (cookie-based)
- `context.tsx` — `/auth/me` (manually adds Bearer)
- `login-form.tsx`, `register-form.tsx`, `login/page.tsx` — auth flow endpoints

### 5. Testing Strategy

**Fixture changes:**
- `auth_token` fixture: creates user, returns valid JWT
- `second_user_token` fixture: creates second user for isolation tests
- All integration tests include `Authorization: Bearer {token}` header
- All DB unit tests pass `user_id` to CRUD methods

**New test categories:**
1. **Isolation tests** — User A creates resume, User B gets 404
2. **Ownership tests** — Endpoints return only authenticated user's data
3. **401 tests** — Data endpoints return 401 without Bearer token
4. **Master resume scoping** — User A's master independent of User B's
5. **Migration test** — NOT NULL constraint and partial unique index verified

### 6. Error Handling & Edge Cases

- **Expired token:** Backend returns 401, frontend refreshes via existing M2 mechanism
- **User deleted while session active:** `get_current_user` returns 401
- **Concurrent master set:** Partial unique index prevents two masters; `set_master_resume` unsets existing first
- **Upload without auth:** Returns 401 before file processing
- **Service function chains:** All calls within a service use same `user_id`

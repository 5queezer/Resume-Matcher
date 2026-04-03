# M4: Multi-User Data Isolation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add authentication and user-scoped data isolation to all data endpoints so each user sees only their own resumes, jobs, and tailoring results.

**Architecture:** Bottom-up — modify database method signatures to require `user_id`, then update routers to inject it from `get_current_user`. The DB layer enforces the invariant.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, JWT auth (from M2), Next.js `authFetch`.

**Design doc:** `docs/plans/2026-04-03-m4-multi-user-data-isolation-design.md`

---

### Task 1: Alembic Migration & Model Changes

**Files:**
- Modify: `apps/backend/app/models.py:33,53,68`
- Create: `apps/backend/alembic/versions/xxxx_make_user_id_not_null.py`
- Test: `apps/backend/tests/unit/test_models.py` (existing — verify schema)

**Step 1: Update models — make user_id NOT NULL**

In `apps/backend/app/models.py`, change the three nullable user_id columns:

```python
# Resume model (line 33) — change from:
user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
# to:
user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)

# Job model (line 53) — same change:
user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)

# Improvement model (line 68) — same change:
user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
```

Also update the Resume relationship from:
```python
user: Mapped["User | None"] = relationship(back_populates="resumes")
```
to:
```python
user: Mapped["User"] = relationship(back_populates="resumes")
```

**Step 2: Create Alembic migration**

Run:
```bash
cd apps/backend && uv run alembic revision --autogenerate -m "make_user_id_not_null_add_master_index"
```

Then edit the generated migration to add the destructive cleanup and partial unique index. The migration should look like:

```python
"""make_user_id_not_null_add_master_index

Revision ID: <auto>
Revises: 42511b93573f
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '<auto>'
down_revision: Union[str, None] = '42511b93573f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Delete orphaned data (no production data exists)
    op.execute("DELETE FROM improvements")
    op.execute("DELETE FROM jobs")
    op.execute("DELETE FROM resumes")

    # Make user_id NOT NULL on all three tables
    with op.batch_alter_table("resumes") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=False)
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=False)
    with op.batch_alter_table("improvements") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=False)

    # Per-user master resume: at most one master per user
    op.create_index(
        "ix_resumes_user_master",
        "resumes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_master = true"),
        sqlite_where=sa.text("is_master = 1"),
    )


def downgrade() -> None:
    op.drop_index("ix_resumes_user_master", table_name="resumes")
    with op.batch_alter_table("improvements") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=True)
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=True)
    with op.batch_alter_table("resumes") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=True)
```

**Step 3: Verify model tests still pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_models.py -v`
Expected: PASS (tests create models via ORM, may need user_id added)

**Step 4: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/
git commit -m "feat(m4): make user_id NOT NULL, add per-user master index"
```

---

### Task 2: Database Layer — Resume Operations with user_id

**Files:**
- Modify: `apps/backend/app/database.py:141-242`
- Test: `apps/backend/tests/unit/test_database_user_scoping.py` (create)

**Step 1: Write failing tests for user-scoped resume operations**

Create `apps/backend/tests/unit/test_database_user_scoping.py`:

```python
"""Tests for user-scoped database operations."""

import pytest

from app.database import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite://")
    await database.init()
    yield database
    await database.close()


@pytest.fixture
async def user_a(db):
    return await db.create_user(email="a@test.com", hashed_password="hash_a")


@pytest.fixture
async def user_b(db):
    return await db.create_user(email="b@test.com", hashed_password="hash_b")


# -- Resume scoping --

class TestResumeScoping:
    async def test_create_resume_requires_user_id(self, db, user_a):
        resume = await db.create_resume(content="# Resume", user_id=user_a["id"])
        assert resume["resume_id"]

    async def test_get_resume_scoped_to_user(self, db, user_a, user_b):
        resume = await db.create_resume(content="# A", user_id=user_a["id"])
        # Owner can see it
        assert await db.get_resume(resume["resume_id"], user_id=user_a["id"]) is not None
        # Other user cannot
        assert await db.get_resume(resume["resume_id"], user_id=user_b["id"]) is None

    async def test_list_resumes_scoped_to_user(self, db, user_a, user_b):
        await db.create_resume(content="# A", user_id=user_a["id"])
        await db.create_resume(content="# B", user_id=user_b["id"])
        a_resumes = await db.list_resumes(user_id=user_a["id"])
        assert len(a_resumes) == 1

    async def test_update_resume_scoped_to_user(self, db, user_a, user_b):
        resume = await db.create_resume(content="# A", user_id=user_a["id"])
        # Owner can update
        updated = await db.update_resume(resume["resume_id"], user_id=user_a["id"], updates={"content": "# Updated"})
        assert updated["content"] == "# Updated"
        # Other user gets ValueError (not found)
        with pytest.raises(ValueError):
            await db.update_resume(resume["resume_id"], user_id=user_b["id"], updates={"content": "# Hack"})

    async def test_delete_resume_scoped_to_user(self, db, user_a, user_b):
        resume = await db.create_resume(content="# A", user_id=user_a["id"])
        # Other user cannot delete
        assert await db.delete_resume(resume["resume_id"], user_id=user_b["id"]) is False
        # Owner can
        assert await db.delete_resume(resume["resume_id"], user_id=user_a["id"]) is True

    async def test_get_master_resume_scoped(self, db, user_a, user_b):
        await db.create_resume(content="# A Master", user_id=user_a["id"], is_master=True)
        await db.create_resume(content="# B Master", user_id=user_b["id"], is_master=True)
        a_master = await db.get_master_resume(user_id=user_a["id"])
        assert "A Master" in a_master["content"]
        b_master = await db.get_master_resume(user_id=user_b["id"])
        assert "B Master" in b_master["content"]

    async def test_set_master_resume_scoped(self, db, user_a, user_b):
        r1 = await db.create_resume(content="# A1", user_id=user_a["id"], is_master=True)
        r2 = await db.create_resume(content="# A2", user_id=user_a["id"])
        rb = await db.create_resume(content="# B1", user_id=user_b["id"], is_master=True)
        # Set r2 as master for user A — should not affect user B
        assert await db.set_master_resume(r2["resume_id"], user_id=user_a["id"]) is True
        b_master = await db.get_master_resume(user_id=user_b["id"])
        assert b_master["resume_id"] == rb["resume_id"]
        assert b_master["is_master"] is True

    async def test_create_resume_atomic_master_scoped(self, db, user_a, user_b):
        # User A gets first master
        r_a = await db.create_resume_atomic_master(content="# A", user_id=user_a["id"])
        assert r_a["is_master"] is True
        # User B also gets their own master (independent)
        r_b = await db.create_resume_atomic_master(content="# B", user_id=user_b["id"])
        assert r_b["is_master"] is True
```

**Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_database_user_scoping.py -v`
Expected: FAIL — `create_resume()` doesn't accept `user_id` parameter yet

**Step 3: Implement user-scoped resume operations**

Modify `apps/backend/app/database.py`:

**`create_resume`** — add `user_id: str` parameter:
```python
async def create_resume(
    self,
    content: str,
    user_id: str,
    content_type: str = "md",
    filename: str | None = None,
    is_master: bool = False,
    parent_id: str | None = None,
    processed_data: dict[str, Any] | None = None,
    processing_status: str = "pending",
    cover_letter: str | None = None,
    outreach_message: str | None = None,
    title: str | None = None,
    original_markdown: str | None = None,
) -> dict[str, Any]:
    resume = Resume(
        resume_id=str(uuid4()),
        user_id=user_id,
        content=content,
        content_type=content_type,
        filename=filename,
        is_master=is_master,
        parent_id=parent_id,
        processed_data=processed_data,
        processing_status=processing_status,
        cover_letter=cover_letter,
        outreach_message=outreach_message,
        title=title,
        original_markdown=original_markdown,
    )
    async with self._session() as session:
        session.add(resume)
        await session.commit()
        await session.refresh(resume)
        return self._resume_to_dict(resume)
```

**`create_resume_atomic_master`** — add `user_id: str` parameter, scope to user:
```python
async def create_resume_atomic_master(
    self,
    content: str,
    user_id: str,
    content_type: str = "md",
    filename: str | None = None,
    processed_data: dict[str, Any] | None = None,
    processing_status: str = "pending",
    cover_letter: str | None = None,
    outreach_message: str | None = None,
    original_markdown: str | None = None,
) -> dict[str, Any]:
    async with self._master_resume_lock:
        current_master = await self.get_master_resume(user_id)
        is_master = current_master is None
        if current_master and current_master.get("processing_status") in ("failed", "processing"):
            await self.update_resume(current_master["resume_id"], user_id, {"is_master": False})
            is_master = True
        return await self.create_resume(
            content=content, user_id=user_id, content_type=content_type,
            filename=filename, is_master=is_master, processed_data=processed_data,
            processing_status=processing_status, cover_letter=cover_letter,
            outreach_message=outreach_message, original_markdown=original_markdown,
        )
```

**`get_resume`** — add `user_id: str` parameter:
```python
async def get_resume(self, resume_id: str, user_id: str) -> dict[str, Any] | None:
    async with self._session() as session:
        result = await session.execute(
            select(Resume).where(Resume.resume_id == resume_id, Resume.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        return self._resume_to_dict(row) if row else None
```

**`get_master_resume`** — add `user_id: str` parameter:
```python
async def get_master_resume(self, user_id: str) -> dict[str, Any] | None:
    async with self._session() as session:
        result = await session.execute(
            select(Resume).where(Resume.is_master == True, Resume.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        return self._resume_to_dict(row) if row else None
```

**`update_resume`** — add `user_id: str` parameter:
```python
async def update_resume(self, resume_id: str, user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    updates["updated_at"] = datetime.now(timezone.utc)
    async with self._session() as session:
        result = await session.execute(
            update(Resume).where(Resume.resume_id == resume_id, Resume.user_id == user_id).values(**updates)
        )
        if result.rowcount == 0:
            raise ValueError(f"Resume not found: {resume_id}")
        await session.commit()
    return await self.get_resume(resume_id, user_id)
```

**`delete_resume`** — add `user_id: str` parameter:
```python
async def delete_resume(self, resume_id: str, user_id: str) -> bool:
    async with self._session() as session:
        result = await session.execute(
            delete(Resume).where(Resume.resume_id == resume_id, Resume.user_id == user_id)
        )
        await session.commit()
        return result.rowcount > 0
```

**`list_resumes`** — add `user_id: str` parameter:
```python
async def list_resumes(self, user_id: str) -> list[dict[str, Any]]:
    async with self._session() as session:
        result = await session.execute(select(Resume).where(Resume.user_id == user_id))
        return [self._resume_to_dict(r) for r in result.scalars().all()]
```

**`set_master_resume`** — add `user_id: str` parameter, scope unset to user only:
```python
async def set_master_resume(self, resume_id: str, user_id: str) -> bool:
    async with self._session() as session:
        target = await session.execute(
            select(Resume).where(Resume.resume_id == resume_id, Resume.user_id == user_id)
        )
        if not target.scalar_one_or_none():
            logger.warning("Cannot set master: resume %s not found for user", resume_id)
            return False
        await session.execute(
            update(Resume).where(Resume.is_master == True, Resume.user_id == user_id).values(is_master=False)
        )
        await session.execute(
            update(Resume).where(Resume.resume_id == resume_id).values(is_master=True)
        )
        await session.commit()
        return True
```

**Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_database_user_scoping.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/backend/app/database.py apps/backend/tests/unit/test_database_user_scoping.py
git commit -m "feat(m4): add user_id scoping to resume database operations"
```

---

### Task 3: Database Layer — Job, Improvement & Stats with user_id

**Files:**
- Modify: `apps/backend/app/database.py:246-498`
- Test: `apps/backend/tests/unit/test_database_user_scoping.py` (append)

**Step 1: Write failing tests for job/improvement/stats scoping**

Append to `apps/backend/tests/unit/test_database_user_scoping.py`:

```python
# -- Job scoping --

class TestJobScoping:
    async def test_create_job_with_user_id(self, db, user_a):
        job = await db.create_job(content="Job desc", user_id=user_a["id"])
        assert job["job_id"]

    async def test_get_job_scoped(self, db, user_a, user_b):
        job = await db.create_job(content="Job desc", user_id=user_a["id"])
        assert await db.get_job(job["job_id"], user_id=user_a["id"]) is not None
        assert await db.get_job(job["job_id"], user_id=user_b["id"]) is None

    async def test_update_job_scoped(self, db, user_a, user_b):
        job = await db.create_job(content="Job desc", user_id=user_a["id"])
        updated = await db.update_job(job["job_id"], user_id=user_a["id"], updates={"content": "New"})
        assert updated["content"] == "New"
        assert await db.update_job(job["job_id"], user_id=user_b["id"], updates={"content": "Hack"}) is None


# -- Improvement scoping --

class TestImprovementScoping:
    async def test_create_improvement_with_user_id(self, db, user_a):
        resume = await db.create_resume(content="# R", user_id=user_a["id"])
        tailored = await db.create_resume(content="# T", user_id=user_a["id"])
        job = await db.create_job(content="JD", user_id=user_a["id"])
        imp = await db.create_improvement(
            original_resume_id=resume["resume_id"],
            tailored_resume_id=tailored["resume_id"],
            job_id=job["job_id"],
            improvements=[],
            user_id=user_a["id"],
        )
        assert imp["request_id"]

    async def test_get_improvement_scoped(self, db, user_a, user_b):
        resume = await db.create_resume(content="# R", user_id=user_a["id"])
        tailored = await db.create_resume(content="# T", user_id=user_a["id"])
        job = await db.create_job(content="JD", user_id=user_a["id"])
        await db.create_improvement(
            original_resume_id=resume["resume_id"],
            tailored_resume_id=tailored["resume_id"],
            job_id=job["job_id"],
            improvements=[],
            user_id=user_a["id"],
        )
        assert await db.get_improvement_by_tailored_resume(tailored["resume_id"], user_id=user_a["id"]) is not None
        assert await db.get_improvement_by_tailored_resume(tailored["resume_id"], user_id=user_b["id"]) is None


# -- Stats scoping --

class TestStatsScoping:
    async def test_stats_scoped_to_user(self, db, user_a, user_b):
        await db.create_resume(content="# A", user_id=user_a["id"])
        await db.create_resume(content="# B1", user_id=user_b["id"])
        await db.create_resume(content="# B2", user_id=user_b["id"])
        stats_a = await db.get_stats(user_id=user_a["id"])
        assert stats_a["total_resumes"] == 1
        stats_b = await db.get_stats(user_id=user_b["id"])
        assert stats_b["total_resumes"] == 2
```

**Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_database_user_scoping.py::TestJobScoping -v`
Expected: FAIL

**Step 3: Implement user-scoped job, improvement, and stats operations**

**`create_job`** — add `user_id: str` parameter:
```python
async def create_job(self, content: str, user_id: str, resume_id: str | None = None) -> dict[str, Any]:
    job = Job(job_id=str(uuid4()), content=content, user_id=user_id, resume_id=resume_id)
    async with self._session() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return self._job_to_dict(job)
```

**`get_job`** — add `user_id: str`:
```python
async def get_job(self, job_id: str, user_id: str) -> dict[str, Any] | None:
    async with self._session() as session:
        result = await session.execute(
            select(Job).where(Job.job_id == job_id, Job.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        return self._job_to_dict(row) if row else None
```

**`update_job`** — add `user_id: str`:
```python
async def update_job(self, job_id: str, user_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    async with self._session() as session:
        result = await session.execute(
            update(Job).where(Job.job_id == job_id, Job.user_id == user_id).values(**updates)
        )
        if result.rowcount == 0:
            return None
        await session.commit()
    return await self.get_job(job_id, user_id)
```

**`create_improvement`** — add `user_id: str`:
```python
async def create_improvement(
    self, original_resume_id: str, tailored_resume_id: str,
    job_id: str, improvements: list[dict[str, Any]], user_id: str,
) -> dict[str, Any]:
    imp = Improvement(
        request_id=str(uuid4()), original_resume_id=original_resume_id,
        tailored_resume_id=tailored_resume_id, job_id=job_id,
        improvements=improvements, user_id=user_id,
    )
    async with self._session() as session:
        session.add(imp)
        await session.commit()
        await session.refresh(imp)
        return self._improvement_to_dict(imp)
```

**`get_improvement_by_tailored_resume`** — add `user_id: str`:
```python
async def get_improvement_by_tailored_resume(self, tailored_resume_id: str, user_id: str) -> dict[str, Any] | None:
    async with self._session() as session:
        result = await session.execute(
            select(Improvement).where(
                Improvement.tailored_resume_id == tailored_resume_id,
                Improvement.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        return self._improvement_to_dict(row) if row else None
```

**`get_stats`** — add `user_id: str`:
```python
async def get_stats(self, user_id: str) -> dict[str, Any]:
    async with self._session() as session:
        resume_count = (await session.execute(
            select(func.count()).select_from(Resume).where(Resume.user_id == user_id)
        )).scalar() or 0
        job_count = (await session.execute(
            select(func.count()).select_from(Job).where(Job.user_id == user_id)
        )).scalar() or 0
        improvement_count = (await session.execute(
            select(func.count()).select_from(Improvement).where(Improvement.user_id == user_id)
        )).scalar() or 0
        master = await session.execute(
            select(Resume).where(Resume.is_master == True, Resume.user_id == user_id)
        )
        return {
            "total_resumes": resume_count,
            "total_jobs": job_count,
            "total_improvements": improvement_count,
            "has_master_resume": master.scalar_one_or_none() is not None,
        }
```

**Step 4: Run all scoping tests**

Run: `cd apps/backend && uv run pytest tests/unit/test_database_user_scoping.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/backend/app/database.py apps/backend/tests/unit/test_database_user_scoping.py
git commit -m "feat(m4): add user_id scoping to job, improvement, and stats operations"
```

---

### Task 4: Auth Test Fixtures

**Files:**
- Modify: `apps/backend/tests/conftest.py`

**Step 1: Add auth fixture helpers**

Add these fixtures to `apps/backend/tests/conftest.py`:

```python
from app.auth.jwt import create_access_token
from app.config import settings


@pytest.fixture
async def auth_user_a(test_db):
    """Create user A and return (user_dict, bearer_token)."""
    user = await test_db.create_user(email="alice@test.com", hashed_password="hash_a", display_name="Alice")
    token = create_access_token(user_id=user["id"], secret=settings.effective_jwt_secret)
    return user, token


@pytest.fixture
async def auth_user_b(test_db):
    """Create user B and return (user_dict, bearer_token)."""
    user = await test_db.create_user(email="bob@test.com", hashed_password="hash_b", display_name="Bob")
    token = create_access_token(user_id=user["id"], secret=settings.effective_jwt_secret)
    return user, token


@pytest.fixture
def auth_headers_a(auth_user_a):
    """Authorization headers for user A."""
    _, token = auth_user_a
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_headers_b(auth_user_b):
    """Authorization headers for user B."""
    _, token = auth_user_b
    return {"Authorization": f"Bearer {token}"}
```

Also add the import for `create_access_token`. Check the actual function signature:

```bash
cd apps/backend && grep -n "def create_access_token" app/auth/jwt.py
```

Adapt the fixture if the signature differs.

**Step 2: Verify fixtures work**

Write a quick smoke test in `apps/backend/tests/integration/test_auth_fixtures_smoke.py`:

```python
"""Smoke test that auth fixtures produce valid tokens."""

import pytest


@pytest.mark.asyncio
async def test_auth_me_with_fixture(client, auth_headers_a):
    resp = await client.get("/api/v1/auth/me", headers=auth_headers_a)
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "alice@test.com"
```

Run: `cd apps/backend && uv run pytest tests/integration/test_auth_fixtures_smoke.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add apps/backend/tests/conftest.py apps/backend/tests/integration/test_auth_fixtures_smoke.py
git commit -m "feat(m4): add auth test fixtures for user A/B with JWT tokens"
```

---

### Task 5: Resumes Router — Add Auth & Thread user_id

**Files:**
- Modify: `apps/backend/app/routers/resumes.py`

This is the largest task. All 17 resume endpoints need:
1. `Depends(get_current_user)` in their signature
2. `user["id"]` passed to every `db.*` call

**Step 1: Add import and auth dependency**

At the top of `resumes.py`, add:
```python
from fastapi import Depends
from app.auth.dependencies import get_current_user
```

**Step 2: Update all endpoint signatures and db calls**

The pattern for every endpoint is the same. Here is each endpoint with the specific changes:

**`upload_resume`** (line 509):
```python
async def upload_resume(file: UploadFile = File(...), user: dict = Depends(get_current_user)) -> ResumeUploadResponse:
```
Change `db.create_resume_atomic_master(...)` call to include `user_id=user["id"]`.
Change `db.update_resume(resume["resume_id"], {...})` calls to `db.update_resume(resume["resume_id"], user["id"], {...})`.

**`get_resume`** (line 588):
```python
async def get_resume(resume_id: str = Query(...), user: dict = Depends(get_current_user)) -> ResumeFetchResponse:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.

**`list_resumes`** (line 638):
```python
async def list_resumes(include_master: bool = Query(False), user: dict = Depends(get_current_user)) -> ResumeListResponse:
```
Change `db.list_resumes()` to `db.list_resumes(user["id"])`.

**`improve_resume_preview_endpoint`** (line 664):
```python
async def improve_resume_preview_endpoint(request: ImproveResumeRequest, user: dict = Depends(get_current_user)) -> ImproveResumeResponse:
```
Change `db.get_resume(request.resume_id)` to `db.get_resume(request.resume_id, user["id"])`.
Change `db.get_job(request.job_id)` to `db.get_job(request.job_id, user["id"])`.
Pass `user_id=user["id"]` to `_improve_preview_flow(...)`.

**`_improve_preview_flow`** (line 710) — add `user_id: str` parameter:
```python
async def _improve_preview_flow(
    *,
    request: ImproveResumeRequest,
    resume: dict[str, Any],
    job: dict[str, Any],
    language: str,
    prompt_id: str,
    user_id: str,
) -> ImproveResumeResponse:
```
Change `db.update_job(request.job_id, {...})` to `db.update_job(request.job_id, user_id, {...})` (two occurrences: lines ~726 and ~869).
Change `db.get_master_resume()` to `db.get_master_resume(user_id)` (line ~811).

**`improve_resume_confirm_endpoint`** (line 922):
```python
async def improve_resume_confirm_endpoint(request: ImproveResumeConfirmRequest, user: dict = Depends(get_current_user)) -> ImproveResumeResponse:
```
Change `db.get_resume(request.resume_id)` to `db.get_resume(request.resume_id, user["id"])`.
Change `db.get_job(request.job_id)` to `db.get_job(request.job_id, user["id"])`.
Change `db.create_resume(...)` to include `user_id=user["id"]`.
Change `db.create_improvement(...)` to include `user_id=user["id"]`.

**`improve_resume_endpoint`** (line 1057):
```python
async def improve_resume_endpoint(request: ImproveResumeRequest, user: dict = Depends(get_current_user)) -> ImproveResumeResponse:
```
Change `db.get_resume(request.resume_id)` to `db.get_resume(request.resume_id, user["id"])`.
Change `db.get_job(request.job_id)` to `db.get_job(request.job_id, user["id"])`.
Change `db.get_master_resume()` to `db.get_master_resume(user["id"])`.
Change `db.create_resume(...)` to include `user_id=user["id"]`.
Change `db.create_improvement(...)` to include `user_id=user["id"]`.

**`update_resume_endpoint`** (line 1299):
```python
async def update_resume_endpoint(resume_id: str, resume_data: ResumeData, user: dict = Depends(get_current_user)) -> ResumeFetchResponse:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.
Change `db.update_resume(resume_id, {...})` to `db.update_resume(resume_id, user["id"], {...})`.

**`download_resume_pdf`** (line 1348):
```python
async def download_resume_pdf(resume_id: str, ..., user: dict = Depends(get_current_user)) -> Response:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.

**`delete_resume`** (line 1431):
```python
async def delete_resume(resume_id: str, user: dict = Depends(get_current_user)) -> dict:
```
Change `db.delete_resume(resume_id)` to `db.delete_resume(resume_id, user["id"])`.

**`retry_processing`** (line 1440):
```python
async def retry_processing(resume_id: str, user: dict = Depends(get_current_user)) -> ResumeUploadResponse:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.
Change both `db.update_resume(resume_id, {...})` calls to include `user["id"]`.

**`update_cover_letter`** (line 1492):
```python
async def update_cover_letter(resume_id: str, request: UpdateCoverLetterRequest, user: dict = Depends(get_current_user)) -> dict:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.
Change `db.update_resume(resume_id, {...})` to `db.update_resume(resume_id, user["id"], {...})`.

**`update_outreach_message`** (line 1505):
```python
async def update_outreach_message(resume_id: str, request: UpdateOutreachMessageRequest, user: dict = Depends(get_current_user)) -> dict:
```
Same pattern as `update_cover_letter`.

**`update_title`** (line 1518):
```python
async def update_title(resume_id: str, request: UpdateTitleRequest, user: dict = Depends(get_current_user)) -> dict:
```
Same pattern.

**`generate_cover_letter_endpoint`** (line 1530):
```python
async def generate_cover_letter_endpoint(resume_id: str, user: dict = Depends(get_current_user)) -> GenerateContentResponse:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.
Change `db.get_improvement_by_tailored_resume(resume_id)` to `db.get_improvement_by_tailored_resume(resume_id, user["id"])`.
Change `db.get_job(improvement["job_id"])` to `db.get_job(improvement["job_id"], user["id"])`.
Change `db.update_resume(resume_id, {...})` to `db.update_resume(resume_id, user["id"], {...})`.

**`generate_outreach_endpoint`** (line 1603):
Same pattern as `generate_cover_letter_endpoint`.

**`get_job_description_for_resume`** (line 1674):
```python
async def get_job_description_for_resume(resume_id: str, user: dict = Depends(get_current_user)) -> dict:
```
Change all three db calls to include `user["id"]`.

**`download_cover_letter_pdf`** (line 1716):
```python
async def download_cover_letter_pdf(resume_id: str, ..., user: dict = Depends(get_current_user)) -> Response:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.

**Step 3: Commit**

```bash
git add apps/backend/app/routers/resumes.py
git commit -m "feat(m4): add auth to all resume endpoints, thread user_id to db calls"
```

---

### Task 6: Jobs Router — Add Auth

**Files:**
- Modify: `apps/backend/app/routers/jobs.py`

**Step 1: Add auth dependency and thread user_id**

```python
from fastapi import APIRouter, Depends, HTTPException
from app.auth.dependencies import get_current_user
from app.database import db
from app.schemas import JobUploadRequest, JobUploadResponse

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post("/upload", response_model=JobUploadResponse)
async def upload_job_descriptions(
    request: JobUploadRequest, user: dict = Depends(get_current_user)
) -> JobUploadResponse:
    if not request.job_descriptions:
        raise HTTPException(status_code=400, detail="No job descriptions provided")

    job_ids = []
    for jd in request.job_descriptions:
        if not jd.strip():
            raise HTTPException(status_code=400, detail="Empty job description")
        job = await db.create_job(
            content=jd.strip(),
            user_id=user["id"],
            resume_id=request.resume_id,
        )
        job_ids.append(job["job_id"])

    return JobUploadResponse(
        message="data successfully processed",
        job_id=job_ids,
        request={
            "job_descriptions": request.job_descriptions,
            "resume_id": request.resume_id,
        },
    )


@router.get("/{job_id}")
async def get_job(job_id: str, user: dict = Depends(get_current_user)) -> dict:
    job = await db.get_job(job_id, user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
```

**Step 2: Commit**

```bash
git add apps/backend/app/routers/jobs.py
git commit -m "feat(m4): add auth to jobs endpoints, thread user_id"
```

---

### Task 7: Enrichment Router — Add Auth

**Files:**
- Modify: `apps/backend/app/routers/enrichment.py`

**Step 1: Add auth and thread user_id to all 5 endpoints**

Add imports:
```python
from fastapi import APIRouter, Depends, HTTPException
from app.auth.dependencies import get_current_user
```

Update each endpoint:

**`analyze_resume`** (line 87):
```python
async def analyze_resume(resume_id: str, user: dict = Depends(get_current_user)) -> AnalysisResponse:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.

**`generate_enhancements`** (line 157):
```python
async def generate_enhancements(request: EnhanceRequest, user: dict = Depends(get_current_user)) -> EnhancementPreview:
```
Change `db.get_resume(request.resume_id)` to `db.get_resume(request.resume_id, user["id"])`.

**`apply_enhancements`** (line 298):
```python
async def apply_enhancements(resume_id: str, request: ApplyEnhancementsRequest, user: dict = Depends(get_current_user)) -> dict:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.
Change `db.update_resume(resume_id, {...})` to `db.update_resume(resume_id, user["id"], {...})`.

**`regenerate_items`** (line 457):
```python
async def regenerate_items(request: RegenerateRequest, user: dict = Depends(get_current_user)) -> RegenerateResponse:
```
Change `db.get_resume(request.resume_id)` to `db.get_resume(request.resume_id, user["id"])`.

**`apply_regenerated_items`** (line 517):
```python
async def apply_regenerated_items(resume_id: str, regenerated_items: list[RegeneratedItem], user: dict = Depends(get_current_user)) -> dict:
```
Change `db.get_resume(resume_id)` to `db.get_resume(resume_id, user["id"])`.
Change `db.update_resume(resume_id, {...})` to `db.update_resume(resume_id, user["id"], {...})`.

**Step 2: Commit**

```bash
git add apps/backend/app/routers/enrichment.py
git commit -m "feat(m4): add auth to enrichment endpoints, thread user_id"
```

---

### Task 8: Health Status — Add Auth

**Files:**
- Modify: `apps/backend/app/routers/health.py`

**Step 1: Add auth to get_status only**

```python
from fastapi import APIRouter, Depends
from app.auth.dependencies import get_current_user
from app.database import db
from app.llm import check_llm_health, get_llm_config
from app.schemas import HealthResponse, StatusResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Basic health check endpoint — stays public."""
    llm_status = await check_llm_health()
    return HealthResponse(
        status="healthy" if llm_status["healthy"] else "degraded",
        llm=llm_status,
    )


@router.get("/status", response_model=StatusResponse)
async def get_status(user: dict = Depends(get_current_user)) -> StatusResponse:
    """Get user-scoped application status."""
    config = get_llm_config()
    llm_status = await check_llm_health(config)
    db_stats = await db.get_stats(user["id"])

    return StatusResponse(
        status="ready" if llm_status["healthy"] and db_stats["has_master_resume"] else "setup_required",
        llm_configured=bool(config.api_key) or config.provider == "ollama",
        llm_healthy=llm_status["healthy"],
        has_master_resume=db_stats["has_master_resume"],
        database_stats=db_stats,
    )
```

**Step 2: Commit**

```bash
git add apps/backend/app/routers/health.py
git commit -m "feat(m4): add auth to /status endpoint, scope stats to user"
```

---

### Task 9: Integration Tests — Auth Enforcement & Isolation

**Files:**
- Create: `apps/backend/tests/integration/test_data_isolation.py`

**Step 1: Write auth enforcement and isolation tests**

```python
"""Integration tests for multi-user data isolation."""

import json
import pytest


# -- 401 for unauthenticated requests --

class TestAuthEnforcement:
    """All data endpoints must return 401 without a Bearer token."""

    PROTECTED_ENDPOINTS = [
        ("GET", "/api/v1/resumes?resume_id=fake"),
        ("GET", "/api/v1/resumes/list"),
        ("GET", "/api/v1/resumes/fake/pdf"),
        ("DELETE", "/api/v1/resumes/fake"),
        ("GET", "/api/v1/resumes/fake/job-description"),
        ("GET", "/api/v1/jobs/fake"),
        ("GET", "/api/v1/status"),
    ]

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    async def test_returns_401_without_token(self, client, method, path):
        resp = await client.request(method, path)
        assert resp.status_code == 401


class TestResumeIsolation:
    """User A's resumes are invisible to User B."""

    async def test_user_b_cannot_see_user_a_resume(self, client, test_db, auth_user_a, auth_user_b):
        user_a, token_a = auth_user_a
        _, token_b = auth_user_b
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # User A creates a resume via DB directly
        resume = await test_db.create_resume(content="# Secret", user_id=user_a["id"])
        rid = resume["resume_id"]

        # User A can see it
        resp = await client.get(f"/api/v1/resumes?resume_id={rid}", headers=headers_a)
        assert resp.status_code == 200

        # User B gets 404
        resp = await client.get(f"/api/v1/resumes?resume_id={rid}", headers=headers_b)
        assert resp.status_code == 404

    async def test_list_resumes_only_shows_own(self, client, test_db, auth_user_a, auth_user_b):
        user_a, token_a = auth_user_a
        user_b, token_b = auth_user_b

        await test_db.create_resume(content="# A1", user_id=user_a["id"])
        await test_db.create_resume(content="# A2", user_id=user_a["id"])
        await test_db.create_resume(content="# B1", user_id=user_b["id"])

        resp = await client.get(
            "/api/v1/resumes/list?include_master=true",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

    async def test_user_b_cannot_delete_user_a_resume(self, client, test_db, auth_user_a, auth_user_b):
        user_a, _ = auth_user_a
        _, token_b = auth_user_b

        resume = await test_db.create_resume(content="# A", user_id=user_a["id"])

        resp = await client.delete(
            f"/api/v1/resumes/{resume['resume_id']}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404

        # Resume still exists for user A
        still_exists = await test_db.get_resume(resume["resume_id"], user_a["id"])
        assert still_exists is not None


class TestJobIsolation:
    async def test_user_b_cannot_see_user_a_job(self, client, test_db, auth_user_a, auth_user_b):
        user_a, token_a = auth_user_a
        _, token_b = auth_user_b

        job = await test_db.create_job(content="JD text", user_id=user_a["id"])

        resp = await client.get(
            f"/api/v1/jobs/{job['job_id']}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 200

        resp = await client.get(
            f"/api/v1/jobs/{job['job_id']}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404


class TestStatusScoping:
    async def test_status_returns_user_stats(self, client, test_db, auth_user_a, auth_user_b):
        user_a, token_a = auth_user_a
        user_b, token_b = auth_user_b

        await test_db.create_resume(content="# A", user_id=user_a["id"], is_master=True)

        resp = await client.get("/api/v1/status", headers={"Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200
        assert resp.json()["database_stats"]["total_resumes"] == 1
        assert resp.json()["has_master_resume"] is True

        resp = await client.get("/api/v1/status", headers={"Authorization": f"Bearer {token_b}"})
        assert resp.status_code == 200
        assert resp.json()["database_stats"]["total_resumes"] == 0
        assert resp.json()["has_master_resume"] is False
```

**Step 2: Run isolation tests**

Run: `cd apps/backend && uv run pytest tests/integration/test_data_isolation.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add apps/backend/tests/integration/test_data_isolation.py
git commit -m "test(m4): add auth enforcement and cross-user isolation tests"
```

---

### Task 10: Frontend — Switch to authFetch

**Files:**
- Modify: `apps/frontend/lib/api/resume.ts`
- Modify: `apps/frontend/lib/api/enrichment.ts`
- Modify: `apps/frontend/lib/api/config.ts` (only `/status` call)

**Step 1: Update resume.ts**

Replace the import:
```typescript
// Before:
import { API_BASE, apiPost, apiPatch, apiDelete, apiFetch } from './client';
// After:
import { API_BASE, authFetch } from './client';
```

Replace every `apiFetch(...)`, `apiPost(...)`, `apiPatch(...)`, `apiDelete(...)` call with `authFetch(...)` using appropriate options:

- `apiPost(endpoint, body, timeout)` → `authFetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, timeout)`
- `apiPatch(endpoint, body)` → `authFetch(endpoint, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })`
- `apiDelete(endpoint)` → `authFetch(endpoint, { method: 'DELETE' })`
- `apiFetch(endpoint)` → `authFetch(endpoint)`
- `apiFetch(url)` for absolute URLs → `authFetch(url)`

The `getResumePdfUrl` and `getCoverLetterPdfUrl` functions return URL strings (used in `<a>` tags or `window.open`), not fetch calls. These stay unchanged — PDF downloads opened in a new tab won't have the token. If PDF downloads need auth in the future, that's a separate concern.

Wait — `downloadResumePdf` and `downloadCoverLetterPdf` DO use `apiFetch` to fetch the blob. Change those to `authFetch`.

**Step 2: Update enrichment.ts**

Replace the import:
```typescript
// Before:
import { apiFetch, apiPost } from './client';
// After:
import { authFetch } from './client';
```

Replace all calls:
- `apiFetch(url, { method: 'POST', credentials: 'include' })` → `authFetch(url, { method: 'POST' })`
- `apiPost(endpoint, body)` → `authFetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })`

**Step 3: Update config.ts — only `/status`**

Find the `getStatus` function (or equivalent) that calls `apiFetch('/status', ...)` and change it to `authFetch('/status', ...)`. Leave all other config calls unchanged.

**Step 4: Run lint**

Run: `cd apps/frontend && npm run lint`
Expected: PASS (no unused imports, etc.)

**Step 5: Commit**

```bash
git add apps/frontend/lib/api/resume.ts apps/frontend/lib/api/enrichment.ts apps/frontend/lib/api/config.ts
git commit -m "feat(m4): switch frontend data API calls to authFetch"
```

---

### Task 11: Fix Existing Integration Tests

**Files:**
- Modify: `apps/backend/tests/integration/test_resume_api.py`
- Modify: `apps/backend/tests/integration/test_jobs_api.py`
- Modify: `apps/backend/tests/integration/test_health_api.py`
- Modify: `apps/backend/tests/integration/test_config_api.py`
- Modify: `apps/backend/tests/integration/test_regenerate_endpoints.py`
- Modify: `apps/backend/tests/unit/test_database.py`

**Step 1: Identify all failing tests**

Run: `cd apps/backend && uv run pytest --tb=short 2>&1 | head -100`

Every integration test that calls data endpoints without auth will fail with 401.
Every unit test that calls DB methods without `user_id` will fail with TypeError.

**Step 2: Fix unit/test_database.py**

Any test calling `db.create_resume()`, `db.get_resume()`, etc. needs the `user_id` parameter added. Create a user first:

```python
user = await db.create_user(email="test@test.com")
resume = await db.create_resume(content="# Test", user_id=user["id"])
fetched = await db.get_resume(resume["resume_id"], user["id"])
```

**Step 3: Fix integration tests**

For each integration test file, add the `auth_headers_a` fixture and pass headers to requests:

```python
# Before:
resp = await client.get("/api/v1/resumes/list")

# After:
resp = await client.get("/api/v1/resumes/list", headers=auth_headers_a)
```

For tests that create data via db directly, also add `user_id`:
```python
# Before:
resume = await test_db.create_resume(content="# Test")

# After:
user, _ = auth_user_a
resume = await test_db.create_resume(content="# Test", user_id=user["id"])
```

For `test_health_api.py`, the `GET /status` test needs auth headers. The `GET /health` test stays unchanged (public).

For `test_config_api.py`, tests are unchanged (config endpoints are deferred, still unauthenticated).

**Step 4: Run full test suite**

Run: `cd apps/backend && uv run pytest -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add apps/backend/tests/
git commit -m "fix(m4): update existing tests for user_id scoping and auth requirements"
```

---

### Task 12: Final Verification & Cleanup

**Step 1: Run full backend test suite**

```bash
cd apps/backend && uv run pytest -v --tb=short
```

Expected: ALL PASS

**Step 2: Run frontend lint**

```bash
cd apps/frontend && npm run lint
```

Expected: PASS

**Step 3: Run frontend format**

```bash
cd apps/frontend && npm run format
```

**Step 4: Final commit if any format changes**

```bash
git add -A
git commit -m "chore(m4): format and lint cleanup"
```

**Step 5: Create PR**

Create a PR targeting `fork/main` with title: `feat: M4 multi-user data isolation`

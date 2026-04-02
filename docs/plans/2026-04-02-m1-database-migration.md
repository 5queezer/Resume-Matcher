# M1: Database Migration — TinyDB to SQLAlchemy 2.0 Async

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace TinyDB with SQLAlchemy 2.0 async, supporting PostgreSQL (prod) and SQLite (dev), while preserving the exact same `Database` class interface so routers don't change.

**Architecture:** Swap the storage engine behind `app/database.py` from TinyDB to SQLAlchemy async. The `Database` class keeps its method signatures — routers still call `db.create_resume(...)`, `db.get_job(...)`, etc. The only visible change is a new `DATABASE_URL` env var and Alembic migrations. A new `User` model is added but not wired to endpoints yet (M2 handles that).

**Tech Stack:** SQLAlchemy 2.0 async, asyncpg (Postgres), aiosqlite (SQLite), Alembic, argon2-cffi (future-proofing for M2)

---

## File Map

```text
apps/backend/
├── app/
│   ├── config.py              # MODIFY: add DATABASE_URL setting
│   ├── database.py            # REWRITE: SQLAlchemy async engine + same interface
│   ├── models.py              # CREATE: SQLAlchemy ORM models
│   ├── main.py                # MODIFY: async engine lifecycle
│   └── routers/               # NO CHANGES (interface preserved)
├── alembic.ini                # CREATE: Alembic config
├── alembic/                   # CREATE: migrations directory
│   ├── env.py
│   └── versions/
│       └── 74770b4ed9d5_initial_schema.py
├── pyproject.toml             # MODIFY: swap deps
└── tests/
    └── unit/
        └── test_database.py   # CREATE: database layer tests
```

---

### Task 1: Add Dependencies

**Files:**
- Modify: `apps/backend/pyproject.toml`

**Step 1: Update pyproject.toml**

Replace `tinydb==4.8.2` with the new database stack:

```toml
[project]
dependencies = [
    "fastapi==0.128.4",
    "uvicorn==0.40.0",
    "python-multipart==0.0.22",
    "pydantic==2.12.5",
    "pydantic-settings==2.12.0",
    "sqlalchemy[asyncio]==2.0.41",
    "aiosqlite==0.21.0",
    "asyncpg==0.30.0",
    "alembic==1.15.2",
    "litellm==1.81.8",
    "markitdown[docx]==0.1.4",
    "pdfminer.six==20260107",
    "playwright==1.58.0",
    "python-docx==1.2.0",
    "python-dotenv==1.2.1",
]
```

**Step 2: Install**

```bash
cd apps/backend && uv sync
```

Expected: Clean install, no errors.

**Step 3: Commit**

```bash
git add apps/backend/pyproject.toml apps/backend/uv.lock
git commit -m "feat(m1): swap tinydb for sqlalchemy async + alembic"
```

---

### Task 2: Add DATABASE_URL to Settings

**Files:**
- Modify: `apps/backend/app/config.py`

**Step 1: Write the failing test**

Create `apps/backend/tests/unit/test_config_database_url.py`:

```python
"""Test DATABASE_URL configuration."""

import pytest
from app.config import Settings


def test_default_database_url_is_sqlite():
    """Default DATABASE_URL should be a local SQLite file."""
    s = Settings(llm_api_key="test")
    assert s.database_url.startswith("sqlite+aiosqlite:///")
    assert "database.db" in s.database_url


def test_database_url_from_env(monkeypatch):
    """DATABASE_URL env var should override default."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/rm")
    s = Settings(llm_api_key="test")
    assert s.database_url == "postgresql+asyncpg://user:pass@localhost/rm"
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_config_database_url.py -v
```

Expected: FAIL — `Settings` has no `database_url` attribute.

**Step 3: Add database_url to Settings**

In `apps/backend/app/config.py`, add to the `Settings` class after `llm_api_base`:

```python
    # Database Configuration
    database_url: str = ""

    @property
    def effective_database_url(self) -> str:
        """Resolve DATABASE_URL with fallback to local SQLite."""
        if self.database_url:
            return self.database_url
        db_file = self.data_dir / "database.db"
        return f"sqlite+aiosqlite:///{db_file}"
```

Update the test to use `effective_database_url`:

```python
def test_default_database_url_is_sqlite():
    s = Settings(llm_api_key="test")
    assert s.effective_database_url.startswith("sqlite+aiosqlite:///")
    assert "database.db" in s.effective_database_url


def test_database_url_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/rm")
    s = Settings(llm_api_key="test")
    assert s.effective_database_url == "postgresql+asyncpg://user:pass@localhost/rm"
```

**Step 4: Run test to verify it passes**

```bash
cd apps/backend && uv run pytest tests/unit/test_config_database_url.py -v
```

Expected: PASS

**Step 5: Remove dead TinyDB properties**

Remove `db_path` property from Settings (lines 182-185 in config.py). Keep `data_dir` and `config_path`.

**Step 6: Commit**

```bash
git add apps/backend/app/config.py apps/backend/tests/unit/test_config_database_url.py
git commit -m "feat(m1): add DATABASE_URL setting with SQLite default"
```

---

### Task 3: Create SQLAlchemy ORM Models

**Files:**
- Create: `apps/backend/app/models.py`

**Step 1: Write the failing test**

Create `apps/backend/tests/unit/test_models.py`:

```python
"""Test SQLAlchemy ORM models."""

import pytest
from sqlalchemy import inspect
from app.models import Base, User, Resume, Job, Improvement


def test_user_table_name():
    assert User.__tablename__ == "users"


def test_resume_table_name():
    assert Resume.__tablename__ == "resumes"


def test_job_table_name():
    assert Job.__tablename__ == "jobs"


def test_improvement_table_name():
    assert Improvement.__tablename__ == "improvements"


def test_resume_has_user_fk():
    """Resume model must have a user_id foreign key for M4."""
    mapper = inspect(Resume)
    col = mapper.columns["user_id"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "users.id"


def test_all_models_registered_on_base():
    """All models must be discoverable by Alembic via Base.metadata."""
    table_names = set(Base.metadata.tables.keys())
    assert {"users", "resumes", "jobs", "improvements"} <= table_names
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_models.py -v
```

Expected: FAIL — `app.models` does not exist.

**Step 3: Create the models**

Create `apps/backend/app/models.py`:

```python
"""SQLAlchemy ORM models."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    type_annotation_map = {
        dict: JSONB,
    }


class User(Base):
    """User account — wired in M2, but the table exists from day one."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    resumes: Mapped[list["Resume"]] = relationship(back_populates="user")


class Resume(Base):
    """Resume storage — mirrors TinyDB fields exactly."""

    __tablename__ = "resumes"

    resume_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(10), default="md")
    filename: Mapped[str | None] = mapped_column(String(255))
    is_master: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[str | None] = mapped_column(String(36), index=True)
    processed_data: Mapped[dict | None] = mapped_column(nullable=True)
    processing_status: Mapped[str] = mapped_column(String(20), default="pending")
    cover_letter: Mapped[str | None] = mapped_column(Text)
    outreach_message: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(500))
    original_markdown: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User | None"] = relationship(back_populates="resumes")


class Job(Base):
    """Job description storage."""

    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    resume_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Improvement(Base):
    """Improvement tracking — links original resume, tailored resume, and job."""

    __tablename__ = "improvements"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    original_resume_id: Mapped[str] = mapped_column(String(36), index=True)
    tailored_resume_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    improvements: Mapped[dict | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

> **Note on JSONB:** SQLAlchemy's `JSONB` type automatically falls back to `JSON` on SQLite. The `type_annotation_map` on `Base` makes `dict` fields use JSONB on Postgres and JSON on SQLite — no conditional logic needed.

**Step 4: Run test to verify it passes**

```bash
cd apps/backend && uv run pytest tests/unit/test_models.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add apps/backend/app/models.py apps/backend/tests/unit/test_models.py
git commit -m "feat(m1): add SQLAlchemy ORM models for User, Resume, Job, Improvement"
```

---

### Task 4: Rewrite Database Class with SQLAlchemy Async

**Files:**
- Rewrite: `apps/backend/app/database.py`
- Create: `apps/backend/tests/unit/test_database.py`

This is the core task. The new `Database` class must expose the **exact same method signatures** as the old TinyDB one, but all methods become `async`. Routers already use `await` for `create_resume_atomic_master` but call other methods synchronously — those callers will need `await` added.

**Step 1: Write the failing test**

Create `apps/backend/tests/unit/test_database.py`:

```python
"""Test async Database wrapper over SQLAlchemy."""

import pytest
from app.database import Database


@pytest.fixture
async def db():
    """Create an in-memory SQLite database for testing."""
    database = Database("sqlite+aiosqlite://")
    await database.init()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_create_and_get_resume(db):
    resume = await db.create_resume(content="# Test", content_type="md")
    assert resume["resume_id"]
    assert resume["content"] == "# Test"
    assert resume["processing_status"] == "pending"

    fetched = await db.get_resume(resume["resume_id"])
    assert fetched is not None
    assert fetched["resume_id"] == resume["resume_id"]


@pytest.mark.asyncio
async def test_get_resume_not_found(db):
    result = await db.get_resume("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_update_resume(db):
    resume = await db.create_resume(content="# Old")
    updated = await db.update_resume(resume["resume_id"], {"content": "# New"})
    assert updated["content"] == "# New"
    assert updated["updated_at"] != resume["created_at"]


@pytest.mark.asyncio
async def test_update_resume_not_found(db):
    with pytest.raises(ValueError, match="Resume not found"):
        await db.update_resume("nonexistent", {"content": "x"})


@pytest.mark.asyncio
async def test_delete_resume(db):
    resume = await db.create_resume(content="# Delete me")
    assert await db.delete_resume(resume["resume_id"]) is True
    assert await db.get_resume(resume["resume_id"]) is None


@pytest.mark.asyncio
async def test_delete_resume_not_found(db):
    assert await db.delete_resume("nonexistent") is False


@pytest.mark.asyncio
async def test_list_resumes(db):
    await db.create_resume(content="# A")
    await db.create_resume(content="# B")
    resumes = await db.list_resumes()
    assert len(resumes) == 2


@pytest.mark.asyncio
async def test_master_resume_atomic(db):
    r1 = await db.create_resume_atomic_master(content="# First")
    assert r1["is_master"] is True

    r2 = await db.create_resume_atomic_master(content="# Second")
    assert r2["is_master"] is False

    master = await db.get_master_resume()
    assert master["resume_id"] == r1["resume_id"]


@pytest.mark.asyncio
async def test_set_master_resume(db):
    r1 = await db.create_resume_atomic_master(content="# First")
    r2 = await db.create_resume(content="# Second")

    result = await db.set_master_resume(r2["resume_id"])
    assert result is True

    master = await db.get_master_resume()
    assert master["resume_id"] == r2["resume_id"]

    old = await db.get_resume(r1["resume_id"])
    assert old["is_master"] is False


@pytest.mark.asyncio
async def test_create_and_get_job(db):
    job = await db.create_job(content="Backend engineer needed")
    assert job["job_id"]
    fetched = await db.get_job(job["job_id"])
    assert fetched["content"] == "Backend engineer needed"


@pytest.mark.asyncio
async def test_update_job(db):
    job = await db.create_job(content="Original")
    updated = await db.update_job(job["job_id"], {"content": "Updated"})
    assert updated["content"] == "Updated"


@pytest.mark.asyncio
async def test_create_and_get_improvement(db):
    imp = await db.create_improvement(
        original_resume_id="orig-1",
        tailored_resume_id="tail-1",
        job_id="job-1",
        improvements=[{"suggestion": "Add Python", "lineNumber": 5}],
    )
    assert imp["request_id"]

    fetched = await db.get_improvement_by_tailored_resume("tail-1")
    assert fetched is not None
    assert fetched["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_get_stats(db):
    await db.create_resume(content="# Test", is_master=True)
    await db.create_job(content="Job desc")
    stats = await db.get_stats()
    assert stats["total_resumes"] == 1
    assert stats["total_jobs"] == 1
    assert stats["has_master_resume"] is True


@pytest.mark.asyncio
async def test_reset_database(db):
    await db.create_resume(content="# Test")
    await db.create_job(content="Job")
    await db.reset_database()
    stats = await db.get_stats()
    assert stats["total_resumes"] == 0
    assert stats["total_jobs"] == 0
```

**Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_database.py -v
```

Expected: FAIL — new Database class doesn't exist yet.

**Step 3: Rewrite database.py**

Replace `apps/backend/app/database.py` entirely:

```python
"""Async SQLAlchemy database layer for Resume Matcher."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base, Improvement, Job, Resume

logger = logging.getLogger(__name__)


class Database:
    """Async database wrapper preserving the TinyDB-era interface.

    All public methods return plain dicts (not ORM objects) so routers
    don't need to change.
    """

    _master_resume_lock = asyncio.Lock()

    def __init__(self, url: str | None = None):
        self._url = url or settings.effective_database_url
        connect_args = {}
        if self._url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self._engine = create_async_engine(self._url, connect_args=connect_args)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init(self) -> None:
        """Create tables. In production use Alembic instead."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose engine and connection pool."""
        await self._engine.dispose()

    def _session(self) -> AsyncSession:
        return self._session_factory()

    @staticmethod
    def _resume_to_dict(r: Resume) -> dict[str, Any]:
        return {
            "resume_id": r.resume_id,
            "content": r.content,
            "content_type": r.content_type,
            "filename": r.filename,
            "is_master": r.is_master,
            "parent_id": r.parent_id,
            "processed_data": r.processed_data,
            "processing_status": r.processing_status,
            "cover_letter": r.cover_letter,
            "outreach_message": r.outreach_message,
            "title": r.title,
            "original_markdown": r.original_markdown,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }

    @staticmethod
    def _job_to_dict(j: Job) -> dict[str, Any]:
        d: dict[str, Any] = {
            "job_id": j.job_id,
            "content": j.content,
            "resume_id": j.resume_id,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        return d

    @staticmethod
    def _improvement_to_dict(i: Improvement) -> dict[str, Any]:
        return {
            "request_id": i.request_id,
            "original_resume_id": i.original_resume_id,
            "tailored_resume_id": i.tailored_resume_id,
            "job_id": i.job_id,
            "improvements": i.improvements,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }

    # ── Resume operations ──────────────────────────────────────────

    async def create_resume(
        self,
        content: str,
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
        """Create a new resume entry."""
        resume = Resume(
            resume_id=str(uuid4()),
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

    async def create_resume_atomic_master(
        self,
        content: str,
        content_type: str = "md",
        filename: str | None = None,
        processed_data: dict[str, Any] | None = None,
        processing_status: str = "pending",
        cover_letter: str | None = None,
        outreach_message: str | None = None,
        original_markdown: str | None = None,
    ) -> dict[str, Any]:
        """Create a resume with atomic master assignment."""
        async with self._master_resume_lock:
            current_master = await self.get_master_resume()
            is_master = current_master is None

            if current_master and current_master.get("processing_status") in (
                "failed",
                "processing",
            ):
                await self.update_resume(
                    current_master["resume_id"], {"is_master": False}
                )
                is_master = True

            return await self.create_resume(
                content=content,
                content_type=content_type,
                filename=filename,
                is_master=is_master,
                processed_data=processed_data,
                processing_status=processing_status,
                cover_letter=cover_letter,
                outreach_message=outreach_message,
                original_markdown=original_markdown,
            )

    async def get_resume(self, resume_id: str) -> dict[str, Any] | None:
        """Get resume by ID."""
        async with self._session() as session:
            result = await session.execute(
                select(Resume).where(Resume.resume_id == resume_id)
            )
            row = result.scalar_one_or_none()
            return self._resume_to_dict(row) if row else None

    async def get_master_resume(self) -> dict[str, Any] | None:
        """Get the master resume."""
        async with self._session() as session:
            result = await session.execute(
                select(Resume).where(Resume.is_master == True)
            )
            row = result.scalar_one_or_none()
            return self._resume_to_dict(row) if row else None

    async def update_resume(
        self, resume_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Update resume by ID. Raises ValueError if not found."""
        updates["updated_at"] = datetime.now(timezone.utc)
        async with self._session() as session:
            result = await session.execute(
                update(Resume)
                .where(Resume.resume_id == resume_id)
                .values(**updates)
            )
            if result.rowcount == 0:
                raise ValueError(f"Resume not found: {resume_id}")
            await session.commit()
        return await self.get_resume(resume_id)

    async def delete_resume(self, resume_id: str) -> bool:
        """Delete resume by ID."""
        async with self._session() as session:
            result = await session.execute(
                delete(Resume).where(Resume.resume_id == resume_id)
            )
            await session.commit()
            return result.rowcount > 0

    async def list_resumes(self) -> list[dict[str, Any]]:
        """List all resumes."""
        async with self._session() as session:
            result = await session.execute(select(Resume))
            return [self._resume_to_dict(r) for r in result.scalars().all()]

    async def set_master_resume(self, resume_id: str) -> bool:
        """Set a resume as master, unsetting any existing master."""
        async with self._session() as session:
            # Verify target exists
            target = await session.execute(
                select(Resume).where(Resume.resume_id == resume_id)
            )
            if not target.scalar_one_or_none():
                logger.warning("Cannot set master: resume %s not found", resume_id)
                return False

            # Unset current master
            await session.execute(
                update(Resume).where(Resume.is_master == True).values(is_master=False)
            )
            # Set new master
            await session.execute(
                update(Resume)
                .where(Resume.resume_id == resume_id)
                .values(is_master=True)
            )
            await session.commit()
            return True

    # ── Job operations ─────────────────────────────────────────────

    async def create_job(
        self, content: str, resume_id: str | None = None
    ) -> dict[str, Any]:
        """Create a new job description entry."""
        job = Job(
            job_id=str(uuid4()),
            content=content,
            resume_id=resume_id,
        )
        async with self._session() as session:
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return self._job_to_dict(job)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Get job by ID."""
        async with self._session() as session:
            result = await session.execute(
                select(Job).where(Job.job_id == job_id)
            )
            row = result.scalar_one_or_none()
            return self._job_to_dict(row) if row else None

    async def update_job(
        self, job_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a job by ID."""
        async with self._session() as session:
            result = await session.execute(
                update(Job).where(Job.job_id == job_id).values(**updates)
            )
            if result.rowcount == 0:
                return None
            await session.commit()
        return await self.get_job(job_id)

    # ── Improvement operations ─────────────────────────────────────

    async def create_improvement(
        self,
        original_resume_id: str,
        tailored_resume_id: str,
        job_id: str,
        improvements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create an improvement result entry."""
        imp = Improvement(
            request_id=str(uuid4()),
            original_resume_id=original_resume_id,
            tailored_resume_id=tailored_resume_id,
            job_id=job_id,
            improvements=improvements,
        )
        async with self._session() as session:
            session.add(imp)
            await session.commit()
            await session.refresh(imp)
            return self._improvement_to_dict(imp)

    async def get_improvement_by_tailored_resume(
        self, tailored_resume_id: str
    ) -> dict[str, Any] | None:
        """Get improvement record by tailored resume ID."""
        async with self._session() as session:
            result = await session.execute(
                select(Improvement).where(
                    Improvement.tailored_resume_id == tailored_resume_id
                )
            )
            row = result.scalar_one_or_none()
            return self._improvement_to_dict(row) if row else None

    # ── Stats & admin ──────────────────────────────────────────────

    async def get_stats(self) -> dict[str, Any]:
        """Get database statistics."""
        async with self._session() as session:
            resumes = (await session.execute(select(Resume))).scalars().all()
            jobs = (await session.execute(select(Job))).scalars().all()
            improvements = (
                (await session.execute(select(Improvement))).scalars().all()
            )
            master = await session.execute(
                select(Resume).where(Resume.is_master == True)
            )
            return {
                "total_resumes": len(resumes),
                "total_jobs": len(jobs),
                "total_improvements": len(improvements),
                "has_master_resume": master.scalar_one_or_none() is not None,
            }

    async def reset_database(self) -> None:
        """Reset by truncating all tables."""
        async with self._session() as session:
            await session.execute(delete(Improvement))
            await session.execute(delete(Job))
            await session.execute(delete(Resume))
            await session.commit()

        # Clear uploads directory
        uploads_dir = settings.data_dir / "uploads"
        if uploads_dir.exists():
            import shutil

            shutil.rmtree(uploads_dir)
            uploads_dir.mkdir(parents=True, exist_ok=True)


# Global database instance — initialized in main.py lifespan
db = Database()
```

**Step 4: Run test to verify it passes**

```bash
cd apps/backend && uv run pytest tests/unit/test_database.py -v
```

Expected: All 16 tests PASS.

**Step 5: Commit**

```bash
git add apps/backend/app/database.py apps/backend/tests/unit/test_database.py
git commit -m "feat(m1): rewrite database.py with SQLAlchemy async, same interface"
```

---

### Task 5: Update main.py Lifespan for Async Init

**Files:**
- Modify: `apps/backend/app/main.py`

**Step 1: Update lifespan**

The database now needs `await db.init()` on startup and `await db.close()` on shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await db.init()
    yield
    try:
        await close_pdf_renderer()
    except Exception as e:
        logger.error(f"Error closing PDF renderer: {e}")
    try:
        await db.close()
    except Exception as e:
        logger.error(f"Error closing database: {e}")
```

**Step 2: Commit**

```bash
git add apps/backend/app/main.py
git commit -m "feat(m1): async db init/close in FastAPI lifespan"
```

---

### Task 6: Add `await` to All Router Database Calls

**Files:**
- Modify: `apps/backend/app/routers/resumes.py`
- Modify: `apps/backend/app/routers/jobs.py`
- Modify: `apps/backend/app/routers/health.py`
- Modify: `apps/backend/app/routers/config.py`
- Modify: `apps/backend/app/routers/enrichment.py`

Every `db.` call that was previously synchronous must become `await db.`. This is a mechanical find-and-replace.

**Pattern:**

| Old | New |
|-----|-----|
| `db.create_resume(...)` | `await db.create_resume(...)` |
| `db.get_resume(...)` | `await db.get_resume(...)` |
| `db.update_resume(...)` | `await db.update_resume(...)` |
| `db.delete_resume(...)` | `await db.delete_resume(...)` |
| `db.list_resumes()` | `await db.list_resumes()` |
| `db.get_master_resume()` | `await db.get_master_resume()` |
| `db.set_master_resume(...)` | `await db.set_master_resume(...)` |
| `db.create_job(...)` | `await db.create_job(...)` |
| `db.get_job(...)` | `await db.get_job(...)` |
| `db.update_job(...)` | `await db.update_job(...)` |
| `db.create_improvement(...)` | `await db.create_improvement(...)` |
| `db.get_improvement_by_tailored_resume(...)` | `await db.get_improvement_by_tailored_resume(...)` |
| `db.get_stats()` | `await db.get_stats()` |
| `db.reset_database()` | `await db.reset_database()` |
| `db.close()` | `await db.close()` |

**Important:** All router functions are already `async def`, so adding `await` is safe.

**Step 1: Apply the await changes across all routers**

Search each file for `db.` calls and prefix with `await`. Every function calling `db.*` is already `async def`.

**Step 2: Run existing integration tests**

```bash
cd apps/backend && uv run pytest tests/ -v
```

Expected: Tests may need fixture updates (see Task 7).

**Step 3: Commit**

```bash
git add apps/backend/app/routers/
git commit -m "feat(m1): add await to all router db calls"
```

---

### Task 7: Update Test Fixtures for Async Database

**Files:**
- Modify: `apps/backend/tests/conftest.py`
- Modify: all integration test files

**Step 1: Add async db fixture to conftest.py**

Add to `apps/backend/tests/conftest.py`:

```python
from httpx import ASGITransport, AsyncClient
from app.database import Database, db as global_db
from app.main import app


@pytest.fixture
async def test_db():
    """Provide a clean in-memory SQLite database per test."""
    test_database = Database("sqlite+aiosqlite://")
    await test_database.init()
    yield test_database
    await test_database.close()


@pytest.fixture
async def client(test_db, monkeypatch):
    """Async HTTP client with test database injected."""
    # Replace the global db with our test db
    import app.database as db_module
    monkeypatch.setattr(db_module, "db", test_db)
    # Re-init tables (lifespan won't run with this pattern)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
```

**Step 2: Update integration tests to use `client` fixture**

Each integration test file should use the `client` fixture and make HTTP requests through it, rather than calling `db` directly. The exact changes depend on each test file's current approach.

**Step 3: Run all tests**

```bash
cd apps/backend && uv run pytest tests/ -v
```

Expected: All tests PASS.

**Step 4: Commit**

```bash
git add apps/backend/tests/
git commit -m "feat(m1): update test fixtures for async SQLAlchemy database"
```

---

### Task 8: Set Up Alembic

**Files:**
- Create: `apps/backend/alembic.ini`
- Create: `apps/backend/alembic/env.py`
- Create: `apps/backend/alembic/script.py.mako`
- Create: `apps/backend/alembic/versions/<revid>_initial_schema.py`

**Step 1: Initialize Alembic**

```bash
cd apps/backend && uv run alembic init alembic
```

**Step 2: Configure alembic.ini**

Set the default sqlalchemy.url:

```ini
sqlalchemy.url = sqlite+aiosqlite:///data/database.db
```

**Step 3: Edit alembic/env.py for async**

Replace `alembic/env.py` with async version that imports `app.models.Base`:

```python
"""Alembic async migration environment."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = settings.effective_database_url
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode with async engine."""
    engine = create_async_engine(settings.effective_database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

**Step 4: Generate initial migration**

```bash
cd apps/backend && uv run alembic revision --autogenerate -m "initial schema"
```

Expected: Creates a migration file in `alembic/versions/`.

**Step 5: Test migration**

```bash
cd apps/backend && uv run alembic upgrade head
```

Expected: Creates `data/database.db` with all 4 tables.

**Step 6: Verify**

```bash
cd apps/backend && uv run python -c "
import sqlite3
conn = sqlite3.connect('data/database.db')
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
print([t[0] for t in tables])
conn.close()
"
```

Expected: `['alembic_version', 'users', 'resumes', 'jobs', 'improvements']`

**Step 7: Commit**

```bash
git add apps/backend/alembic.ini apps/backend/alembic/
git commit -m "feat(m1): add Alembic async migrations with initial schema"
```

---

### Task 9: Clean Up — Remove TinyDB Artifacts

**Files:**
- Delete: `apps/backend/data/database.json` (if exists, gitignored)
- Modify: `apps/backend/app/config.py` — remove `db_path` property
- Verify: no remaining `tinydb` imports

**Step 1: Search for stale references**

```bash
cd apps/backend && grep -r "tinydb\|TinyDB\|database\.json" app/ tests/ --include="*.py"
```

Expected: No matches.

**Step 2: Remove db_path from config**

The `db_path` property in `config.py` (lines 182-185) should have been removed in Task 2. Verify it's gone.

**Step 3: Add database.db to .gitignore**

Add to the project `.gitignore`:

```gitignore
apps/backend/data/database.db
```

**Step 4: Run full test suite**

```bash
cd apps/backend && uv run pytest tests/ -v
```

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add -A
git commit -m "chore(m1): remove TinyDB artifacts, add database.db to gitignore"
```

---

### Task 10: Job Model — Dynamic Fields Support

**Files:**
- Modify: `apps/backend/app/models.py`
- Modify: `apps/backend/app/database.py`

The TinyDB Job had dynamic fields added at runtime (`job_keywords`, `job_keywords_hash`, `preview_hash`, `preview_hashes`, `preview_prompt_id`). These were stored as arbitrary keys on the JSON document. With SQL, we need explicit columns or a JSON extras column.

**Step 1: Add columns to Job model**

Add to the `Job` class in `models.py`:

```python
    job_keywords: Mapped[dict | None] = mapped_column(nullable=True)
    job_keywords_hash: Mapped[str | None] = mapped_column(String(64))
    preview_hash: Mapped[str | None] = mapped_column(String(64))
    preview_prompt_id: Mapped[str | None] = mapped_column(String(50))
    preview_hashes: Mapped[dict | None] = mapped_column(nullable=True)
```

**Step 2: Update `_job_to_dict` in database.py**

```python
    @staticmethod
    def _job_to_dict(j: Job) -> dict[str, Any]:
        d: dict[str, Any] = {
            "job_id": j.job_id,
            "content": j.content,
            "resume_id": j.resume_id,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        # Include dynamic fields only if set
        if j.job_keywords is not None:
            d["job_keywords"] = j.job_keywords
        if j.job_keywords_hash is not None:
            d["job_keywords_hash"] = j.job_keywords_hash
        if j.preview_hash is not None:
            d["preview_hash"] = j.preview_hash
        if j.preview_prompt_id is not None:
            d["preview_prompt_id"] = j.preview_prompt_id
        if j.preview_hashes is not None:
            d["preview_hashes"] = j.preview_hashes
        return d
```

**Step 3: Generate migration**

```bash
cd apps/backend && uv run alembic revision --autogenerate -m "add job dynamic fields"
```

**Step 4: Apply migration**

```bash
cd apps/backend && uv run alembic upgrade head
```

**Step 5: Run tests**

```bash
cd apps/backend && uv run pytest tests/ -v
```

**Step 6: Commit**

```bash
git add apps/backend/app/models.py apps/backend/app/database.py apps/backend/alembic/
git commit -m "feat(m1): add explicit Job columns for keywords and preview hashes"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Swap deps | pyproject.toml |
| 2 | DATABASE_URL setting | config.py |
| 3 | ORM models | models.py (new) |
| 4 | Rewrite Database class | database.py |
| 5 | Async lifespan | main.py |
| 6 | Add `await` to routers | all routers |
| 7 | Update test fixtures | tests/ |
| 8 | Alembic setup | alembic/ |
| 9 | Remove TinyDB artifacts | cleanup |
| 10 | Job dynamic fields | models.py, database.py |

After M1, the app runs identically but on SQLAlchemy async with SQLite locally and Postgres-ready for prod. The `User` table exists but isn't wired — that's M2.

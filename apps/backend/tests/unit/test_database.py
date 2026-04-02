"""Test async Database wrapper over SQLAlchemy."""

import pytest
from app.database import Database


@pytest.fixture
async def db():
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
    assert await db.get_resume("nonexistent") is None


@pytest.mark.asyncio
async def test_update_resume(db):
    resume = await db.create_resume(content="# Old")
    updated = await db.update_resume(resume["resume_id"], {"content": "# New"})
    assert updated["content"] == "# New"


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
    assert len(await db.list_resumes()) == 2


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
    assert await db.set_master_resume(r2["resume_id"]) is True
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
        original_resume_id="orig-1", tailored_resume_id="tail-1",
        job_id="job-1", improvements=[{"suggestion": "Add Python", "lineNumber": 5}],
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

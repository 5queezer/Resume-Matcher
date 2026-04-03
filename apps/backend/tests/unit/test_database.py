"""Test async Database wrapper over SQLAlchemy."""

import pytest
from app.database import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite://")
    await database.init()
    yield database
    await database.close()


@pytest.fixture
async def user(db):
    return await db.create_user(email="test@test.com", hashed_password="hash")


@pytest.mark.asyncio
async def test_create_and_get_resume(db, user):
    resume = await db.create_resume(content="# Test", content_type="md", user_id=user["id"])
    assert resume["resume_id"]
    assert resume["content"] == "# Test"
    assert resume["processing_status"] == "pending"
    fetched = await db.get_resume(resume["resume_id"], user["id"])
    assert fetched is not None
    assert fetched["resume_id"] == resume["resume_id"]


@pytest.mark.asyncio
async def test_get_resume_not_found(db, user):
    assert await db.get_resume("nonexistent", user["id"]) is None


@pytest.mark.asyncio
async def test_update_resume(db, user):
    resume = await db.create_resume(content="# Old", user_id=user["id"])
    updated = await db.update_resume(resume["resume_id"], user["id"], {"content": "# New"})
    assert updated["content"] == "# New"


@pytest.mark.asyncio
async def test_update_resume_not_found(db, user):
    with pytest.raises(ValueError, match="Resume not found"):
        await db.update_resume("nonexistent", user["id"], {"content": "x"})


@pytest.mark.asyncio
async def test_delete_resume(db, user):
    resume = await db.create_resume(content="# Delete me", user_id=user["id"])
    assert await db.delete_resume(resume["resume_id"], user["id"]) is True
    assert await db.get_resume(resume["resume_id"], user["id"]) is None


@pytest.mark.asyncio
async def test_delete_resume_not_found(db, user):
    assert await db.delete_resume("nonexistent", user["id"]) is False


@pytest.mark.asyncio
async def test_list_resumes(db, user):
    await db.create_resume(content="# A", user_id=user["id"])
    await db.create_resume(content="# B", user_id=user["id"])
    assert len(await db.list_resumes(user["id"])) == 2


@pytest.mark.asyncio
async def test_master_resume_atomic(db, user):
    r1 = await db.create_resume_atomic_master(content="# First", user_id=user["id"])
    assert r1["is_master"] is True
    r2 = await db.create_resume_atomic_master(content="# Second", user_id=user["id"])
    assert r2["is_master"] is False
    master = await db.get_master_resume(user["id"])
    assert master["resume_id"] == r1["resume_id"]


@pytest.mark.asyncio
async def test_set_master_resume(db, user):
    r1 = await db.create_resume_atomic_master(content="# First", user_id=user["id"])
    r2 = await db.create_resume(content="# Second", user_id=user["id"])
    assert await db.set_master_resume(r2["resume_id"], user["id"]) is True
    master = await db.get_master_resume(user["id"])
    assert master["resume_id"] == r2["resume_id"]
    old = await db.get_resume(r1["resume_id"], user["id"])
    assert old["is_master"] is False


@pytest.mark.asyncio
async def test_create_and_get_job(db, user):
    job = await db.create_job(content="Backend engineer needed", user_id=user["id"])
    assert job["job_id"]
    fetched = await db.get_job(job["job_id"], user["id"])
    assert fetched["content"] == "Backend engineer needed"


@pytest.mark.asyncio
async def test_update_job(db, user):
    job = await db.create_job(content="Original", user_id=user["id"])
    updated = await db.update_job(job["job_id"], user["id"], {"content": "Updated"})
    assert updated["content"] == "Updated"


@pytest.mark.asyncio
async def test_create_and_get_improvement(db, user):
    imp = await db.create_improvement(
        original_resume_id="orig-1", tailored_resume_id="tail-1",
        job_id="job-1", improvements=[{"suggestion": "Add Python", "lineNumber": 5}],
        user_id=user["id"],
    )
    assert imp["request_id"]
    fetched = await db.get_improvement_by_tailored_resume("tail-1", user["id"])
    assert fetched is not None
    assert fetched["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_get_stats(db, user):
    await db.create_resume(content="# Test", is_master=True, user_id=user["id"])
    await db.create_job(content="Job desc", user_id=user["id"])
    stats = await db.get_stats(user["id"])
    assert stats["total_resumes"] == 1
    assert stats["total_jobs"] == 1
    assert stats["has_master_resume"] is True


@pytest.mark.asyncio
async def test_reset_database(db, user):
    await db.create_resume(content="# Test", user_id=user["id"])
    await db.create_job(content="Job", user_id=user["id"])
    await db.reset_database()
    stats = await db.get_stats(user["id"])
    assert stats["total_resumes"] == 0
    assert stats["total_jobs"] == 0

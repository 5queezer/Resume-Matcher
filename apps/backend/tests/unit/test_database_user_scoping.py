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


class TestResumeScoping:
    @pytest.mark.asyncio
    async def test_create_resume_requires_user_id(self, db, user_a):
        resume = await db.create_resume(content="# Resume", user_id=user_a["id"])
        assert resume["resume_id"]

    @pytest.mark.asyncio
    async def test_get_resume_scoped_to_user(self, db, user_a, user_b):
        resume = await db.create_resume(content="# A", user_id=user_a["id"])
        assert await db.get_resume(resume["resume_id"], user_id=user_a["id"]) is not None
        assert await db.get_resume(resume["resume_id"], user_id=user_b["id"]) is None

    @pytest.mark.asyncio
    async def test_list_resumes_scoped_to_user(self, db, user_a, user_b):
        await db.create_resume(content="# A", user_id=user_a["id"])
        await db.create_resume(content="# B", user_id=user_b["id"])
        a_resumes = await db.list_resumes(user_id=user_a["id"])
        assert len(a_resumes) == 1

    @pytest.mark.asyncio
    async def test_update_resume_scoped_to_user(self, db, user_a, user_b):
        resume = await db.create_resume(content="# A", user_id=user_a["id"])
        updated = await db.update_resume(resume["resume_id"], user_id=user_a["id"], updates={"content": "# Updated"})
        assert updated["content"] == "# Updated"
        with pytest.raises(ValueError):
            await db.update_resume(resume["resume_id"], user_id=user_b["id"], updates={"content": "# Hack"})

    @pytest.mark.asyncio
    async def test_delete_resume_scoped_to_user(self, db, user_a, user_b):
        resume = await db.create_resume(content="# A", user_id=user_a["id"])
        assert await db.delete_resume(resume["resume_id"], user_id=user_b["id"]) is False
        assert await db.delete_resume(resume["resume_id"], user_id=user_a["id"]) is True

    @pytest.mark.asyncio
    async def test_get_master_resume_scoped(self, db, user_a, user_b):
        await db.create_resume(content="# A Master", user_id=user_a["id"], is_master=True)
        await db.create_resume(content="# B Master", user_id=user_b["id"], is_master=True)
        a_master = await db.get_master_resume(user_id=user_a["id"])
        assert "A Master" in a_master["content"]
        b_master = await db.get_master_resume(user_id=user_b["id"])
        assert "B Master" in b_master["content"]

    @pytest.mark.asyncio
    async def test_set_master_resume_scoped(self, db, user_a, user_b):
        r1 = await db.create_resume(content="# A1", user_id=user_a["id"], is_master=True)
        r2 = await db.create_resume(content="# A2", user_id=user_a["id"])
        rb = await db.create_resume(content="# B1", user_id=user_b["id"], is_master=True)
        assert await db.set_master_resume(r2["resume_id"], user_id=user_a["id"]) is True
        b_master = await db.get_master_resume(user_id=user_b["id"])
        assert b_master["resume_id"] == rb["resume_id"]
        assert b_master["is_master"] is True

    @pytest.mark.asyncio
    async def test_create_resume_atomic_master_scoped(self, db, user_a, user_b):
        r_a = await db.create_resume_atomic_master(content="# A", user_id=user_a["id"])
        assert r_a["is_master"] is True
        r_b = await db.create_resume_atomic_master(content="# B", user_id=user_b["id"])
        assert r_b["is_master"] is True


class TestJobScoping:
    @pytest.mark.asyncio
    async def test_create_job_with_user_id(self, db, user_a):
        job = await db.create_job(content="Job desc", user_id=user_a["id"])
        assert job["job_id"]

    @pytest.mark.asyncio
    async def test_get_job_scoped(self, db, user_a, user_b):
        job = await db.create_job(content="Job desc", user_id=user_a["id"])
        assert await db.get_job(job["job_id"], user_id=user_a["id"]) is not None
        assert await db.get_job(job["job_id"], user_id=user_b["id"]) is None

    @pytest.mark.asyncio
    async def test_update_job_scoped(self, db, user_a, user_b):
        job = await db.create_job(content="Job desc", user_id=user_a["id"])
        updated = await db.update_job(job["job_id"], user_id=user_a["id"], updates={"content": "New"})
        assert updated["content"] == "New"
        assert await db.update_job(job["job_id"], user_id=user_b["id"], updates={"content": "Hack"}) is None


class TestImprovementScoping:
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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


class TestStatsScoping:
    @pytest.mark.asyncio
    async def test_stats_scoped_to_user(self, db, user_a, user_b):
        await db.create_resume(content="# A", user_id=user_a["id"])
        await db.create_resume(content="# B1", user_id=user_b["id"])
        await db.create_resume(content="# B2", user_id=user_b["id"])
        stats_a = await db.get_stats(user_id=user_a["id"])
        assert stats_a["total_resumes"] == 1
        stats_b = await db.get_stats(user_id=user_b["id"])
        assert stats_b["total_resumes"] == 2

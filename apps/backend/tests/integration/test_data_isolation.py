"""Integration tests for multi-user data isolation."""

import pytest


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
        ("POST", "/api/v1/enrichment/analyze/fake"),
    ]

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    async def test_returns_401_without_token(self, client, method, path):
        resp = await client.request(method, path)
        assert resp.status_code == 401


class TestResumeIsolation:
    async def test_user_b_cannot_see_user_a_resume(self, client, test_db, auth_user_a, auth_user_b):
        user_a, token_a = auth_user_a
        _, token_b = auth_user_b
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        resume = await test_db.create_resume(content="# Secret", user_id=user_a["id"])
        rid = resume["resume_id"]

        resp = await client.get(f"/api/v1/resumes?resume_id={rid}", headers=headers_a)
        assert resp.status_code == 200

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

        still_exists = await test_db.get_resume(resume["resume_id"], user_a["id"])
        assert still_exists is not None


class TestJobIsolation:
    async def test_user_b_cannot_see_user_a_job(self, client, test_db, auth_user_a, auth_user_b):
        user_a, token_a = auth_user_a
        _, token_b = auth_user_b

        job = await test_db.create_job(content="JD text", user_id=user_a["id"])

        resp = await client.get(f"/api/v1/jobs/{job['job_id']}", headers={"Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200

        resp = await client.get(f"/api/v1/jobs/{job['job_id']}", headers={"Authorization": f"Bearer {token_b}"})
        assert resp.status_code == 404


class TestStatusScoping:
    async def test_status_returns_user_stats(self, client, test_db, auth_user_a, auth_user_b):
        user_a, token_a = auth_user_a
        _, token_b = auth_user_b

        await test_db.create_resume(content="# A", user_id=user_a["id"], is_master=True)

        resp = await client.get("/api/v1/status", headers={"Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200
        assert resp.json()["database_stats"]["total_resumes"] == 1
        assert resp.json()["has_master_resume"] is True

        resp = await client.get("/api/v1/status", headers={"Authorization": f"Bearer {token_b}"})
        assert resp.status_code == 200
        assert resp.json()["database_stats"]["total_resumes"] == 0
        assert resp.json()["has_master_resume"] is False

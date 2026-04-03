"""Shared test fixtures for Resume Matcher backend tests."""

import copy

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import create_access_token
from app.auth.keys import load_rsa_keys, reset_keys
from app.database import Database


# ---------------------------------------------------------------------------
# Database & HTTP client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def test_db():
    """Provide a clean in-memory SQLite database per test."""
    test_database = Database("sqlite+aiosqlite://")
    await test_database.init()
    yield test_database
    await test_database.close()


@pytest.fixture
def jwt_secret(monkeypatch) -> str:
    """Patch JWT secret for tests -- shared by client and auth fixtures."""
    secret = "test-secret-for-tests"
    monkeypatch.setattr("app.config.settings.jwt_secret_key", secret)
    return secret


@pytest.fixture
async def client(test_db, jwt_secret, rsa_keys, monkeypatch):
    """Async HTTP client with test database and JWT secret injected.

    Patches the ``db`` attribute in every module that imports it so that
    the routers, services **and** the lifespan all talk to the in-memory
    test database.
    """
    import app.database as db_module
    import app.auth.dependencies as auth_deps_mod
    import app.routers.auth as auth_mod
    import app.routers.oauth as oauth_mod
    import app.routers.google_oauth as google_oauth_mod
    import app.routers.resumes as resumes_mod
    import app.routers.jobs as jobs_mod
    import app.routers.health as health_mod
    import app.routers.config as config_mod
    import app.routers.enrichment as enrichment_mod
    import app.main as main_mod

    for mod in (db_module, auth_deps_mod, auth_mod, oauth_mod, google_oauth_mod, resumes_mod, jobs_mod, health_mod, config_mod, enrichment_mod, main_mod):
        if hasattr(mod, "db"):
            monkeypatch.setattr(mod, "db", test_db)

    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Sample resume data — full ResumeData-compatible dict
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_resume() -> dict:
    """A realistic resume dict matching the ResumeData schema."""
    return {
        "personalInfo": {
            "name": "Jane Doe",
            "title": "Senior Backend Engineer",
            "email": "jane@example.com",
            "phone": "+1-555-0100",
            "location": "San Francisco, CA",
            "website": "https://janedoe.dev",
            "linkedin": "linkedin.com/in/janedoe",
            "github": "github.com/janedoe",
        },
        "summary": "Backend engineer with 6 years of experience building scalable Python APIs and microservices.",
        "workExperience": [
            {
                "id": 1,
                "title": "Senior Backend Engineer",
                "company": "Acme Corp",
                "location": "San Francisco, CA",
                "years": "Jan 2021 - Present",
                "description": [
                    "Built REST APIs serving 50K requests/day using Python and FastAPI",
                    "Led migration from monolith to microservices architecture",
                    "Mentored 3 junior developers on backend best practices",
                ],
            },
            {
                "id": 2,
                "title": "Software Engineer",
                "company": "StartupCo",
                "location": "New York, NY",
                "years": "Jun 2018 - Dec 2020",
                "description": [
                    "Developed payment processing system handling $2M monthly",
                    "Wrote unit and integration tests improving coverage from 40% to 85%",
                ],
            },
        ],
        "education": [
            {
                "id": 1,
                "institution": "MIT",
                "degree": "B.S. Computer Science",
                "years": "2014 - 2018",
                "description": "Graduated with honors, Dean's List",
            }
        ],
        "personalProjects": [
            {
                "id": 1,
                "name": "OpenAPI Generator",
                "role": "Creator & Maintainer",
                "years": "Mar 2021 - Present",
                "description": [
                    "CLI tool generating API clients from OpenAPI specs",
                    "500+ GitHub stars, used by 30+ companies",
                ],
            }
        ],
        "additional": {
            "technicalSkills": ["Python", "FastAPI", "Docker", "AWS", "PostgreSQL", "Redis"],
            "languages": ["English (Native)", "Spanish (Conversational)"],
            "certificationsTraining": ["AWS Solutions Architect Associate"],
            "awards": ["Employee of the Year 2022"],
        },
        "customSections": {},
        "sectionMeta": [],
    }


@pytest.fixture
def sample_resume_copy(sample_resume) -> dict:
    """Deep copy of sample_resume for mutation-safe tests."""
    return copy.deepcopy(sample_resume)


# ---------------------------------------------------------------------------
# Job-related fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_job_keywords() -> dict:
    """Extracted job keywords matching the LLM output format."""
    return {
        "required_skills": ["Python", "FastAPI", "Docker", "Kubernetes"],
        "preferred_skills": ["AWS", "Terraform", "GraphQL"],
        "experience_requirements": ["5+ years backend development"],
        "education_requirements": ["Bachelor's in CS or equivalent"],
        "key_responsibilities": [
            "Design and build scalable APIs",
            "Lead technical architecture decisions",
        ],
        "keywords": ["microservices", "CI/CD", "agile", "REST API"],
        "experience_years": 5,
        "seniority_level": "senior",
    }


@pytest.fixture
def sample_job_description() -> str:
    """A realistic job description text."""
    return (
        "Senior Backend Engineer at TechCorp\n\n"
        "We are looking for a Senior Backend Engineer to join our platform team. "
        "You will design and build scalable APIs using Python and FastAPI. "
        "Experience with Docker, Kubernetes, and AWS is required. "
        "Terraform and GraphQL experience is a plus.\n\n"
        "Requirements:\n"
        "- 5+ years backend development experience\n"
        "- Strong Python skills with FastAPI or similar frameworks\n"
        "- Experience with microservices architecture\n"
        "- Familiarity with CI/CD pipelines and agile methodologies\n"
        "- Bachelor's degree in CS or equivalent\n"
    )


# ---------------------------------------------------------------------------
# Master resume — used for alignment validation
# ---------------------------------------------------------------------------

@pytest.fixture
def master_resume(sample_resume) -> dict:
    """Master resume (source of truth) — same as sample_resume by default."""
    return copy.deepcopy(sample_resume)


# ---------------------------------------------------------------------------
# ResumeChange fixtures for diff-based tests
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_changes():
    """A set of ResumeChange dicts covering all action types."""
    from app.schemas.models import ResumeChange

    return [
        ResumeChange(
            path="summary",
            action="replace",
            original="Backend engineer with 6 years of experience building scalable Python APIs and microservices.",
            value="Senior backend engineer with 6 years building scalable Python APIs, microservices, and cloud infrastructure on AWS.",
            reason="Added cloud/AWS keywords from JD",
        ),
        ResumeChange(
            path="workExperience[0].description[0]",
            action="replace",
            original="Built REST APIs serving 50K requests/day using Python and FastAPI",
            value="Designed and built REST APIs serving 50K requests/day using Python, FastAPI, and Docker",
            reason="Added Docker keyword from JD",
        ),
        ResumeChange(
            path="workExperience[0].description",
            action="append",
            original=None,
            value="Implemented CI/CD pipelines with GitHub Actions reducing deploy time by 40%",
            reason="Added CI/CD keyword from JD",
        ),
        ResumeChange(
            path="additional.technicalSkills",
            action="reorder",
            original=None,
            value=["Python", "FastAPI", "Docker", "AWS", "PostgreSQL", "Redis"],
            reason="Already in good order, no change needed",
        ),
    ]


# ---------------------------------------------------------------------------
# RSA key fixture for RS256 JWT signing
# ---------------------------------------------------------------------------

@pytest.fixture
def rsa_keys():
    """Generate and load test RSA keys for JWT signing."""
    from joserfc.jwk import RSAKey
    reset_keys()
    key = RSAKey.generate_key(2048)
    load_rsa_keys(pem_data=key.as_pem(private=True).decode("utf-8"))
    yield
    reset_keys()


# ---------------------------------------------------------------------------
# Auth fixtures — user creation + JWT tokens
# ---------------------------------------------------------------------------

@pytest.fixture
async def auth_user_a(test_db, rsa_keys):
    """Create user A and return (user_dict, bearer_token)."""
    user = await test_db.create_user(email="alice@test.com", hashed_password="hash_a", display_name="Alice")
    token = create_access_token(user_id=user["id"], email=user["email"])
    return user, token


@pytest.fixture
async def auth_user_b(test_db, rsa_keys):
    """Create user B and return (user_dict, bearer_token)."""
    user = await test_db.create_user(email="bob@test.com", hashed_password="hash_b", display_name="Bob")
    token = create_access_token(user_id=user["id"], email=user["email"])
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

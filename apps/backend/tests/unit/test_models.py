"""Test SQLAlchemy ORM models."""

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

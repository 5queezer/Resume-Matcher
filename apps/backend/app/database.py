"""Async SQLAlchemy database layer for Resume Matcher."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base, Improvement, Job, Resume, User

logger = logging.getLogger(__name__)


class Database:
    """Async database wrapper with dict-based interface for routers."""

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

    @staticmethod
    def _user_to_dict(u: User, include_password: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "updated_at": u.updated_at.isoformat() if u.updated_at else None,
        }
        if include_password:
            d["hashed_password"] = u.hashed_password
        return d

    # -- User operations -------------------------------------------------------

    async def create_user(
        self,
        email: str,
        hashed_password: str,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        user = User(
            id=str(uuid4()),
            email=email,
            hashed_password=hashed_password,
            display_name=display_name,
        )
        async with self._session() as session:
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return self._user_to_dict(user)

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(select(User).where(User.email == email))
            row = result.scalar_one_or_none()
            return self._user_to_dict(row, include_password=True) if row else None

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            row = result.scalar_one_or_none()
            return self._user_to_dict(row) if row else None

    # -- Resume operations ---------------------------------------------------

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
        async with self._master_resume_lock:
            current_master = await self.get_master_resume()
            is_master = current_master is None
            if current_master and current_master.get("processing_status") in ("failed", "processing"):
                await self.update_resume(current_master["resume_id"], {"is_master": False})
                is_master = True
            return await self.create_resume(
                content=content, content_type=content_type, filename=filename,
                is_master=is_master, processed_data=processed_data,
                processing_status=processing_status, cover_letter=cover_letter,
                outreach_message=outreach_message, original_markdown=original_markdown,
            )

    async def get_resume(self, resume_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(select(Resume).where(Resume.resume_id == resume_id))
            row = result.scalar_one_or_none()
            return self._resume_to_dict(row) if row else None

    async def get_master_resume(self) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(select(Resume).where(Resume.is_master == True))
            row = result.scalar_one_or_none()
            return self._resume_to_dict(row) if row else None

    async def update_resume(self, resume_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        updates["updated_at"] = datetime.now(timezone.utc)
        async with self._session() as session:
            result = await session.execute(
                update(Resume).where(Resume.resume_id == resume_id).values(**updates)
            )
            if result.rowcount == 0:
                raise ValueError(f"Resume not found: {resume_id}")
            await session.commit()
        return await self.get_resume(resume_id)

    async def delete_resume(self, resume_id: str) -> bool:
        async with self._session() as session:
            result = await session.execute(delete(Resume).where(Resume.resume_id == resume_id))
            await session.commit()
            return result.rowcount > 0

    async def list_resumes(self) -> list[dict[str, Any]]:
        async with self._session() as session:
            result = await session.execute(select(Resume))
            return [self._resume_to_dict(r) for r in result.scalars().all()]

    async def set_master_resume(self, resume_id: str) -> bool:
        async with self._session() as session:
            target = await session.execute(select(Resume).where(Resume.resume_id == resume_id))
            if not target.scalar_one_or_none():
                logger.warning("Cannot set master: resume %s not found", resume_id)
                return False
            await session.execute(update(Resume).where(Resume.is_master == True).values(is_master=False))
            await session.execute(update(Resume).where(Resume.resume_id == resume_id).values(is_master=True))
            await session.commit()
            return True

    # -- Job operations ------------------------------------------------------

    async def create_job(self, content: str, resume_id: str | None = None) -> dict[str, Any]:
        job = Job(job_id=str(uuid4()), content=content, resume_id=resume_id)
        async with self._session() as session:
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return self._job_to_dict(job)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(select(Job).where(Job.job_id == job_id))
            row = result.scalar_one_or_none()
            return self._job_to_dict(row) if row else None

    async def update_job(self, job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(update(Job).where(Job.job_id == job_id).values(**updates))
            if result.rowcount == 0:
                return None
            await session.commit()
        return await self.get_job(job_id)

    # -- Improvement operations ----------------------------------------------

    async def create_improvement(
        self, original_resume_id: str, tailored_resume_id: str,
        job_id: str, improvements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        imp = Improvement(
            request_id=str(uuid4()), original_resume_id=original_resume_id,
            tailored_resume_id=tailored_resume_id, job_id=job_id, improvements=improvements,
        )
        async with self._session() as session:
            session.add(imp)
            await session.commit()
            await session.refresh(imp)
            return self._improvement_to_dict(imp)

    async def get_improvement_by_tailored_resume(self, tailored_resume_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            result = await session.execute(
                select(Improvement).where(Improvement.tailored_resume_id == tailored_resume_id)
            )
            row = result.scalar_one_or_none()
            return self._improvement_to_dict(row) if row else None

    # -- Stats & admin -------------------------------------------------------

    async def get_stats(self) -> dict[str, Any]:
        async with self._session() as session:
            resume_count = (await session.execute(select(func.count()).select_from(Resume))).scalar() or 0
            job_count = (await session.execute(select(func.count()).select_from(Job))).scalar() or 0
            improvement_count = (await session.execute(select(func.count()).select_from(Improvement))).scalar() or 0
            master = await session.execute(select(Resume).where(Resume.is_master == True))
            return {
                "total_resumes": resume_count,
                "total_jobs": job_count,
                "total_improvements": improvement_count,
                "has_master_resume": master.scalar_one_or_none() is not None,
            }

    async def reset_database(self) -> None:
        async with self._session() as session:
            await session.execute(delete(Improvement))
            await session.execute(delete(Job))
            await session.execute(delete(Resume))
            await session.commit()
        uploads_dir = settings.data_dir / "uploads"
        if uploads_dir.exists():
            import shutil
            shutil.rmtree(uploads_dir)
            uploads_dir.mkdir(parents=True, exist_ok=True)


# Global database instance -- initialized in main.py lifespan
db = Database()

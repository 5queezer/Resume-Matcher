"""API routers."""

from app.routers.auth import router as auth_router
from app.routers.config import router as config_router
from app.routers.enrichment import router as enrichment_router
from app.routers.google_oauth import router as google_oauth_router
from app.routers.health import router as health_router
from app.routers.jobs import router as jobs_router
from app.routers.oauth import router as oauth_router
from app.routers.resumes import router as resumes_router

__all__ = [
    "auth_router",
    "google_oauth_router",
    "oauth_router",
    "resumes_router",
    "jobs_router",
    "config_router",
    "health_router",
    "enrichment_router",
]

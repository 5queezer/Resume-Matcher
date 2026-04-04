"""FastAPI application entry point."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

# Fix for Windows: Use ProactorEventLoop for subprocess support (Playwright)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.auth.keys import get_jwks, load_rsa_keys
from app.config import settings
from app.database import db
from app.pdf import close_pdf_renderer, init_pdf_renderer
from app.routers import auth_router, config_router, enrichment_router, google_oauth_router, health_router, jobs_router, mcp_router, oauth_router, resumes_router


def _configure_application_logging() -> None:
    """Set application log level from configuration."""
    numeric_level = getattr(logging, settings.log_level, logging.INFO)
    logging.getLogger("app").setLevel(numeric_level)


_configure_application_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await db.init()
    load_rsa_keys(
        pem_data=settings.rsa_private_key_pem or None,
        key_file=str(settings.effective_rsa_key_file),
    )
    yield
    try:
        await close_pdf_renderer()
    except Exception as e:
        logger.error(f"Error closing PDF renderer: {e}")
    try:
        await db.close()
    except Exception as e:
        logger.error(f"Error closing database: {e}")


app = FastAPI(
    title="Resume Matcher API",
    description="AI-powered resume tailoring for job descriptions",
    version=__version__,
    lifespan=lifespan,
)

# CORS middleware - origins configurable via CORS_ORIGINS env var
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.effective_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(oauth_router, prefix="/api/v1")
app.include_router(google_oauth_router, prefix="/api/v1")
app.include_router(health_router, prefix="/api/v1")
app.include_router(config_router, prefix="/api/v1")
app.include_router(resumes_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(enrichment_router, prefix="/api/v1")
app.include_router(mcp_router)  # No prefix — mounted at /mcp directly


@app.get("/")
async def root() -> dict:
    """Root endpoint."""
    return {
        "name": "Resume Matcher API",
        "version": __version__,
        "docs": "/docs",
    }


@app.get("/.well-known/oauth-authorization-server")
async def oauth_server_metadata() -> dict:
    """RFC 8414 OAuth 2.1 Authorization Server Metadata."""
    base = settings.frontend_base_url.rstrip("/")
    api_base = f"{base}/api/v1"
    return {
        "issuer": base,
        "authorization_endpoint": f"{api_base}/oauth/authorize",
        "token_endpoint": f"{api_base}/oauth/token",
        "revocation_endpoint": f"{api_base}/oauth/revoke",
        "registration_endpoint": f"{api_base}/oauth/register",
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["openid", "profile", "email"],
    }


@app.get("/.well-known/jwks.json")
async def jwks_endpoint() -> dict:
    """RFC 7517 JSON Web Key Set — public key for token verification."""
    return get_jwks()


@app.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata() -> dict:
    """RFC 9728 OAuth 2.0 Protected Resource Metadata."""
    base = settings.frontend_base_url.rstrip("/")
    return {
        "resource": base,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "resource_name": "Resume Matcher",
    }


# -- claude.ai compatibility proxies ------------------------------------------
# claude.ai web appends /authorize, /token, /register to the server root,
# ignoring the URLs in AS metadata. These thin proxies handle that quirk.

from fastapi.responses import RedirectResponse as _RedirectResponse

from app.schemas.auth import (
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    TokenRequest,
    TokenResponse,
)


@app.get("/authorize")
async def root_authorize(request: Request) -> _RedirectResponse:
    """Redirect browser authorize to frontend login with OAuth params."""
    qs = str(request.query_params)
    target = f"{settings.frontend_origin}/login"
    if qs:
        target = f"{target}?{qs}"
    return _RedirectResponse(url=target, status_code=302)


@app.post("/register", status_code=201, response_model=ClientRegistrationResponse)
async def root_register(body: ClientRegistrationRequest) -> ClientRegistrationResponse:
    """Proxy POST /register to the DCR endpoint."""
    from app.routers.oauth import register_client
    return await register_client(body)


@app.post("/token", response_model=TokenResponse)
async def root_token(body: TokenRequest, request: Request, response: Response) -> TokenResponse:
    """Proxy POST /token to the token endpoint."""
    from app.routers.oauth import token as token_handler
    return await token_handler(body, request, response)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )

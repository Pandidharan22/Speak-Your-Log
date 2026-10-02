"""FastAPI application factory.

Run locally:  uvicorn app.main:create_app --factory --reload
(A factory, not a module-level `app`, so importing this module never reads the environment.)
"""

import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.config import Settings, get_settings
from app.crypto import TokenVault
from app.db import Database
from app.livekit_service import LiveKitService
from app.proof import ProofClient
from app.ratelimit import RateLimiter
from app.routers import internal as internal_router
from app.routers import interviews as interviews_router
from app.routers import proof_token as proof_token_router
from app.routers import session as session_router
from app.static import mount_web_app

# Microphone is required by the product; everything else is denied.
_PERMISSIONS_POLICY = "microphone=(self), camera=(), geolocation=(), payment=()"
_NO_STORE_PATHS = ("/api/", "/internal/", "/healthz", "/readyz")
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MAX_API_BODY_BYTES = 16_384  # the largest legitimate body is one token (~1 KB)


_FIELD_RE = re.compile(r"^[a-z][a-z_]{0,31}$")


def configure_logging() -> None:
    """Make our own INFO events (e.g. `proof_token_connected`) visible under uvicorn.

    Uvicorn only configures its own loggers, so without this every `log.info(...)` in the app is
    silently dropped and production has no audit trail. Idempotent.
    """
    logger = logging.getLogger("app")
    logger.setLevel(logging.INFO)
    if not logging.getLogger().handlers and not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)


def _safe_field(loc: tuple) -> str:
    """Name the offending field, but never reflect attacker-chosen text (e.g. odd JSON keys)."""
    name = str(loc[1]) if len(loc) > 1 else "body"
    return name if _FIELD_RE.fullmatch(name) else "body"


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    proof: ProofClient | None = None,
    vault: TokenVault | None = None,
    livekit: LiveKitService | None = None,
) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()
    production = settings.app_env == "production"
    db = db or Database(
        settings.database_url.get_secret_value(), max_size=settings.db_pool_max_size
    )
    proof = proof or ProofClient(settings.proof_mcp_url)
    vault = vault or TokenVault.from_settings(settings)
    livekit = livekit or LiveKitService(
        settings.livekit_url,
        settings.livekit_api_key.get_secret_value(),
        settings.livekit_api_secret.get_secret_value(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        db.open()
        try:
            yield
        finally:
            db.close()
            proof.close()

    app = FastAPI(
        lifespan=lifespan,
        title="Speak Your Log API",
        version=__version__,
        # No interactive docs/schema on the public production host.
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.db = db
    app.state.proof = proof
    app.state.vault = vault
    app.state.livekit = livekit
    # Starting an interview consumes LiveKit minutes: at most 5 per user per 10 minutes.
    app.state.interview_limiter = RateLimiter(limit=5, window_seconds=600)
    # Each token attempt calls Proof, so it is limited per IP and per user (SRS NFR-2).
    app.state.token_limiter = RateLimiter(limit=10, window_seconds=60)
    # New anonymous users per client IP. (Behind Render, run uvicorn with --proxy-headers so
    # request.client is the real client, not the proxy.)
    app.state.session_create_limiter = RateLimiter(limit=10, window_seconds=60)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 body echoes the offending INPUT back. For a request that carries
        # a secret that would send the token straight back to the caller (and into proxy logs),
        # so report only which fields were wrong.
        fields = sorted({_safe_field(e["loc"]) for e in exc.errors()})
        return JSONResponse({"detail": "invalid_request", "fields": fields}, status_code=422)

    @app.middleware("http")
    async def limit_body_size(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Bounds memory on a small free instance. (Checks the declared length; the hosting proxy
        # is the backstop for chunked uploads that omit it.)
        if request.url.path.startswith(("/api/", "/internal/")):
            try:
                declared = int(request.headers.get("content-length") or 0)
            except ValueError:
                return JSONResponse({"detail": "bad_content_length"}, status_code=400)
            if declared > _MAX_API_BODY_BYTES:
                return JSONResponse({"detail": "body_too_large"}, status_code=413)
        return await call_next(request)

    # Registered BEFORE the security-headers middleware so that it sits inside it and its 403s
    # still get the security headers (the last-added middleware is the outermost).
    @app.middleware("http")
    async def require_same_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # CSRF defence in depth (the cookie is also SameSite=Lax): browser requests that change
        # state must come from our own origin. /internal/* is called by the agent with a signed
        # token, not a cookie, so it is deliberately outside this rule.
        if (
            request.method in _UNSAFE_METHODS
            and request.url.path.startswith("/api/")
            and request.headers.get("origin") not in settings.allowed_origins
        ):
            return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith(_NO_STORE_PATHS):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        # Liveness only: no DB or upstream calls, so the keep-warm ping is cheap and
        # never loads the shared database.
        return {"status": "ok"}

    app.include_router(session_router.router)
    app.include_router(proof_token_router.router)
    app.include_router(interviews_router.router)
    app.include_router(internal_router.router)

    @app.get("/readyz", include_in_schema=False)
    def readyz(response: Response) -> dict[str, str]:
        # Readiness = can we reach the database. Plain `def` so it runs in the threadpool
        # (the pool is synchronous). Never returns error details.
        if db.ping():
            return {"status": "ready"}
        response.status_code = 503
        return {"status": "unavailable"}

    if settings.web_dist_dir is not None:
        mount_web_app(app, settings.web_dist_dir, settings.livekit_url)  # last: API routes win

    return app

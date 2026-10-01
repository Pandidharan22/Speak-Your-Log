"""FastAPI application factory.

Run locally:  uvicorn app.main:create_app --factory --reload
(A factory, not a module-level `app`, so importing this module never reads the environment.)
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app import __version__
from app.config import Settings, get_settings
from app.db import Database

# Microphone is required by the product; everything else is denied.
_PERMISSIONS_POLICY = "microphone=(self), camera=(), geolocation=(), payment=()"
_NO_STORE_PATHS = ("/api/", "/healthz", "/readyz")


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    production = settings.app_env == "production"
    db = db or Database(
        settings.database_url.get_secret_value(), max_size=settings.db_pool_max_size
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        db.open()
        try:
            yield
        finally:
            db.close()

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

    @app.get("/readyz", include_in_schema=False)
    def readyz(response: Response) -> dict[str, str]:
        # Readiness = can we reach the database. Plain `def` so it runs in the threadpool
        # (the pool is synchronous). Never returns error details.
        if db.ping():
            return {"status": "ready"}
        response.status_code = 503
        return {"status": "unavailable"}

    return app

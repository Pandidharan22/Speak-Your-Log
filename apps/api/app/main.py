"""FastAPI application factory.

Run locally:  uvicorn app.main:create_app --factory --reload
(A factory, not a module-level `app`, so importing this module never reads the environment.)
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app import __version__
from app.config import Settings, get_settings

# Microphone is required by the product; everything else is denied.
_PERMISSIONS_POLICY = "microphone=(self), camera=(), geolocation=(), payment=()"
_NO_STORE_PATHS = ("/api/", "/healthz")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    production = settings.app_env == "production"

    app = FastAPI(
        title="Speak Your Log API",
        version=__version__,
        # No interactive docs/schema on the public production host.
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = settings

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

    return app

"""Serves the built web app from the same service as the API (one origin: the session cookie is
first-party, there is no CORS, and the Origin check in main.py is exact).

Only a Content-Security-Policy that names exactly what the page needs is sent with the HTML, so a
script injected into the page (there is no such path today) would still not run or phone home.
"""

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

# Paths owned by the API. An unknown one is a JSON 404, never the single-page app.
_API_PREFIXES = ("api/", "internal/", "docs", "openapi.json")
_IMMUTABLE = "public, max-age=31536000, immutable"  # Vite names bundles by content hash


def content_security_policy(livekit_url: str) -> str:
    """The CSP for the HTML page. `livekit_url` is the wss:// signalling address; the browser also
    talks to the same host over https (region discovery, token validation)."""
    host = urlsplit(livekit_url).netloc
    connect = f"'self' wss://{host} https://{host}"
    return "; ".join(
        [
            "default-src 'none'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            f"connect-src {connect}",
            "media-src 'self' blob:",
            "worker-src 'self' blob:",
            "font-src 'self'",
            "manifest-src 'self'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "object-src 'none'",
        ]
    )


def mount_web_app(app: FastAPI, dist: Path, livekit_url: str) -> None:
    """Serve `dist` and fall back to index.html for client-side routes. Call LAST: the API's own
    routes must be registered first so they take precedence over the catch-all."""
    root = dist.resolve()
    index = root / "index.html"
    csp = content_security_policy(livekit_url)

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def serve(path: str) -> FileResponse:
        if path.startswith(_API_PREFIXES):
            raise HTTPException(status_code=404, detail="not_found")
        try:
            candidate = (root / path).resolve()
            servable = path != "" and candidate.is_file() and candidate.is_relative_to(root)
        except (ValueError, OSError):  # e.g. an embedded NUL byte in the URL
            servable = False
        # resolve() collapses "..": anything that escapes the dist directory is not served.
        if servable and candidate != index:
            cache = _IMMUTABLE if candidate.parent.name == "assets" else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(
            index, headers={"Cache-Control": "no-cache", "Content-Security-Policy": csp}
        )

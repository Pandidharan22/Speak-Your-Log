"""Device-session identity.

A "user" is a browser/device. The cookie holds a random 256-bit token; the database stores only
HMAC-SHA256(token, SESSION_HMAC_KEY), so a leaked database cannot be replayed as a login, and the
cookie is never readable by page JavaScript (HttpOnly). See docs/adr/002.
"""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

import psycopg
from fastapi import Depends, HTTPException, Request, Response

from app import repo
from app.config import Settings
from app.db import Database

SESSION_TTL = timedelta(days=90)
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits of entropy


def hash_token(token: str, key: bytes) -> bytes:
    return hmac.new(key, token.encode("ascii"), hashlib.sha256).digest()  # 32 bytes


def read_token(request: Request, settings: Settings) -> str | None:
    """The raw cookie value, or None if absent or not shaped like one of our tokens."""
    raw = request.cookies.get(settings.cookie_name)
    return raw if raw and _TOKEN_RE.fullmatch(raw) else None


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,  # invisible to page JavaScript
        secure=settings.cookie_secure,
        samesite="lax",  # not sent on cross-site POSTs
        path="/",
    )


def issue_session(conn: psycopg.Connection, settings: Settings) -> tuple[repo.User, str]:
    """Create a fresh anonymous user and a session for them. Returns (user, raw cookie token)."""
    repo.purge_stale(conn)  # keeps the shared DB tidy; cheap and indexed
    user = repo.create_user(conn)
    token = new_token()
    repo.create_device_session(
        conn,
        user.id,
        hash_token(token, settings.session_hmac_key_bytes),
        datetime.now(UTC) + SESSION_TTL,
    )
    return user, token


def resolve_user(
    conn: psycopg.Connection, request: Request, settings: Settings
) -> repo.User | None:
    token = read_token(request, settings)
    if token is None:
        return None
    user = repo.get_user_by_session(conn, hash_token(token, settings.session_hmac_key_bytes))
    if user is not None:
        repo.touch_user(conn, user.id)
    return user


# ---- FastAPI dependencies ---------------------------------------------------------------------


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def current_user(
    request: Request,
    db: Database = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
) -> repo.User:
    """Require an existing session. Routes that must not create users use this."""
    with db.connection() as conn:
        user = resolve_user(conn, request, settings)
    if user is None:
        raise HTTPException(status_code=401, detail="no_session")
    return user

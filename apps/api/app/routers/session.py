from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app import repo
from app.auth import (
    get_db,
    get_settings_dep,
    issue_session,
    resolve_user,
    set_session_cookie,
)
from app.config import Settings
from app.db import Database
from app.schemas import SessionStatus

router = APIRouter(prefix="/api")


@router.post("/session")
def bootstrap_session(
    request: Request,
    response: Response,
    db: Database = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
) -> SessionStatus:
    """Idempotent: recognise this device, or silently create an anonymous user for it."""
    with db.connection() as conn:
        user = resolve_user(conn, request, settings)
        if user is None:
            # Only *creating* users is rate limited; returning visitors never hit this.
            limiter = request.app.state.session_create_limiter
            ip = request.client.host if request.client else "unknown"
            global_limiter = request.app.state.session_create_global_limiter
            if not (limiter.allow(ip) and global_limiter.allow("all")):
                raise HTTPException(
                    status_code=429,
                    detail="too_many_new_sessions",
                    headers={"Retry-After": str(limiter.retry_after(ip))},
                )
            user, token = issue_session(conn, settings)
            set_session_cookie(response, token, settings)
        credential = repo.get_proof_credential(conn, user.id)
    return SessionStatus(
        connected=credential is not None,
        last4=credential.last4 if credential else None,
    )

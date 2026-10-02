"""Start an interview: create the record and the LiveKit room, dispatch the agent, hand the
browser a token that can join that one room.

Order matters: the database transaction (checks + insert) is committed and its connection released
BEFORE the slow LiveKit calls, so a few slow requests cannot starve the 3-connection pool.
"""

import json
import logging
import secrets
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import repo
from app.auth import current_user, get_db, get_settings_dep
from app.config import Settings
from app.db import Database
from app.jobtoken import sign_job_token
from app.livekit_service import LiveKitUnavailable
from app.payload import IncompleteDraft, build_payload

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_ACTIVE_WINDOW_MINUTES = 10


class InterviewStart(BaseModel):
    interview_id: UUID
    livekit_url: str  # public wss:// URL of the LiveKit project
    token: str  # joins ONE room, microphone only, 10 minutes. Never the Proof token.


@router.post("/interviews")
def start_interview(
    request: Request,
    user: repo.User = Depends(current_user),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
) -> InterviewStart:
    limiter = request.app.state.interview_limiter
    if not limiter.allow(f"user:{user.id}"):
        raise HTTPException(
            status_code=429,
            detail="too_many_interviews",
            headers={"Retry-After": str(limiter.retry_after(f"user:{user.id}"))},
        )

    room_name = f"syl-{uuid4().hex}"
    with db.connection() as conn:
        if repo.get_proof_credential(conn, user.id) is None:
            # Never let someone spend two minutes talking to an interview that cannot be posted.
            raise HTTPException(status_code=409, detail="token_not_connected")
        if repo.count_recent_active_interviews(conn, _ACTIVE_WINDOW_MINUTES) >= (
            settings.max_active_interviews
        ):
            # LiveKit's free plan allows 5 concurrent agent sessions; refuse politely before that.
            raise HTTPException(
                status_code=503, detail="busy_try_shortly", headers={"Retry-After": "30"}
            )
        interview = repo.create_interview(conn, user.id, room_name)
    # ---- the connection is released here; nothing below holds the database ----

    metadata = json.dumps(
        {
            "interview_id": str(interview.id),
            "job_token": sign_job_token(settings.agent_job_secret_bytes, interview.id),
            "api_base_url": settings.public_base_url,
        }
    )
    livekit = request.app.state.livekit
    try:
        livekit.start_interview(room_name, metadata)
    except LiveKitUnavailable:
        with db.connection() as conn:
            repo.mark_interview_failed(conn, interview.id)
        raise HTTPException(status_code=502, detail="voice_service_unavailable") from None

    log.info("interview_started interview=%s user=%s", interview.id, user.id)
    return InterviewStart(
        interview_id=interview.id,
        livekit_url=settings.livekit_url,
        token=livekit.student_token(room_name, f"student-{secrets.token_hex(4)}"),
    )


class PreviewOut(BaseModel):
    content: str
    why: str


class InterviewStatus(BaseModel):
    state: str
    preview: PreviewOut | None  # the exact text that will be / was posted, once it is complete
    proof_url: str | None


_SHOW_PREVIEW = {"confirming", "posting", "posted", "post_unknown"}


@router.get("/interviews/{interview_id}")
def interview_status(
    interview_id: UUID,
    user: repo.User = Depends(current_user),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> InterviewStatus:
    """What the page needs while the interview runs: state, the read-back text, the result link.
    Owner only; someone else's interview looks exactly like one that does not exist."""
    with db.connection() as conn:
        row = repo.get_interview(conn, interview_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="not_found")
    preview = None
    if row.state in _SHOW_PREVIEW:
        try:
            payload = build_payload(row.draft.get("answers", {}))
            preview = PreviewOut(content=payload.content, why=payload.why)
        except IncompleteDraft:
            preview = None
    return InterviewStatus(state=row.state, preview=preview, proof_url=row.proof_url)

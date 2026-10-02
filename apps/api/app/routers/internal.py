"""Endpoints the voice agent calls (never the browser). Authenticated by the per-interview job token
(app/jobtoken.py), which authorises exactly ONE interview. There is deliberately no cookie, no
Origin check and no CORS here: these are server-to-server calls.

What the agent can do: report progress, store the student's verbatim answers, fetch the exact text
to read back, and say "the student confirmed". What it cannot do: supply post text, choose the
verb, or move an interview into `posting`/`posted` itself. Only the gateway does that.
"""

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app import repo
from app.auth import get_db, get_settings_dep
from app.config import Settings
from app.db import Database
from app.gateway import post_interview
from app.jobtoken import InvalidJobToken, verify_job_token
from app.payload import ANSWER_KEYS, MAX_ANSWER_CHARS, IncompleteDraft, build_payload, is_complete
from app.states import InterviewState, can_transition

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/interviews")

_PREVIEW_STATES = {"interviewing", "confirming", "posting", "posted", "post_unknown"}


def agent_auth(
    interview_id: UUID,
    request: Request,
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
) -> UUID:
    """Require a valid job token for THIS interview. Every failure looks the same."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not token:
            raise InvalidJobToken
        authorised = verify_job_token(settings.agent_job_secret_bytes, token.strip())
        if authorised != interview_id:  # a token for another interview is worthless here
            raise InvalidJobToken
    except InvalidJobToken:
        raise HTTPException(
            status_code=401, detail="invalid_job_token", headers={"WWW-Authenticate": "Bearer"}
        ) from None
    return interview_id


class StateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The agent may NOT request posting/posted/post_unknown: only the gateway moves those.
    state: Literal["interviewing", "confirming", "cancelled", "failed"]


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)


class PostIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_text: str = Field(max_length=600)


@router.get("/{interview_id}")
def status(
    interview_id: UUID = Depends(agent_auth),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> dict:
    """Where the interview is. The agent uses it to learn what happened when a reply to the post
    call was lost, so it never has to guess "not posted". Only the state, nothing else."""
    with db.connection() as conn:
        row = repo.get_interview(conn, interview_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return {"state": row.state}


@router.post("/{interview_id}/state")
def set_state(
    body: StateIn,
    interview_id: UUID = Depends(agent_auth),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> dict:
    with db.connection() as conn:
        row = repo.get_interview(conn, interview_id)
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        current, target = row.state, body.state
        if current == target:
            return {"state": current}  # idempotent: the agent may repeat itself
        if target == "confirming" and not is_complete(row.draft.get("answers", {})):
            raise HTTPException(status_code=409, detail="incomplete_draft")
        if not can_transition(InterviewState(current), InterviewState(target)):
            raise HTTPException(status_code=409, detail="illegal_transition")
        if not repo.transition_interview(conn, interview_id, current, target):
            raise HTTPException(status_code=409, detail="illegal_transition")  # lost a race
        if current == "confirming" and target == "interviewing":
            repo.clear_draft(conn, interview_id)  # "edit: start over" really starts over
    log.info("interview_state interview=%s %s->%s", interview_id, current, target)
    return {"state": target}


@router.put("/{interview_id}/answers/{key}")
def store_answer(
    key: Literal["q1", "q2", "q3", "followup_q", "followup_a"],
    body: AnswerIn,
    interview_id: UUID = Depends(agent_auth),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> dict:
    assert key in ANSWER_KEYS  # noqa: S101 - the Literal above and ANSWER_KEYS must agree
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="blank_answer")
    with db.connection() as conn:
        if not repo.set_draft_answer(conn, interview_id, key, body.text):
            raise HTTPException(status_code=409, detail="not_interviewing")
    log.info("answer_stored interview=%s key=%s chars=%d", interview_id, key, len(body.text))
    return {"stored": True}


@router.delete("/{interview_id}/answers")
def clear_answers(
    interview_id: UUID = Depends(agent_auth),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> dict:
    with db.connection() as conn:
        if not repo.clear_draft(conn, interview_id):
            raise HTTPException(status_code=409, detail="not_interviewing")
    return {"cleared": True}


@router.get("/{interview_id}/preview")
def preview(
    interview_id: UUID = Depends(agent_auth),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> dict:
    """The exact text that would be posted: what the agent reads back and the screen shows."""
    with db.connection() as conn:
        row = repo.get_interview(conn, interview_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    if row.state not in _PREVIEW_STATES:
        raise HTTPException(status_code=409, detail="not_available")
    try:
        payload = build_payload(row.draft.get("answers", {}))
    except IncompleteDraft:
        raise HTTPException(status_code=409, detail="incomplete_draft") from None
    return {"verb": payload.verb, "content": payload.content, "why": payload.why}


@router.post("/{interview_id}/post")
def post(
    body: PostIn,
    request: Request,
    interview_id: UUID = Depends(agent_auth),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> dict:
    """The agent reports a validated spoken confirmation. The gateway decides what happens."""
    outcome = post_interview(
        db,
        request.app.state.vault,
        request.app.state.proof,
        interview_id,
        body.confirmation_text,
    )
    if outcome.result == "not_found":
        raise HTTPException(status_code=404, detail="not_found")
    return {"result": outcome.result, "url": outcome.url, "reason": outcome.reason}

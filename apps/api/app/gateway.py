"""The post gateway: the ONLY code path that publishes to Proof (ADR-005).

What it guarantees, and how:

  * a log is posted only for an interview in `confirming` — the claim is one atomic
    `UPDATE ... WHERE state = 'confirming'`, so two simultaneous requests cannot both win;
  * the text posted is built here from the stored answers (app/payload.py); the caller supplies
    none of it;
  * no database connection is held while Proof is called (pool of 3, slow external call);
  * every Proof outcome ends in exactly one state: posted, back to confirming (definitely NOT
    applied, so the student may confirm again), failed (refused for good), or post_unknown (it
    MAY have been applied: never retried);
  * the user's daily limit is enforced before calling Proof (Proof allows 20/day per token).

The caller (the agent, through app/routers/internal.py) learns only the outcome category.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from app import repo
from app.crypto import TokenVault, VaultError
from app.db import Database
from app.payload import IncompleteDraft, build_payload
from app.proof import (
    PostOutcomeUnknown,
    PostRejected,
    ProofClient,
    ProofUnavailable,
    RateLimited,
    TokenRejected,
)

log = logging.getLogger(__name__)

DAILY_LIMIT = 20  # Proof's own limit per token per day; we refuse before spending a call


@dataclass(frozen=True, slots=True)
class GatewayResult:
    # posted | retryable | rejected | unknown | in_progress | not_confirming | not_found
    result: str
    url: str | None = None
    reason: str | None = None


def post_interview(
    db: Database,
    vault: TokenVault,
    proof: ProofClient,
    interview_id: UUID,
    confirmation_text: str,
) -> GatewayResult:
    # ---- 1. claim the interview atomically (short transaction) -------------------------------
    with db.connection() as conn:
        repo.resolve_stale_posting(conn)
        row = repo.get_interview(conn, interview_id)
        if row is None:
            return GatewayResult("not_found")
        if row.state != "confirming":
            return _describe_existing(row)
        if not repo.transition_interview(conn, interview_id, "confirming", "posting"):
            return _describe_existing(repo.get_interview(conn, interview_id))  # lost the race
        repo.save_confirmation(conn, interview_id, confirmation_text[:600])
        credential = repo.get_proof_credential(conn, row.user_id)
        posted_today = repo.count_posted_since(conn, row.user_id, hours=24)
    # ---- the connection is released: nothing below holds the database until finalising --------

    # ---- 2. checks that mean "definitely not sent" ----------------------------------------------
    if posted_today >= DAILY_LIMIT:
        return _finish(db, row, "failed", reason="daily_limit")
    if credential is None:
        return _finish(db, row, "confirming", reason="token_missing")
    try:
        payload = build_payload(row.draft.get("answers", {}))
    except IncompleteDraft:
        return _finish(db, row, "failed", reason="incomplete_draft")
    try:
        token = vault.decrypt(
            row.user_id,
            ciphertext=credential.ciphertext,
            nonce=credential.nonce,
            key_id=credential.key_id,
        )
    except VaultError:
        log.error("vault_decrypt_failed interview=%s", interview_id)
        return _finish(db, row, "failed", reason="vault_error")

    # ---- 3. the one external call: never retried ----------------------------------------------
    try:
        result = proof.post_log(token, verb=payload.verb, content=payload.content, why=payload.why)
    except TokenRejected:
        with (
            db.connection() as conn
        ):  # the stored token is dead: disconnect it so the UI asks again
            repo.delete_proof_credential(conn, row.user_id)
        return _finish(db, row, "confirming", reason="token_rejected")
    except RateLimited:
        return _finish(db, row, "confirming", reason="rate_limited")
    except ProofUnavailable:
        return _finish(db, row, "confirming", reason="unavailable")
    except PostRejected:
        return _finish(db, row, "failed", reason="rejected")
    except PostOutcomeUnknown:
        return _finish(db, row, "post_unknown")
    except Exception as exc:  # noqa: BLE001 - after an unexpected error we cannot know what happened
        # Class name only, never log.exception(): a library's message or traceback can echo the
        # request headers, and the Proof token travels in one.
        log.error("post_unexpected_error interview=%s error=%s", interview_id, type(exc).__name__)
        return _finish(db, row, "post_unknown")
    return _finish(db, row, "posted", url=result.url)


def _finish(
    db: Database,
    row: repo.InterviewRow,
    dst: str,
    *,
    url: str | None = None,
    reason: str | None = None,
) -> GatewayResult:
    """Leave `posting` for exactly one resolved state (atomic, so a stale resolver cannot clash)."""
    with db.connection() as conn:
        moved = repo.transition_interview(conn, row.id, "posting", dst, proof_url=url)
    log.info("post_outcome interview=%s outcome=%s reason=%s moved=%s", row.id, dst, reason, moved)
    category = {
        "posted": "posted",
        "confirming": "retryable",
        "failed": "rejected",
        "post_unknown": "unknown",
    }[dst]
    return GatewayResult(category, url=url, reason=reason)


def _describe_existing(row: repo.InterviewRow | None) -> GatewayResult:
    """The interview was not postable now. Report where it is, so repeats are harmless."""
    if row is None:
        return GatewayResult("not_found")
    if row.state == "posted":
        return GatewayResult("posted", url=row.proof_url)  # idempotent replay
    if row.state == "posting":
        return GatewayResult("in_progress")
    if row.state == "post_unknown":
        return GatewayResult("unknown")
    if row.state == "failed":
        return GatewayResult("rejected")
    return GatewayResult("not_confirming", reason=row.state)

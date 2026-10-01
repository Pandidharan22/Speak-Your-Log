"""Store / inspect / remove the student's Proof token.

The token crosses the network exactly once (the PUT body) and is never returned, echoed, logged
or stored in plaintext. Order of operations matters:

  1. validate the token's shape,
  2. validate it with Proof — with NO database connection held (the pool is tiny and Proof is slow),
  3. only then encrypt and store it.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app import repo
from app.auth import current_user, get_db
from app.crypto import InvalidTokenFormat, normalize_token, token_last4
from app.db import Database
from app.proof import PostRejected, ProofUnavailable, RateLimited, TokenRejected
from app.schemas import SessionStatus

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(max_length=1024)  # SecretStr: never printed if the model is logged


def _throttle(request: Request, user: repo.User) -> None:
    """Each attempt calls an external service, so cap attempts per client IP and per user."""
    limiter = request.app.state.token_limiter
    ip = request.client.host if request.client else "unknown"
    if not (limiter.allow(f"ip:{ip}") and limiter.allow(f"user:{user.id}")):
        raise HTTPException(
            status_code=429,
            detail="too_many_attempts",
            headers={"Retry-After": str(limiter.retry_after(f"user:{user.id}"))},
        )


@router.put("/proof-token")
def connect_proof_token(
    body: TokenIn,
    request: Request,
    user: repo.User = Depends(current_user),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> SessionStatus:
    _throttle(request, user)
    try:
        token = normalize_token(body.token.get_secret_value())
    except InvalidTokenFormat:
        raise HTTPException(status_code=422, detail="invalid_token_format") from None

    try:
        request.app.state.proof.validate_token(token)  # no DB connection is held here
    except TokenRejected:
        raise HTTPException(status_code=400, detail="token_rejected") from None
    except PostRejected:
        raise HTTPException(status_code=400, detail="token_cannot_post") from None
    except RateLimited as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(429, detail="proof_rate_limited", headers=headers) from None
    except ProofUnavailable:
        raise HTTPException(status_code=503, detail="proof_unavailable") from None

    encrypted = request.app.state.vault.encrypt(token, user.id)
    with db.connection() as conn:
        repo.upsert_proof_credential(
            conn,
            user.id,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            key_id=encrypted.key_id,
            last4=token_last4(token),
        )
    log.info("proof_token_connected user=%s key_id=%s", user.id, encrypted.key_id)
    return SessionStatus(connected=True, last4=token_last4(token))


@router.get("/proof-token")
def proof_token_status(
    user: repo.User = Depends(current_user),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> SessionStatus:
    with db.connection() as conn:
        credential = repo.get_proof_credential(conn, user.id)
    return SessionStatus(
        connected=credential is not None, last4=credential.last4 if credential else None
    )


@router.delete("/proof-token")
def disconnect_proof_token(
    user: repo.User = Depends(current_user),  # noqa: B008
    db: Database = Depends(get_db),  # noqa: B008
) -> SessionStatus:
    """Idempotent: removing a token that is not there is not an error."""
    with db.connection() as conn:
        removed = repo.delete_proof_credential(conn, user.id)
    log.info("proof_token_disconnected user=%s removed=%s", user.id, removed)
    return SessionStatus(connected=False, last4=None)

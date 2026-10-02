"""Typed queries for identity and the Proof-token vault.

Every function takes an open connection so the caller decides transaction boundaries, and every
query is schema-qualified and parameterised. Authorization is the caller's job: all reads and
writes here are scoped by `user_id`, and nothing returns another user's rows.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Interview:
    id: UUID
    user_id: UUID
    room_name: str
    state: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class InterviewRow:
    id: UUID
    user_id: UUID
    state: str
    draft: dict
    proof_url: str | None
    created_at: datetime
    posted_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProofCredential:
    user_id: UUID
    ciphertext: bytes
    nonce: bytes
    key_id: str
    last4: str
    updated_at: datetime


# ---- identity -------------------------------------------------------------------------------


def create_user(conn: psycopg.Connection) -> User:
    row = conn.execute(
        "insert into speakyourlog.users default values returning id, created_at"
    ).fetchone()
    return User(id=row["id"], created_at=row["created_at"])


def create_device_session(
    conn: psycopg.Connection, user_id: UUID, token_hash: bytes, expires_at: datetime
) -> None:
    conn.execute(
        "insert into speakyourlog.device_sessions (token_hash, user_id, expires_at)"
        " values (%s, %s, %s)",
        (token_hash, user_id, expires_at),
    )


def get_user_by_session(conn: psycopg.Connection, token_hash: bytes) -> User | None:
    """Resolve a hashed cookie to its user; expired sessions resolve to nothing."""
    row = conn.execute(
        "select u.id, u.created_at"
        " from speakyourlog.device_sessions s"
        " join speakyourlog.users u on u.id = s.user_id"
        " where s.token_hash = %s and s.expires_at > now()",
        (token_hash,),
    ).fetchone()
    return User(id=row["id"], created_at=row["created_at"]) if row else None


def touch_user(conn: psycopg.Connection, user_id: UUID) -> None:
    """Bump last_seen_at at most hourly, so reads do not turn into a write per request."""
    conn.execute(
        "update speakyourlog.users set last_seen_at = now()"
        " where id = %s and last_seen_at < now() - interval '1 hour'",
        (user_id,),
    )


def delete_device_session(conn: psycopg.Connection, token_hash: bytes) -> bool:
    cur = conn.execute(
        "delete from speakyourlog.device_sessions where token_hash = %s", (token_hash,)
    )
    return cur.rowcount == 1


def purge_expired_sessions(conn: psycopg.Connection) -> int:
    return conn.execute(
        "delete from speakyourlog.device_sessions where expires_at <= now()"
    ).rowcount


def purge_orphan_users(conn: psycopg.Connection) -> int:
    """Delete anonymous users that can never be used again: no session, no token, no interviews.

    Interviews are checked explicitly because deleting a user cascades to their interviews.
    """
    return conn.execute(
        "delete from speakyourlog.users u"
        " where not exists (select 1 from speakyourlog.device_sessions s where s.user_id = u.id)"
        "   and not exists (select 1 from speakyourlog.proof_credentials c where c.user_id = u.id)"
        "   and not exists (select 1 from speakyourlog.interview_sessions i where i.user_id = u.id)"
    ).rowcount


def purge_stale(conn: psycopg.Connection) -> None:
    purge_expired_sessions(conn)
    purge_orphan_users(conn)
    resolve_stale_posting(conn)
    purge_old_drafts(conn)


# ---- Proof-token vault (ciphertext only; encryption happens in app/crypto, step 2.4) -------


def upsert_proof_credential(
    conn: psycopg.Connection,
    user_id: UUID,
    *,
    ciphertext: bytes,
    nonce: bytes,
    key_id: str,
    last4: str,
) -> None:
    conn.execute(
        "insert into speakyourlog.proof_credentials (user_id, ciphertext, nonce, key_id, last4)"
        " values (%s, %s, %s, %s, %s)"
        " on conflict (user_id) do update set"
        "   ciphertext = excluded.ciphertext, nonce = excluded.nonce,"
        "   key_id = excluded.key_id, last4 = excluded.last4, updated_at = now()",
        (user_id, ciphertext, nonce, key_id, last4),
    )


def get_proof_credential(conn: psycopg.Connection, user_id: UUID) -> ProofCredential | None:
    row = conn.execute(
        "select user_id, ciphertext, nonce, key_id, last4, updated_at"
        " from speakyourlog.proof_credentials where user_id = %s",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return ProofCredential(
        user_id=row["user_id"],
        ciphertext=bytes(row["ciphertext"]),
        nonce=bytes(row["nonce"]),
        key_id=row["key_id"],
        last4=row["last4"],
        updated_at=row["updated_at"],
    )


def delete_proof_credential(conn: psycopg.Connection, user_id: UUID) -> bool:
    cur = conn.execute("delete from speakyourlog.proof_credentials where user_id = %s", (user_id,))
    return cur.rowcount == 1


# ---- interviews ---------------------------------------------------------------------------------


def create_interview(conn: psycopg.Connection, user_id: UUID, room_name: str) -> Interview:
    row = conn.execute(
        "insert into speakyourlog.interview_sessions (user_id, room_name) values (%s, %s)"
        " returning id, user_id, room_name, state, created_at",
        (user_id, room_name),
    ).fetchone()
    return Interview(**row)


def count_recent_active_interviews(conn: psycopg.Connection, within_minutes: int) -> int:
    """Interviews that may still hold a LiveKit agent session (not finished, recently started)."""
    return conn.execute(
        "select count(*) as n from speakyourlog.interview_sessions"
        " where state in ('created', 'interviewing', 'confirming', 'posting')"
        "   and created_at > now() - make_interval(mins => %s)",
        (within_minutes,),
    ).fetchone()["n"]


def mark_interview_failed(conn: psycopg.Connection, interview_id: UUID) -> bool:
    """created -> failed, atomically (only if it is still in `created`)."""
    cur = conn.execute(
        "update speakyourlog.interview_sessions set state = 'failed', updated_at = now()"
        " where id = %s and state = 'created'",
        (interview_id,),
    )
    return cur.rowcount == 1


def get_interview(conn: psycopg.Connection, interview_id: UUID) -> InterviewRow | None:
    row = conn.execute(
        "select id, user_id, state, draft, proof_url, created_at, posted_at"
        " from speakyourlog.interview_sessions where id = %s",
        (interview_id,),
    ).fetchone()
    return InterviewRow(**row) if row else None


def transition_interview(
    conn: psycopg.Connection,
    interview_id: UUID,
    src: str,
    dst: str,
    *,
    proof_url: str | None = None,
) -> bool:
    """Atomically move `src -> dst`. True only if THIS call made the move (0 rows otherwise):
    the compare-and-set is what stops two requests from both winning (e.g. a double post)."""
    from app.states import InterviewState, require_transition

    require_transition(InterviewState(src), InterviewState(dst))  # illegal moves never reach SQL
    cur = conn.execute(
        "update speakyourlog.interview_sessions"
        " set state = %s, updated_at = now(),"
        "     proof_url = coalesce(%s, proof_url),"
        "     posted_at = case when %s = 'posted' then now() else posted_at end"
        " where id = %s and state = %s",
        (dst, proof_url, dst, interview_id, src),
    )
    return cur.rowcount == 1


def set_draft_answer(conn: psycopg.Connection, interview_id: UUID, key: str, text: str) -> bool:
    """Store one verbatim answer (overwrites). Only while the interview is `interviewing`."""
    cur = conn.execute(
        "update speakyourlog.interview_sessions"
        " set draft = jsonb_set(draft, '{answers}',"
        "       coalesce(draft->'answers', '{}'::jsonb) || jsonb_build_object(%s::text, %s::text)),"
        "     updated_at = now()"
        " where id = %s and state = 'interviewing'",
        (key, text, interview_id),
    )
    return cur.rowcount == 1


def clear_draft(conn: psycopg.Connection, interview_id: UUID) -> bool:
    cur = conn.execute(
        "update speakyourlog.interview_sessions set draft = '{}'::jsonb, updated_at = now()"
        " where id = %s and state = 'interviewing'",
        (interview_id,),
    )
    return cur.rowcount == 1


def save_confirmation(conn: psycopg.Connection, interview_id: UUID, text: str) -> None:
    """The student's spoken reply that triggered the post: kept in the draft as the audit trail."""
    conn.execute(
        "update speakyourlog.interview_sessions"
        " set draft = jsonb_set(draft, '{confirmation}', to_jsonb(%s::text), true)"
        " where id = %s",
        (text, interview_id),
    )


def count_posted_since(conn: psycopg.Connection, user_id: UUID, hours: int) -> int:
    return conn.execute(
        "select count(*) as n from speakyourlog.interview_sessions"
        " where user_id = %s and state = 'posted'"
        "   and posted_at > now() - make_interval(hours => %s)",
        (user_id, hours),
    ).fetchone()["n"]


def resolve_stale_posting(conn: psycopg.Connection, older_than_seconds: int = 120) -> int:
    """A post that has been `posting` for minutes lost its process (crash, redeploy) mid-call.
    We cannot know whether Proof applied it, so it becomes `post_unknown`: never retried."""
    return conn.execute(
        "update speakyourlog.interview_sessions set state = 'post_unknown', updated_at = now()"
        " where state = 'posting' and updated_at < now() - make_interval(secs => %s)",
        (older_than_seconds,),
    ).rowcount


def purge_old_drafts(conn: psycopg.Connection, hours: int = 24) -> int:
    """Text drafts do not outlive their session by more than a day (SRS NFR-3)."""
    return conn.execute(
        "update speakyourlog.interview_sessions set draft = '{}'::jsonb"
        " where state in ('posted', 'post_unknown', 'failed', 'cancelled')"
        "   and draft <> '{}'::jsonb"
        "   and updated_at < now() - make_interval(hours => %s)",
        (hours,),
    ).rowcount

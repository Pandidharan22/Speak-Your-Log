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

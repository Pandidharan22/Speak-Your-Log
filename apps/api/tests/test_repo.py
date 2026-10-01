"""Integration tests against the real isolated schema as the limited `syl_app` role.

Each test runs in a transaction that is rolled back, so nothing persists in the shared project.
"""

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from psycopg import errors

from app import repo

pytestmark = pytest.mark.integration


def future() -> datetime:
    return datetime.now(UTC) + timedelta(days=30)


def past() -> datetime:
    return datetime.now(UTC) - timedelta(days=1)


def save_credential(conn, user_id, **overrides):
    values = {
        "ciphertext": b"\x00" + os.urandom(46) + b"\xff",  # NUL and 0xFF bytes must round-trip
        "nonce": os.urandom(12),
        "key_id": "v1",
        "last4": "abcd",
    } | overrides
    repo.upsert_proof_credential(conn, user_id, **values)
    return values


def last_seen(conn, user_id):
    return conn.execute(
        "select last_seen_at from speakyourlog.users where id = %s", (user_id,)
    ).fetchone()["last_seen_at"]


def test_session_lifecycle(conn):
    user = repo.create_user(conn)
    token_hash = os.urandom(32)
    repo.create_device_session(conn, user.id, token_hash, future())

    assert repo.get_user_by_session(conn, token_hash).id == user.id
    assert repo.get_user_by_session(conn, os.urandom(32)) is None  # unknown cookie
    assert repo.delete_device_session(conn, token_hash) is True
    assert repo.get_user_by_session(conn, token_hash) is None
    assert repo.delete_device_session(conn, token_hash) is False  # idempotent


def test_expired_sessions_do_not_authenticate_and_can_be_purged(conn):
    user = repo.create_user(conn)
    token_hash = os.urandom(32)
    repo.create_device_session(conn, user.id, token_hash, past())

    assert repo.get_user_by_session(conn, token_hash) is None
    assert repo.purge_expired_sessions(conn) >= 1


def test_touch_user_updates_at_most_hourly(conn):
    user = repo.create_user(conn)

    # `now()` is frozen inside a transaction, so a wrongful write would be invisible unless the
    # starting value differs from now(). Backdate by 10 minutes: recent enough to be skipped.
    conn.execute(
        "update speakyourlog.users set last_seen_at = now() - interval '10 minutes' where id = %s",
        (user.id,),
    )
    first = last_seen(conn, user.id)
    repo.touch_user(conn, user.id)
    assert last_seen(conn, user.id) == first  # fresh: no write

    conn.execute(
        "update speakyourlog.users set last_seen_at = now() - interval '2 hours' where id = %s",
        (user.id,),
    )
    stale = last_seen(conn, user.id)
    repo.touch_user(conn, user.id)
    assert last_seen(conn, user.id) > stale  # stale: refreshed


def test_credential_upsert_get_delete_roundtrip(conn):
    user = repo.create_user(conn)
    assert repo.get_proof_credential(conn, user.id) is None

    v1 = save_credential(conn, user.id)
    got = repo.get_proof_credential(conn, user.id)
    assert (got.ciphertext, got.nonce, got.key_id, got.last4) == (
        v1["ciphertext"],
        v1["nonce"],
        "v1",
        "abcd",
    )
    assert isinstance(got.ciphertext, bytes) and isinstance(got.nonce, bytes)

    v2 = save_credential(conn, user.id, key_id="v2", last4="wxyz")  # replaces, never duplicates
    got = repo.get_proof_credential(conn, user.id)
    assert (got.ciphertext, got.key_id, got.last4) == (v2["ciphertext"], "v2", "wxyz")
    count = conn.execute(
        "select count(*) as n from speakyourlog.proof_credentials where user_id = %s", (user.id,)
    ).fetchone()["n"]
    assert count == 1

    assert repo.delete_proof_credential(conn, user.id) is True
    assert repo.get_proof_credential(conn, user.id) is None
    assert repo.delete_proof_credential(conn, user.id) is False


def test_credentials_are_scoped_to_their_owner(conn):
    alice, bob = repo.create_user(conn), repo.create_user(conn)
    save_credential(conn, alice.id)

    assert repo.get_proof_credential(conn, bob.id) is None
    assert repo.delete_proof_credential(conn, bob.id) is False  # cannot delete someone else's
    assert repo.get_proof_credential(conn, alice.id) is not None


def test_deleting_a_user_cascades_to_sessions_and_credentials(conn):
    user = repo.create_user(conn)
    token_hash = os.urandom(32)
    repo.create_device_session(conn, user.id, token_hash, future())
    save_credential(conn, user.id)

    conn.execute("delete from speakyourlog.users where id = %s", (user.id,))
    assert repo.get_user_by_session(conn, token_hash) is None
    assert repo.get_proof_credential(conn, user.id) is None


@pytest.mark.parametrize(
    "bad",
    [{"nonce": os.urandom(11)}, {"nonce": os.urandom(13)}, {"last4": "abc"}, {"last4": "abcde"}],
)
def test_database_rejects_malformed_credentials(conn, bad):
    user = repo.create_user(conn)
    with pytest.raises(errors.CheckViolation), conn.transaction():  # savepoint keeps conn usable
        save_credential(conn, user.id, **bad)


def test_database_rejects_wrong_length_session_hash(conn):
    user = repo.create_user(conn)
    with pytest.raises(errors.CheckViolation), conn.transaction():
        repo.create_device_session(conn, user.id, os.urandom(31), future())


def test_unknown_user_cannot_get_a_session_or_credential(conn):
    ghost = uuid.uuid4()
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        repo.create_device_session(conn, ghost, os.urandom(32), future())
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        save_credential(conn, ghost)


def test_connections_never_use_server_side_prepared_statements(real_db):
    # Behind the transaction pooler a prepared statement can land on a different server
    # connection and fail intermittently in production only — so it must stay disabled.
    with real_db.connection() as c:
        assert c.prepare_threshold is None
        for _ in range(12):  # well past psycopg's default threshold of 5
            c.execute("select %s::int as n", (1,))
        prepared = c.execute("select count(*) as n from pg_prepared_statements").fetchone()["n"]
        assert prepared == 0


def test_pool_serves_more_threads_than_connections(real_db):
    # 8 concurrent requests, pool of 3: extras must queue and all succeed (no deadlock/timeouts).
    failures: list[BaseException] = []

    def work() -> None:
        try:
            with real_db.connection() as c:
                c.execute("select pg_sleep(0.05)")
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert failures == []
    assert real_db.ping() is True


def test_failed_request_leaves_no_committed_rows(real_db):
    marker_id = None
    with pytest.raises(RuntimeError), real_db.connection() as c:
        marker_id = repo.create_user(c).id
        raise RuntimeError("boom")
    with real_db.connection() as c:
        n = c.execute(
            "select count(*) as n from speakyourlog.users where id = %s", (marker_id,)
        ).fetchone()["n"]
    assert n == 0

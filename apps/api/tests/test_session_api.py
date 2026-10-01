"""The device-session endpoint end to end, against the real isolated schema.

The app runs on the test's single rolled-back connection, so nothing is committed to the shared
database. Requests are sent with the app's own Origin, as a browser would.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app import repo
from app.auth import current_user, hash_token
from app.main import create_app
from tests.conftest import PROD, SharedConnDb

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": "http://localhost:8000"}


@pytest.fixture
def make_client(make_settings, conn):
    def _make(**settings_overrides):
        settings = make_settings(**settings_overrides)
        app = create_app(settings, db=SharedConnDb(conn))

        @app.get("/api/whoami")  # test-only route to exercise the current_user dependency
        def whoami(user=Depends(current_user)):  # noqa: B008
            return {"id": str(user.id)}

        base = "https://testserver" if settings.cookie_secure else "http://testserver"
        origin = {"Origin": settings.public_base_url}
        return TestClient(app, base_url=base, headers=origin), settings

    return _make


def count(conn, table):
    return conn.execute(f"select count(*) as n from speakyourlog.{table}").fetchone()["n"]  # noqa: S608


def test_first_visit_creates_a_user_and_sets_a_hardened_cookie(make_client, conn):
    client, settings = make_client()
    before = count(conn, "users")

    r = client.post("/api/session")

    assert r.status_code == 200
    assert r.json() == {"connected": False, "last4": None}
    assert count(conn, "users") == before + 1
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("syl_session=")
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert f"max-age={90 * 24 * 3600}" in cookie
    assert "secure" not in cookie  # plain-http local dev only
    assert "domain=" not in cookie


def test_database_stores_only_the_hash_never_the_cookie_value(make_client, conn):
    client, settings = make_client()
    token = client.post("/api/session").cookies[settings.cookie_name]

    stored = conn.execute(
        "select token_hash from speakyourlog.device_sessions where token_hash = %s",
        (hash_token(token, settings.session_hmac_key_bytes),),
    ).fetchone()
    assert stored is not None
    assert (
        conn.execute(
            "select count(*) as n from speakyourlog.device_sessions where token_hash = %s",
            (token.encode(),),
        ).fetchone()["n"]
        == 0
    )


def test_returning_device_is_recognised_without_creating_another_user(make_client, conn):
    client, _ = make_client()
    client.post("/api/session")
    users = count(conn, "users")

    r = client.post("/api/session")  # cookie jar sends the cookie

    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    assert count(conn, "users") == users


def test_connected_state_reflects_a_stored_token_and_exposes_only_last4(make_client, conn):
    client, settings = make_client()
    client.post("/api/session")
    me = client.get("/api/whoami").json()["id"]
    repo.upsert_proof_credential(
        conn, me, ciphertext=os.urandom(48), nonce=os.urandom(12), key_id="v1", last4="9f2c"
    )

    body = client.post("/api/session").json()

    assert body == {"connected": True, "last4": "9f2c"}


@pytest.mark.parametrize("junk", ["garbage", "x" * 500, "../../etc/passwd", "a b c", ""])
def test_malformed_cookies_are_treated_as_no_session_not_an_error(make_client, junk):
    client, settings = make_client()
    client.cookies.set(settings.cookie_name, junk)
    r = client.post("/api/session")
    assert r.status_code == 200
    assert settings.cookie_name in r.cookies  # got a fresh, valid one


def test_a_tampered_cookie_does_not_authenticate(make_client, conn):
    client, settings = make_client()
    token = client.post("/api/session").cookies[settings.cookie_name]
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    client.cookies.clear()
    client.cookies.set(settings.cookie_name, tampered)

    assert client.get("/api/whoami").status_code == 401


def test_an_expired_session_does_not_authenticate(make_client, conn):
    # (A full day, not a second: Postgres freezes now() at transaction start inside a test.)
    client, settings = make_client()
    client.post("/api/session")
    conn.execute(
        "update speakyourlog.device_sessions set expires_at = %s",
        (datetime.now(UTC) - timedelta(days=1),),
    )
    assert client.get("/api/whoami").status_code == 401
    r = client.post("/api/session")  # and the app recovers by issuing a new session
    assert r.status_code == 200 and settings.cookie_name in r.cookies


def test_current_user_requires_a_session_and_does_not_create_one(make_client, conn):
    client, _ = make_client()
    users = count(conn, "users")
    r = client.get("/api/whoami")
    assert (r.status_code, r.json()) == (401, {"detail": "no_session"})
    assert count(conn, "users") == users


def test_two_devices_are_separate_users(make_client, conn):
    a, _ = make_client()
    b, _ = make_client()
    a.post("/api/session")
    b.post("/api/session")
    assert a.get("/api/whoami").json()["id"] != b.get("/api/whoami").json()["id"]


def test_production_cookie_is_host_prefixed_and_secure(make_client):
    client, settings = make_client(**PROD)
    cookie = client.post("/api/session").headers["set-cookie"].lower()
    assert cookie.startswith("__host-syl_session=")
    assert "secure" in cookie and "httponly" in cookie and "domain=" not in cookie


def test_creating_users_is_rate_limited_per_ip_but_returning_visitors_are_not(make_client):
    client, _ = make_client()
    client.post("/api/session")  # 1st creation
    for _ in range(30):  # a returning visitor can call as often as the UI needs
        assert client.post("/api/session").status_code == 200

    status = []
    for _ in range(12):  # new devices (no cookie) from the same IP
        fresh, _ = make_client()
        fresh.app.state.session_create_limiter = client.app.state.session_create_limiter
        status.append(fresh.post("/api/session"))
    assert [r.status_code for r in status].count(429) >= 1
    blocked = next(r for r in status if r.status_code == 429)
    assert blocked.json() == {"detail": "too_many_new_sessions"}
    assert int(blocked.headers["retry-after"]) >= 1


def test_wrong_origin_cannot_create_sessions(make_client, conn):
    client, _ = make_client()
    users = count(conn, "users")
    r = client.post("/api/session", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert count(conn, "users") == users  # rejected before touching the database


def test_recognising_a_returning_device_refreshes_last_seen(make_client, conn):
    client, _ = make_client()
    client.post("/api/session")
    conn.execute("update speakyourlog.users set last_seen_at = now() - interval '3 hours'")
    stale = conn.execute("select last_seen_at from speakyourlog.users").fetchone()["last_seen_at"]

    client.post("/api/session")

    fresh = conn.execute("select last_seen_at from speakyourlog.users").fetchone()["last_seen_at"]
    assert fresh > stale

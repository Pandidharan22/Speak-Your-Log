"""POST /api/interviews: real app and isolated schema, fake LiveKit (no real room is created)."""

import json
import logging
import os
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from livekit import api

from app import repo
from app.jobtoken import InvalidJobToken, verify_job_token
from app.livekit_service import AGENT_NAME, LiveKitService, LiveKitUnavailable
from app.main import create_app
from tests.conftest import KEY_B, SharedConnDb

pytestmark = pytest.mark.integration

LK_KEY, LK_SECRET = "lk-key", "lk-secret"  # match conftest VALID settings
JOB_SECRET = b"b" * 32  # what KEY_B decodes to


class FakeLiveKit(LiveKitService):
    """The real token logic, but no network: server calls are recorded (or made to fail)."""

    def __init__(self, db: SharedConnDb) -> None:
        super().__init__("wss://example.livekit.cloud", LK_KEY, LK_SECRET)
        self.db = db
        self.fail = False
        self.started: list[tuple[str, dict]] = []
        self.db_connections_held: list[int] = []

    def start_interview(self, room_name: str, metadata: str) -> None:
        self.db_connections_held.append(self.db.active)
        if self.fail:
            raise LiveKitUnavailable
        self.started.append((room_name, json.loads(metadata)))


class World:
    def __init__(self, make_settings, conn, caplog, **overrides) -> None:
        self.conn, self.caplog = conn, caplog
        self.settings = make_settings(**overrides)
        self.db = SharedConnDb(conn)
        self.lk = FakeLiveKit(self.db)
        self.app = create_app(self.settings, db=self.db, livekit=self.lk)

    def student(self, *, connected: bool = True) -> TestClient:
        c = TestClient(self.app, headers={"Origin": self.settings.public_base_url})
        c.post("/api/session")
        if connected:
            uid = self.user_id(c)
            repo.upsert_proof_credential(
                self.conn, uid, ciphertext=os.urandom(48), nonce=os.urandom(12),
                key_id="v1", last4="abcd",
            )  # fmt: skip
        return c

    def user_id(self, client: TestClient) -> UUID:
        from app.auth import hash_token

        token = client.cookies[self.settings.cookie_name]
        hashed = hash_token(token, self.settings.session_hmac_key_bytes)
        return repo.get_user_by_session(self.conn, hashed).id

    def interviews(self) -> list[dict]:
        return self.conn.execute(
            "select id, user_id, room_name, state from speakyourlog.interview_sessions"
            " order by created_at"
        ).fetchall()


@pytest.fixture
def world(make_settings, conn, caplog):
    caplog.set_level(logging.DEBUG)
    return World(make_settings, conn, caplog)


# ---- success ---------------------------------------------------------------------------------


def test_starting_an_interview_creates_the_record_and_dispatches_the_agent(world):
    me = world.student()
    r = me.post("/api/interviews")

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"interview_id", "livekit_url", "token"}
    assert body["livekit_url"] == "wss://example.livekit.cloud"

    (row,) = world.interviews()
    assert str(row["id"]) == body["interview_id"]
    assert row["state"] == "created" and row["room_name"].startswith("syl-")
    assert row["user_id"] == world.user_id(me)

    ((room, metadata),) = world.lk.started
    assert room == row["room_name"]
    assert metadata["interview_id"] == body["interview_id"]
    assert metadata["api_base_url"] == world.settings.public_base_url


def test_the_agent_receives_a_job_token_for_exactly_this_interview(world):
    body = world.student().post("/api/interviews").json()
    _, metadata = world.lk.started[0]

    assert str(verify_job_token(JOB_SECRET, metadata["job_token"])) == body["interview_id"]
    with pytest.raises(InvalidJobToken):  # and for no one else's secret
        verify_job_token(b"z" * 32, metadata["job_token"])


def test_the_job_token_never_reaches_the_browser(world):
    me = world.student()
    r = me.post("/api/interviews")
    job_token = world.lk.started[0][1]["job_token"]

    seen = r.text + json.dumps(dict(r.headers)) + json.dumps(dict(me.cookies)) + world.caplog.text
    assert job_token not in seen
    assert KEY_B not in seen and AGENT_NAME not in seen


def test_the_browser_token_can_join_only_this_room_with_the_microphone(world):
    r = world.student().post("/api/interviews").json()
    claims = api.TokenVerifier(LK_KEY, LK_SECRET).verify(r["token"])
    assert claims.video.room == world.lk.started[0][0]
    assert claims.video.room_join and not claims.video.room_admin
    assert list(claims.video.can_publish_sources) == ["microphone"]


def test_each_interview_gets_its_own_room_and_identity(world):
    me = world.student()
    a, b = me.post("/api/interviews").json(), me.post("/api/interviews").json()
    assert a["interview_id"] != b["interview_id"]
    assert world.lk.started[0][0] != world.lk.started[1][0]
    ids = [api.TokenVerifier(LK_KEY, LK_SECRET).verify(x["token"]).identity for x in (a, b)]
    assert ids[0] != ids[1] and all(i.startswith("student-") for i in ids)


def test_no_database_connection_is_held_while_calling_livekit(world):
    world.student().post("/api/interviews")
    assert world.lk.db_connections_held == [0]


# ---- who may start one -----------------------------------------------------------------------


def test_a_session_is_required(world):
    stranger = TestClient(world.app, headers={"Origin": world.settings.public_base_url})
    r = stranger.post("/api/interviews")
    assert (r.status_code, r.json()) == (401, {"detail": "no_session"})
    assert world.lk.started == [] and world.interviews() == []


def test_a_connected_proof_token_is_required_first(world):
    r = world.student(connected=False).post("/api/interviews")
    assert (r.status_code, r.json()) == (409, {"detail": "token_not_connected"})
    assert world.lk.started == [] and world.interviews() == []  # nothing created, nothing spent


def test_cross_site_requests_cannot_start_interviews(world):
    me = world.student()
    r = me.post("/api/interviews", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and world.lk.started == [] and world.interviews() == []


# ---- failure handling ------------------------------------------------------------------------


def test_a_livekit_outage_gives_a_clear_error_and_marks_the_interview_failed(world):
    world.lk.fail = True
    me = world.student()
    r = me.post("/api/interviews")

    assert (r.status_code, r.json()) == (502, {"detail": "voice_service_unavailable"})
    (row,) = world.interviews()
    assert row["state"] == "failed"  # not left dangling in `created`
    assert "token" not in r.json()


def test_a_failed_interview_does_not_hold_a_concurrency_slot(make_settings, conn, caplog):
    w = World(make_settings, conn, caplog, max_active_interviews=1)  # a single slot makes it sharp
    me = w.student()
    w.lk.fail = True
    assert me.post("/api/interviews").status_code == 502
    w.lk.fail = False
    assert (
        me.post("/api/interviews").status_code == 200
    )  # would be 503 if the failure held the slot


# ---- protecting the free plan ----------------------------------------------------------------


def test_the_global_cap_refuses_new_interviews_before_livekit_does(make_settings, conn, caplog):
    w = World(make_settings, conn, caplog, max_active_interviews=2)
    a, b, c = w.student(), w.student(), w.student()
    assert a.post("/api/interviews").status_code == 200
    assert b.post("/api/interviews").status_code == 200

    r = c.post("/api/interviews")
    assert (r.status_code, r.json()) == (503, {"detail": "busy_try_shortly"})
    assert r.headers["retry-after"] == "30"
    assert len(w.interviews()) == 2 and len(w.lk.started) == 2


def test_finished_interviews_free_their_slot(make_settings, conn, caplog):
    w = World(make_settings, conn, caplog, max_active_interviews=1)
    a, b = w.student(), w.student()
    assert a.post("/api/interviews").status_code == 200
    assert b.post("/api/interviews").status_code == 503

    conn.execute("update speakyourlog.interview_sessions set state = 'posted'")
    assert b.post("/api/interviews").status_code == 200


def test_one_user_cannot_start_unlimited_interviews(make_settings, conn, caplog):
    w = World(make_settings, conn, caplog, max_active_interviews=5)
    me = w.student()
    codes = []
    for _ in range(6):
        codes.append(me.post("/api/interviews").status_code)
        conn.execute("update speakyourlog.interview_sessions set state = 'posted'")  # free slots
    assert codes == [200] * 5 + [429]
    assert len(w.lk.started) == 5  # the refused one never reached LiveKit


def test_the_per_user_limit_does_not_block_other_users(make_settings, conn, caplog):
    w = World(make_settings, conn, caplog, max_active_interviews=5)
    heavy, other = w.student(), w.student()
    for _ in range(6):
        heavy.post("/api/interviews")
        conn.execute("update speakyourlog.interview_sessions set state = 'posted'")
    assert heavy.post("/api/interviews").status_code == 429
    assert other.post("/api/interviews").status_code == 200

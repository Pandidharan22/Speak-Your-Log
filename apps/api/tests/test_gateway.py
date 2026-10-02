"""The post gateway against the real isolated schema and a fake Proof server.

These tests are the proof of ADR-005: a log is posted only for an interview in `confirming`, at most
once, from the stored answers only, and every outcome leaves exactly one well-defined state.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import repo
from app.gateway import DAILY_LIMIT, GatewayResult, post_interview
from app.payload import build_payload
from tests.conftest import SharedConnDb
from tests.interview_helpers import (
    FULL,
    LOG_URL,
    TOKEN,
    FakeProof,
    make_interview,
    make_user_with_token,
    make_vault,
    state_of,
)

pytestmark = pytest.mark.integration


class Env:
    def __init__(self, conn) -> None:
        self.conn = conn
        self.db = SharedConnDb(conn)
        self.vault = make_vault()
        self.fake = FakeProof(self.db)
        self.proof = self.fake.client()
        self.user = make_user_with_token(conn, self.vault)

    def interview(self, state: str = "confirming", answers: dict | None = None):
        return make_interview(self.conn, self.user, state, answers)

    def post(self, interview_id, text: str = "yes, post it") -> GatewayResult:
        return post_interview(self.db, self.vault, self.proof, interview_id, text)


@pytest.fixture
def env(conn) -> Env:
    return Env(conn)


# ---- the happy path --------------------------------------------------------------------------


def test_a_confirmed_interview_is_posted_with_text_built_from_the_stored_answers(env):
    iv = env.interview()
    out = env.post(iv.id)

    assert out == GatewayResult("posted", url=LOG_URL)
    expected = build_payload(FULL)
    assert env.fake.posts == [{"verb": "built", "content": expected.content, "why": expected.why}]
    row = repo.get_interview(env.conn, iv.id)
    assert (row.state, row.proof_url) == ("posted", LOG_URL) and row.posted_at is not None


def test_the_proof_call_carries_the_decrypted_token_of_the_interviews_owner(env):
    other_token = "tok-SOMEONE-ELSE-0000000000"
    other = make_user_with_token(env.conn, env.vault, other_token)
    env.post(env.interview().id)
    bearer = env.fake.requests[0].headers["authorization"]
    assert bearer == f"Bearer {TOKEN}" and other_token not in bearer
    assert other != env.user


def test_the_spoken_confirmation_is_kept_as_the_audit_trail(env):
    iv = env.interview()
    env.post(iv.id, "yes, post it")
    assert repo.get_interview(env.conn, iv.id).draft["confirmation"] == "yes, post it"


def test_the_confirmation_text_is_truncated_not_trusted(env):
    iv = env.interview()
    env.post(iv.id, "y" * 5000)
    assert len(repo.get_interview(env.conn, iv.id).draft["confirmation"]) == 600


# ---- only from `confirming` ------------------------------------------------------------------


@pytest.mark.parametrize(
    "state,expected",
    [
        ("created", "not_confirming"),
        ("interviewing", "not_confirming"),
        ("cancelled", "not_confirming"),
        ("failed", "rejected"),
        ("post_unknown", "unknown"),
    ],
)
def test_no_other_state_can_ever_post(env, state, expected):
    iv = env.interview(state)
    out = env.post(iv.id)
    assert out.result == expected
    assert env.fake.requests == [] and state_of(env.conn, iv.id) == state


def test_a_post_already_in_flight_reports_in_progress_and_sends_nothing(env):
    iv = env.interview("posting")
    assert env.post(iv.id) == GatewayResult("in_progress")
    assert env.fake.requests == []


def test_an_unknown_interview_is_not_found(env):
    import uuid

    assert env.post(uuid.uuid4()) == GatewayResult("not_found")
    assert env.fake.requests == []


# ---- at most once ----------------------------------------------------------------------------


def test_posting_twice_is_harmless_and_the_second_call_replays_the_result(env):
    iv = env.interview()
    first, second = env.post(iv.id), env.post(iv.id)
    assert first == second == GatewayResult("posted", url=LOG_URL)
    assert len(env.fake.posts) == 1  # Proof was contacted exactly once


def test_two_requests_racing_for_the_same_interview_post_once(env):
    """Simulate the race: while the first call is waiting on Proof, a second one arrives."""
    iv = env.interview()
    inner: list[GatewayResult] = []

    def handler(request):
        inner.append(env.post(iv.id))  # re-entrant call, state is `posting` at this moment
        return FakeProof.ok(request)

    env.fake.handler = handler
    outer = env.post(iv.id)

    assert inner == [GatewayResult("in_progress")]
    assert outer == GatewayResult("posted", url=LOG_URL)
    assert len(env.fake.posts) == 1


def test_an_ambiguous_outcome_is_never_retried(env):
    iv = env.interview()
    env.fake.handler = lambda r: httpx.Response(503)
    assert env.post(iv.id).result == "unknown"
    env.fake.handler = FakeProof.ok  # Proof recovers; we still must not post again
    for _ in range(3):
        assert env.post(iv.id).result == "unknown"
    assert len(env.fake.requests) == 1 and state_of(env.conn, iv.id) == "post_unknown"


# ---- every Proof outcome ends in exactly one state -------------------------------------------

OUTCOMES = [
    ("token rejected", lambda r: httpx.Response(401), "retryable", "confirming", "token_rejected"),
    ("rate limited", lambda r: httpx.Response(429), "retryable", "confirming", "rate_limited"),
    ("unreachable", lambda r: httpx.ConnectError("down"), "retryable", "confirming", "unavailable"),
    ("connect timeout", lambda r: httpx.ConnectTimeout("t"), "retryable", "confirming",
     "unavailable"),
    ("refused", lambda r: httpx.Response(400), "rejected", "failed", "rejected"),
    ("500", lambda r: httpx.Response(500), "unknown", "post_unknown", None),
    ("502", lambda r: httpx.Response(502), "unknown", "post_unknown", None),
    ("read timeout", lambda r: httpx.ReadTimeout("slow"), "unknown", "post_unknown", None),
    ("connection reset", lambda r: httpx.ReadError("reset"), "unknown", "post_unknown", None),
    ("garbled reply", lambda r: httpx.Response(200, text="<html>"), "unknown", "post_unknown",
     None),
    ("rpc internal error", lambda r: httpx.Response(200, json={"error": {"code": -32603}}),
     "unknown", "post_unknown", None),
    ("unexpected crash", lambda r: ValueError("boom"), "unknown", "post_unknown", None),
]  # fmt: skip


@pytest.mark.parametrize(
    "name,handler,category,final_state,reason", OUTCOMES, ids=[o[0] for o in OUTCOMES]
)
def test_each_proof_outcome_maps_to_one_result_and_one_state(
    env, name, handler, category, final_state, reason
):
    iv = env.interview()
    env.fake.handler = handler
    out = env.post(iv.id)

    assert (out.result, out.reason) == (category, reason)
    assert state_of(env.conn, iv.id) == final_state
    assert len(env.fake.requests) == 1  # never retried inside the gateway


def test_a_rejected_token_is_disconnected_so_the_student_is_asked_to_reconnect(env):
    env.fake.handler = lambda r: httpx.Response(401)
    env.post(env.interview().id)
    assert repo.get_proof_credential(env.conn, env.user) is None


@pytest.mark.parametrize(
    "handler", [lambda r: httpx.Response(429), lambda r: httpx.ConnectError("x")]
)
def test_a_retryable_failure_keeps_the_token(env, handler):
    env.fake.handler = handler
    env.post(env.interview().id)
    assert repo.get_proof_credential(env.conn, env.user) is not None


def test_after_a_retryable_failure_a_fresh_confirmation_can_succeed_once(env):
    iv = env.interview()
    env.fake.handler = lambda r: httpx.Response(429)
    assert env.post(iv.id).result == "retryable" and state_of(env.conn, iv.id) == "confirming"

    env.fake.handler = FakeProof.ok  # the student confirms again
    assert env.post(iv.id) == GatewayResult("posted", url=LOG_URL)
    assert len(env.fake.requests) == 2 and state_of(env.conn, iv.id) == "posted"
    assert env.post(iv.id).result == "posted" and len(env.fake.requests) == 2  # replay: no 3rd call


# ---- checks that mean "definitely not sent" --------------------------------------------------


def test_the_daily_limit_blocks_the_call_without_contacting_proof(env):
    for _ in range(DAILY_LIMIT):
        repo.create_interview(env.conn, env.user, f"r-{os.urandom(4).hex()}")
    env.conn.execute(
        "update speakyourlog.interview_sessions set state = 'posted', posted_at = now()"
        " where state = 'created'"
    )
    iv = env.interview()
    out = env.post(iv.id)

    assert (out.result, out.reason) == ("rejected", "daily_limit")
    assert env.fake.requests == [] and state_of(env.conn, iv.id) == "failed"


def test_posts_older_than_24_hours_do_not_count_toward_the_limit(env):
    for _ in range(DAILY_LIMIT):  # a full day's quota, but all of it yesterday
        repo.create_interview(env.conn, env.user, f"r-{os.urandom(4).hex()}")
    env.conn.execute(
        "update speakyourlog.interview_sessions set state = 'posted', posted_at = %s"
        " where state = 'created'",
        (datetime.now(UTC) - timedelta(hours=25),),  # yesterday: outside the 24 h window
    )
    assert env.post(env.interview().id).result == "posted"


def test_posts_from_23_hours_ago_still_count_toward_the_limit(env):
    for _ in range(DAILY_LIMIT):
        repo.create_interview(env.conn, env.user, f"r-{os.urandom(4).hex()}")
    env.conn.execute(
        "update speakyourlog.interview_sessions set state = 'posted', posted_at = %s"
        " where state = 'created'",
        (datetime.now(UTC) - timedelta(hours=23),),  # inside the 24 h window
    )
    out = env.post(env.interview().id)
    assert (out.result, out.reason) == ("rejected", "daily_limit") and env.fake.requests == []


def test_the_daily_limit_is_per_user(env):
    other = make_user_with_token(env.conn, env.vault, "tok-OTHER-USER-9999999999")
    for _ in range(DAILY_LIMIT):
        iv = make_interview(env.conn, other, "posted")
        env.conn.execute(
            "update speakyourlog.interview_sessions set posted_at = now() where id = %s", (iv.id,)
        )
    assert env.post(env.interview().id).result == "posted"  # someone else's posts are not mine


def test_a_missing_token_is_retryable_without_calling_proof(env):
    repo.delete_proof_credential(env.conn, env.user)
    iv = env.interview()
    out = env.post(iv.id)
    assert (out.result, out.reason) == ("retryable", "token_missing")
    assert env.fake.requests == [] and state_of(env.conn, iv.id) == "confirming"


def test_a_ciphertext_that_does_not_belong_to_the_user_is_never_used(env):
    # A row copied from another user decrypts to nothing for this one (AAD binding).
    victim = make_user_with_token(env.conn, env.vault, "tok-VICTIM-0000000000000")
    stolen = repo.get_proof_credential(env.conn, victim)
    repo.upsert_proof_credential(
        env.conn, env.user, ciphertext=stolen.ciphertext, nonce=stolen.nonce,
        key_id=stolen.key_id, last4="0000",
    )  # fmt: skip
    iv = env.interview()
    out = env.post(iv.id)
    assert (out.result, out.reason) == ("rejected", "vault_error")
    assert env.fake.requests == [] and state_of(env.conn, iv.id) == "failed"


@pytest.mark.parametrize("missing", ["q1", "q2", "q3", "followup_a"])
def test_an_incomplete_draft_is_never_posted(env, missing):
    iv = env.interview(answers={k: v for k, v in FULL.items() if k != missing})
    out = env.post(iv.id)
    assert (out.result, out.reason) == ("rejected", "incomplete_draft")
    assert env.fake.requests == []


# ---- resources and secrets -------------------------------------------------------------------


def test_no_database_connection_is_held_while_waiting_for_proof(env):
    env.post(env.interview().id)
    assert env.fake.db_held == [0]


def test_the_token_never_appears_in_results_or_logs(env, caplog):
    caplog.set_level(logging.DEBUG)
    results = []
    for handler in (FakeProof.ok, lambda r: httpx.Response(401), lambda r: ValueError(TOKEN)):
        env.fake.handler = handler
        iv = env.interview()
        results.append(env.post(iv.id))
        if handler is not FakeProof.ok:  # restore the token a 401 deleted
            env.user = make_user_with_token(env.conn, env.vault)
    assert TOKEN not in repr(results) + caplog.text


def test_the_students_words_are_not_written_to_the_log(env, caplog):
    caplog.set_level(logging.DEBUG)
    env.post(env.interview().id, "yes, post it")
    for secret in (FULL["q1"], FULL["q3"], "yes, post it"):
        assert secret not in caplog.text


# ---- crash recovery --------------------------------------------------------------------------


def test_a_post_stuck_in_posting_after_a_crash_becomes_post_unknown_never_retried(env):
    stuck = env.interview("posting")
    env.conn.execute(
        "update speakyourlog.interview_sessions set updated_at = now() - interval '5 minutes'"
        " where id = %s",
        (stuck.id,),
    )
    other = env.interview()
    env.post(other.id)  # any gateway call resolves stale posts first
    assert state_of(env.conn, stuck.id) == "post_unknown"
    assert env.post(stuck.id).result == "unknown" and len(env.fake.posts) == 1  # only `other`


def test_a_fresh_post_in_flight_is_left_alone(env):
    live = env.interview("posting")
    env.post(env.interview().id)
    assert state_of(env.conn, live.id) == "posting"


def test_drafts_are_purged_a_day_after_the_session_ends_but_not_before(env):
    old, recent, live = (
        env.interview("posted"),
        env.interview("posted"),
        env.interview("confirming"),
    )
    env.conn.execute(
        "update speakyourlog.interview_sessions set updated_at = now() - interval '25 hours'"
        " where id in (%s, %s)",
        (old.id, live.id),
    )
    repo.purge_stale(env.conn)
    assert repo.get_interview(env.conn, old.id).draft == {}
    assert repo.get_interview(env.conn, recent.id).draft != {}
    assert repo.get_interview(env.conn, live.id).draft != {}  # a live session keeps its draft


def test_gateway_results_are_plain_data(env):
    out = env.post(env.interview().id)
    assert json.dumps({"result": out.result, "url": out.url, "reason": out.reason})


def test_the_atomic_claim_stops_a_request_that_read_a_stale_confirming_row(env, monkeypatch):
    """Two requests both READ `confirming`; only one can win the compare-and-set UPDATE."""
    from dataclasses import replace

    iv = env.interview("posting")  # a competing request has already claimed it
    stale = replace(repo.get_interview(env.conn, iv.id), state="confirming")
    real, calls = repo.get_interview, []

    def get_interview(conn, interview_id):
        calls.append(1)
        return (
            stale if len(calls) == 1 else real(conn, interview_id)
        )  # only the first read is stale

    monkeypatch.setattr("app.gateway.repo.get_interview", get_interview)
    out = env.post(iv.id)

    assert out == GatewayResult("in_progress")  # the UPDATE affected 0 rows: we lost the race
    assert env.fake.requests == [] and state_of(env.conn, iv.id) == "posting"

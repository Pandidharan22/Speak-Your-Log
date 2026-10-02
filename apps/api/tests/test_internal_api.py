"""The agent-facing endpoints and the browser's status endpoint, over HTTP.

Authentication is the per-interview job token; the key properties tested here are that the agent
can store answers and say "confirmed" but can never supply post text or move an interview into
`posting`.
"""

import json
import logging
import time
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app import repo
from app.auth import hash_token
from app.jobtoken import sign_job_token
from app.main import create_app
from app.payload import build_payload
from tests.conftest import SharedConnDb
from tests.interview_helpers import (
    FULL,
    LOG_URL,
    FakeProof,
    make_interview,
    make_user_with_token,
    make_vault,
    state_of,
)

pytestmark = pytest.mark.integration

JOB_SECRET = b"b" * 32  # what the test settings' AGENT_JOB_SECRET decodes to


class World:
    def __init__(self, make_settings, conn, caplog=None) -> None:
        self.conn = conn
        self.settings = make_settings()
        self.db = SharedConnDb(conn)
        self.vault = make_vault()
        self.fake = FakeProof(self.db)
        self.app = create_app(self.settings, db=self.db, proof=self.fake.client(), vault=self.vault)
        self.agent = TestClient(self.app)  # no cookie, no Origin: server-to-server

    def new_interview(self, state="interviewing", answers=None, user=None):
        user = user or make_user_with_token(self.conn, self.vault)
        return make_interview(self.conn, user, state, {} if answers is None else answers)

    def auth(self, interview_id: UUID, secret: bytes = JOB_SECRET, **kw) -> dict:
        return {"Authorization": f"Bearer {sign_job_token(secret, interview_id, **kw)}"}

    def call(self, method: str, interview_id: UUID, path: str = "", body=None, **kw):
        headers = kw.pop("headers", None) or self.auth(interview_id)
        return self.agent.request(
            method, f"/internal/interviews/{interview_id}{path}", json=body, headers=headers
        )


@pytest.fixture
def w(make_settings, conn, caplog):
    caplog.set_level(logging.DEBUG)
    return World(make_settings, conn)


ENDPOINTS = [
    ("GET", "", None),
    ("POST", "/state", {"state": "interviewing"}),
    ("PUT", "/answers/q1", {"text": "hello world again"}),
    ("DELETE", "/answers", None),
    ("GET", "/preview", None),
    ("POST", "/post", {"confirmation_text": "yes"}),
]


# ---- authentication --------------------------------------------------------------------------


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_every_endpoint_rejects_a_missing_or_malformed_credential(w, method, path, body):
    iv = w.new_interview("confirming", FULL)
    for headers in (
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer not-a-token"},
        {"Authorization": f"Token {sign_job_token(JOB_SECRET, iv.id)}"},
    ):
        r = w.call(method, iv.id, path, body, headers=headers or {"X-None": "1"})
        assert (r.status_code, r.json()) == (401, {"detail": "invalid_job_token"}), headers
    assert state_of(w.conn, iv.id) == "confirming" and w.fake.requests == []


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_a_token_for_another_interview_signed_with_the_wrong_key_or_expired_is_useless(
    w, method, path, body
):
    iv, other = w.new_interview("confirming", FULL), w.new_interview("confirming", FULL)
    bad = [
        w.auth(other.id),  # right key, wrong interview
        w.auth(iv.id, secret=b"z" * 32),  # wrong key
        w.auth(iv.id, ttl=timedelta(seconds=-5)),  # expired
    ]
    for headers in bad:
        assert w.call(method, iv.id, path, body, headers=headers).status_code == 401
    assert w.fake.requests == []


def test_a_valid_session_cookie_alone_is_not_enough(w, make_settings):
    iv = w.new_interview("confirming", FULL)
    browser = TestClient(w.app, headers={"Origin": w.settings.public_base_url})
    browser.post("/api/session")  # has a perfectly good cookie
    r = browser.post(f"/internal/interviews/{iv.id}/post", json={"confirmation_text": "yes"})
    assert r.status_code == 401 and w.fake.requests == []


def test_internal_responses_are_never_cached_and_carry_security_headers(w):
    iv = w.new_interview()
    r = w.call("POST", iv.id, "/state", {"state": "interviewing"})
    assert (
        r.headers["cache-control"] == "no-store"
        and r.headers["x-content-type-options"] == "nosniff"
    )


def test_oversized_bodies_are_rejected_before_processing(w):
    iv = w.new_interview()
    r = w.agent.put(
        f"/internal/interviews/{iv.id}/answers/q1",
        content=b'{"text": "' + b"x" * 20_000 + b'"}',
        headers={**w.auth(iv.id), "content-type": "application/json"},
    )
    assert r.status_code == 413


# ---- storing answers -------------------------------------------------------------------------


def test_answers_are_stored_exactly_as_heard(w):
    iv = w.new_interview()
    said = "  சென்சார் ரீடிங் சரியாவே வரல,  so I   switched.  "
    r = w.call("PUT", iv.id, "/answers/q1", {"text": said})
    assert (r.status_code, r.json()) == (200, {"stored": True})
    assert repo.get_interview(w.conn, iv.id).draft["answers"]["q1"] == said  # not even trimmed here


def test_an_answer_can_be_overwritten_and_other_answers_are_untouched(w):
    iv = w.new_interview()
    w.call("PUT", iv.id, "/answers/q1", {"text": "first try here"})
    w.call("PUT", iv.id, "/answers/q2", {"text": "second answer here"})
    w.call("PUT", iv.id, "/answers/q1", {"text": "better first answer"})
    assert repo.get_interview(w.conn, iv.id).draft["answers"] == {
        "q1": "better first answer",
        "q2": "second answer here",
    }


@pytest.mark.parametrize("key", ["q4", "content", "why", "verb", "followup", "Q1", "../q1"])
def test_only_the_five_known_answer_keys_exist(w, key):
    iv = w.new_interview()
    r = w.call("PUT", iv.id, f"/answers/{key}", {"text": "something said"})
    assert r.status_code in (404, 422)
    assert repo.get_interview(w.conn, iv.id).draft == {"answers": {}}


@pytest.mark.parametrize(
    "body", [{"text": ""}, {"text": "   \n"}, {}, {"text": 5}, {"text": "x" * 2001}]
)
def test_blank_missing_or_oversized_answers_are_refused(w, body):
    iv = w.new_interview()
    assert w.call("PUT", iv.id, "/answers/q1", body).status_code == 422
    assert repo.get_interview(w.conn, iv.id).draft == {"answers": {}}


def test_unknown_fields_are_refused_so_the_agent_cannot_smuggle_data(w):
    iv = w.new_interview()
    r = w.call("PUT", iv.id, "/answers/q1", {"text": "fine words here", "verb": "decided"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "state", ["created", "confirming", "posting", "posted", "cancelled", "failed"]
)
def test_answers_can_only_be_stored_while_interviewing(w, state):
    iv = w.new_interview(state, FULL)
    r = w.call("PUT", iv.id, "/answers/q1", {"text": "tampered later words"})
    assert (r.status_code, r.json()) == (409, {"detail": "not_interviewing"})
    assert repo.get_interview(w.conn, iv.id).draft["answers"]["q1"] == FULL["q1"]


def test_clearing_the_draft_only_works_while_interviewing(w):
    iv = w.new_interview("interviewing", FULL)
    assert w.call("DELETE", iv.id, "/answers").status_code == 200
    assert repo.get_interview(w.conn, iv.id).draft == {}
    done = w.new_interview("posted", FULL)
    assert w.call("DELETE", done.id, "/answers").status_code == 409


def test_validation_errors_never_echo_what_was_sent(w):
    iv = w.new_interview()
    r = w.call("PUT", iv.id, "/answers/q1", {"text": 12345, "secret": "TOPSECRETWORDS"})
    assert r.status_code == 422 and "TOPSECRETWORDS" not in r.text and "12345" not in r.text


# ---- the state endpoint ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["created", "interviewing", "confirming", "posted", "post_unknown", "failed"]
)
def test_the_agent_can_ask_where_the_interview_is_and_learns_only_the_state(w, state):
    iv = w.new_interview(state, FULL)
    r = w.call("GET", iv.id)
    assert (r.status_code, r.json()) == (200, {"state": state})  # no answers, no URL, no ids


def test_the_agent_moves_a_new_interview_to_interviewing(w):
    iv = w.new_interview("created")
    r = w.call("POST", iv.id, "/state", {"state": "interviewing"})
    assert (r.status_code, r.json()) == (200, {"state": "interviewing"})
    assert state_of(w.conn, iv.id) == "interviewing"


def test_reporting_the_same_state_again_is_harmless(w):
    iv = w.new_interview("interviewing")
    assert w.call("POST", iv.id, "/state", {"state": "interviewing"}).json() == {
        "state": "interviewing"
    }


def test_confirming_requires_a_complete_draft(w):
    iv = w.new_interview("interviewing", {k: v for k, v in FULL.items() if k != "followup_a"})
    r = w.call("POST", iv.id, "/state", {"state": "confirming"})
    assert (r.status_code, r.json()) == (409, {"detail": "incomplete_draft"})
    assert state_of(w.conn, iv.id) == "interviewing"
    w.call("PUT", iv.id, "/answers/followup_a", {"text": "the last missing answer"})
    assert w.call("POST", iv.id, "/state", {"state": "confirming"}).status_code == 200


def test_starting_over_from_confirming_clears_the_draft(w):
    iv = w.new_interview("confirming", FULL)
    r = w.call("POST", iv.id, "/state", {"state": "interviewing"})
    assert r.status_code == 200
    assert repo.get_interview(w.conn, iv.id).draft == {}  # nothing of the old take survives


@pytest.mark.parametrize(
    "target", ["posting", "posted", "post_unknown", "created", "COMPLETED", ""]
)
def test_the_agent_cannot_request_posting_or_any_gateway_state(w, target):
    iv = w.new_interview("confirming", FULL)
    r = w.call("POST", iv.id, "/state", {"state": target})
    assert r.status_code == 422 and state_of(w.conn, iv.id) == "confirming"
    assert w.fake.requests == []


@pytest.mark.parametrize(
    "current,target",
    [
        ("created", "confirming"),
        ("posted", "interviewing"),
        ("posted", "cancelled"),
        ("post_unknown", "interviewing"),
        ("failed", "interviewing"),
        ("cancelled", "interviewing"),
        ("posting", "cancelled"),
        ("posting", "interviewing"),
    ],
)
def test_illegal_transitions_are_refused_and_change_nothing(w, current, target):
    iv = w.new_interview(current, FULL)
    r = w.call("POST", iv.id, "/state", {"state": target})
    assert (r.status_code, r.json()) == (409, {"detail": "illegal_transition"})
    assert state_of(w.conn, iv.id) == current


@pytest.mark.parametrize("current", ["created", "interviewing", "confirming"])
def test_the_agent_can_cancel_or_fail_an_interview_that_has_not_started_posting(w, current):
    for target in ("cancelled", "failed"):
        iv = w.new_interview(current, FULL)
        assert w.call("POST", iv.id, "/state", {"state": target}).status_code == 200


def test_an_interview_that_does_not_exist_is_refused_everywhere_and_nothing_is_sent(w):
    ghost = uuid4()  # a correctly signed token for an interview with no row
    expected = {
        "": 404,
        "/state": 404,
        "/preview": 404,
        "/post": 404,
        "/answers/q1": 409,
        "/answers": 409,
    }
    for method, path, body in ENDPOINTS:
        assert w.call(method, ghost, path, body).status_code == expected[path], (method, path)
    assert w.fake.requests == []


# ---- preview: exactly what will be posted ----------------------------------------------------


def test_the_preview_is_the_exact_payload_the_gateway_will_post(w):
    iv = w.new_interview("confirming", FULL)
    r = w.call("GET", iv.id, "/preview")
    expected = build_payload(FULL)
    assert (r.status_code, r.json()) == (
        200,
        {"verb": "built", "content": expected.content, "why": expected.why},
    )


def test_what_the_student_is_shown_is_byte_for_byte_what_is_posted(w):
    """The product's core promise, end to end over HTTP."""
    words = {
        "q1": "  சென்சார் ரீடிங் சரியாவே வரல,  so I   switched. ",
        "q2": "the  IR sensor, because ultrasonic WAS noisy",
        "q3": "ஏன்னா it was cheaper ",
        "followup_q": "You said cheaper. Why does it matter?",
        "followup_a": " ultrasonic  was too slow for me",
    }
    iv = w.new_interview("interviewing")
    for key, text in words.items():
        assert w.call("PUT", iv.id, f"/answers/{key}", {"text": text}).status_code == 200
    assert w.call("POST", iv.id, "/state", {"state": "confirming"}).status_code == 200

    shown = w.call("GET", iv.id, "/preview").json()
    posted = w.call("POST", iv.id, "/post", {"confirmation_text": "yes, post it"}).json()

    assert posted == {"result": "posted", "url": LOG_URL, "reason": None}
    (sent,) = w.fake.posts
    assert (sent["verb"], sent["content"], sent["why"]) == (
        shown["verb"],
        shown["content"],
        shown["why"],
    )


@pytest.mark.parametrize("state", ["created", "cancelled", "failed"])
def test_the_preview_is_unavailable_outside_the_interview_states(w, state):
    iv = w.new_interview(state, FULL)
    assert w.call("GET", iv.id, "/preview").status_code == 409


def test_the_preview_of_an_incomplete_draft_is_refused(w):
    iv = w.new_interview("interviewing", {"q1": "only the first answer"})
    r = w.call("GET", iv.id, "/preview")
    assert (r.status_code, r.json()) == (409, {"detail": "incomplete_draft"})


# ---- posting over HTTP -----------------------------------------------------------------------


def test_the_agent_can_only_say_confirmed_never_supply_post_text(w):
    iv = w.new_interview("confirming", FULL)
    for body in (
        {"confirmation_text": "yes", "content": "EVIL CONTENT"},
        {"confirmation_text": "yes", "why": "EVIL WHY"},
        {"confirmation_text": "yes", "verb": "decided"},
        {"confirmation_text": "yes", "token": "x"},
    ):
        assert w.call("POST", iv.id, "/post", body).status_code == 422
    assert w.fake.requests == []
    w.call("POST", iv.id, "/post", {"confirmation_text": "yes, post it"})
    assert "EVIL" not in json.dumps(w.fake.posts)


def test_posting_from_the_wrong_state_over_http_sends_nothing(w):
    iv = w.new_interview("interviewing", FULL)
    r = w.call("POST", iv.id, "/post", {"confirmation_text": "yes"})
    assert r.json() == {"result": "not_confirming", "url": None, "reason": "interviewing"}
    assert w.fake.requests == []


def test_a_double_post_over_http_posts_once_and_replays_the_result(w):
    iv = w.new_interview("confirming", FULL)
    a = w.call("POST", iv.id, "/post", {"confirmation_text": "yes"}).json()
    b = w.call("POST", iv.id, "/post", {"confirmation_text": "yes"}).json()
    assert a == b == {"result": "posted", "url": LOG_URL, "reason": None}
    assert len(w.fake.posts) == 1


def test_the_agent_learns_only_an_outcome_category_never_proofs_detail_or_the_token(w):
    import httpx

    iv = w.new_interview("confirming", FULL)
    w.fake.handler = lambda r: httpx.Response(
        401, text="Bearer tok-GATEWAY-SECRET-1234567890 invalid"
    )
    r = w.call("POST", iv.id, "/post", {"confirmation_text": "yes"})
    assert r.json() == {"result": "retryable", "url": None, "reason": "token_rejected"}
    assert "tok-GATEWAY" not in r.text


# ---- the browser's status endpoint -----------------------------------------------------------


def _web_user(w):
    client = TestClient(w.app, headers={"Origin": w.settings.public_base_url})
    client.post("/api/session")
    token = client.cookies[w.settings.cookie_name]
    user = repo.get_user_by_session(w.conn, hash_token(token, w.settings.session_hmac_key_bytes))
    return client, user.id


def test_the_owner_sees_state_preview_and_result_link(w):
    client, uid = _web_user(w)
    iv = make_interview(w.conn, uid, "confirming", FULL)
    body = client.get(f"/api/interviews/{iv.id}").json()
    expected = build_payload(FULL)
    assert body == {
        "state": "confirming",
        "preview": {"content": expected.content, "why": expected.why},
        "proof_url": None,
    }
    w.conn.execute(
        "update speakyourlog.interview_sessions set state='posted', proof_url=%s where id=%s",
        (LOG_URL, iv.id),
    )
    assert client.get(f"/api/interviews/{iv.id}").json()["proof_url"] == LOG_URL


@pytest.mark.parametrize("state", ["created", "interviewing", "cancelled", "failed"])
def test_no_preview_is_shown_outside_the_confirmation_states(w, state):
    client, uid = _web_user(w)
    iv = make_interview(w.conn, uid, state, FULL)
    assert client.get(f"/api/interviews/{iv.id}").json()["preview"] is None


def test_someone_elses_interview_looks_exactly_like_one_that_does_not_exist(w):
    mine, my_uid = _web_user(w)
    theirs, their_uid = _web_user(w)
    iv = make_interview(w.conn, their_uid, "confirming", FULL)
    other = mine.get(f"/api/interviews/{iv.id}")
    missing = mine.get(f"/api/interviews/{uuid4()}")
    assert (
        (other.status_code, other.json())
        == (missing.status_code, missing.json())
        == (
            404,
            {"detail": "not_found"},
        )
    )
    assert my_uid != their_uid and theirs.get(f"/api/interviews/{iv.id}").status_code == 200


def test_the_status_endpoint_needs_a_session(w):
    iv = w.new_interview("confirming", FULL)
    stranger = TestClient(w.app)
    assert stranger.get(f"/api/interviews/{iv.id}").status_code == 401


def test_a_malformed_interview_id_is_a_clean_422_not_a_crash(w):
    client, _ = _web_user(w)
    assert client.get("/api/interviews/not-a-uuid").status_code == 422


def test_timestamps_for_job_tokens_use_the_clock_not_the_test_process(w):
    # Guards the helper itself: a freshly signed token must verify right now.
    iv = w.new_interview()
    assert w.call("GET", iv.id, "/preview", headers=w.auth(iv.id, now=time.time())).status_code in (
        200,
        409,
    )

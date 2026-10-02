"""The agent's API client, tested against a mock server (no network)."""

import asyncio
import json
import logging
import uuid

import httpx
import pytest

from interview import api_client
from interview.api_client import ApiAuthError, ApiClient, ApiUnavailable, Preview
from interview.flow import PostResult
from interview.jobinfo import JobInfo

IID = uuid.UUID("12345678-1234-5678-1234-567812345678")
TOKEN = "body.signature-SECRET-job-token"
BASE = "https://app.example.com"


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr(api_client.asyncio, "sleep", instant)


class Server:
    """A mock API: `script` maps (method, path-suffix) to a response or a list of responses."""

    def __init__(self, **script) -> None:
        self.script = script
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        suffix = request.url.path.removeprefix(f"/internal/interviews/{IID}")
        key = f"{request.method} {suffix}"
        out = self.script.get(key)
        if isinstance(out, list):
            out = out.pop(0) if len(out) > 1 else out[0]
        if out is None:
            return httpx.Response(404)
        if isinstance(out, Exception):
            raise out
        return out

    def count(self, key: str) -> int:
        return sum(
            1
            for r in self.requests
            if f"{r.method} {r.url.path.removeprefix(f'/internal/interviews/{IID}')}" == key
        )


def client(server: Server) -> ApiClient:
    job = JobInfo(IID, BASE, TOKEN)
    return ApiClient(job, http=httpx.AsyncClient(transport=httpx.MockTransport(server)))


def run(coro):
    return asyncio.run(coro)


def json_ok(**body) -> httpx.Response:
    return httpx.Response(200, json=body)


# ---- requests ---------------------------------------------------------------------------------


def test_requests_carry_the_job_token_and_target_this_interview_only():
    server = Server(**{"POST /state": json_ok(state="interviewing")})
    assert run(client(server).set_state("interviewing")) is True
    req = server.requests[0]
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert str(req.url) == f"{BASE}/internal/interviews/{IID}/state"
    assert json.loads(req.content) == {"state": "interviewing"}


def test_answers_are_sent_exactly_as_given():
    server = Server(**{"PUT /answers/q1": json_ok(stored=True)})
    said = "  சென்சார் ரீடிங்,  so I   switched. "
    assert run(client(server).put_answer("q1", said)) is True
    assert json.loads(server.requests[0].content) == {"text": said}


def test_preview_and_status_are_parsed():
    server = Server(
        **{
            "GET /preview": json_ok(verb="built", content="c text", why="w text"),
            "GET ": json_ok(state="confirming", url=None),
        }
    )
    c = client(server)
    assert run(c.preview()) == Preview("c text", "w text")
    assert run(c.status()) == "confirming"


def test_a_non_200_preview_is_none_not_an_exception():
    assert run(client(Server(**{"GET /preview": httpx.Response(409)})).preview()) is None


# ---- failures on idempotent calls -------------------------------------------------------------


def test_a_rejected_job_token_raises_an_auth_error_for_every_call():
    routes = (
        "POST /state",
        "PUT /answers/q1",
        "DELETE /answers",
        "GET /preview",
        "GET ",
        "POST /post",
    )
    c = client(Server(**{route: httpx.Response(401) for route in routes}))
    for call in (
        lambda: c.set_state("interviewing"),
        lambda: c.put_answer("q1", "x y z"),
        lambda: c.clear_answers(),
        lambda: c.preview(),
        lambda: c.status(),
    ):
        with pytest.raises(ApiAuthError):
            run(call())


def test_idempotent_calls_retry_through_a_network_blip_and_a_5xx():
    server = Server(
        **{"POST /state": [httpx.ConnectError("x"), httpx.Response(503), json_ok(state="x")]}
    )
    assert run(client(server).set_state("interviewing")) is True
    assert server.count("POST /state") == 3


def test_idempotent_calls_give_up_after_their_retries():
    server = Server(**{"PUT /answers/q1": httpx.ConnectError("down")})
    with pytest.raises(ApiUnavailable):
        run(client(server).put_answer("q1", "some words here"))
    assert server.count("PUT /answers/q1") == 3  # 1 try + 2 retries


# ---- posting: never retried, ambiguity resolved by asking -------------------------------------


@pytest.mark.parametrize(
    "result,expected",
    [
        ("posted", PostResult.POSTED),
        ("retryable", PostResult.RETRYABLE),
        ("rejected", PostResult.REJECTED),
        ("unknown", PostResult.UNKNOWN),
        ("not_confirming", PostResult.REJECTED),
    ],
)
def test_the_apis_result_maps_to_the_flows_post_result(result, expected):
    server = Server(**{"POST /post": json_ok(result=result, url="https://p/x", reason="why")})
    out = run(client(server).post("yes, post it"))
    assert out.result is expected and out.url == "https://p/x" and out.reason == "why"
    assert server.count("POST /post") == 1
    assert json.loads(server.requests[0].content) == {"confirmation_text": "yes, post it"}


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("slow"), httpx.ConnectError("x"), httpx.Response(500), httpx.Response(502)],
)
def test_a_lost_post_reply_is_never_retried_and_is_resolved_by_asking(failure):
    server = Server(**{"POST /post": failure, "GET ": json_ok(state="posted", url="u")})
    out = run(client(server).post("yes"))
    assert out.result is PostResult.POSTED
    assert server.count("POST /post") == 1  # the publishing call happened exactly once


@pytest.mark.parametrize(
    "state,expected",
    [
        ("posted", PostResult.POSTED),
        ("confirming", PostResult.RETRYABLE),
        ("failed", PostResult.REJECTED),
        ("post_unknown", PostResult.UNKNOWN),
    ],
)
def test_the_final_interview_state_tells_us_what_a_lost_reply_meant(state, expected):
    server = Server(**{"POST /post": httpx.ReadTimeout("slow"), "GET ": json_ok(state=state)})
    assert run(client(server).post("yes")).result is expected


def test_while_the_post_is_still_in_flight_we_wait_for_it_to_resolve():
    server = Server(
        **{
            "POST /post": json_ok(result="in_progress", url=None, reason=None),
            "GET ": [json_ok(state="posting"), json_ok(state="posting"), json_ok(state="posted")],
        }
    )
    assert run(client(server).post("yes")).result is PostResult.POSTED
    assert server.count("POST /post") == 1


def test_a_post_that_never_resolves_is_unknown_never_assumed_not_posted():
    server = Server(**{"POST /post": httpx.ReadTimeout("x"), "GET ": json_ok(state="posting")})
    assert run(client(server).post("yes")).result is PostResult.UNKNOWN


def test_if_even_the_status_check_fails_the_answer_is_unknown():
    server = Server(**{"POST /post": httpx.ReadTimeout("x"), "GET ": httpx.ConnectError("down")})
    out = run(client(server).post("yes"))
    assert out.result is PostResult.UNKNOWN and server.count("POST /post") == 1


def test_an_unrecognised_reply_is_resolved_not_trusted():
    server = Server(
        **{"POST /post": json_ok(result="banana"), "GET ": json_ok(state="post_unknown")}
    )
    assert run(client(server).post("yes")).result is PostResult.UNKNOWN


def test_a_rejected_token_while_posting_is_an_auth_error():
    with pytest.raises(ApiAuthError):
        run(client(Server(**{"POST /post": httpx.Response(401)})).post("yes"))


# ---- secrets and privacy ----------------------------------------------------------------------


def test_the_token_and_the_students_words_never_reach_logs_or_errors(caplog):
    caplog.set_level(logging.DEBUG)
    server = Server(**{"PUT /answers/q1": httpx.ConnectError(f"boom {TOKEN} private words")})
    with pytest.raises(ApiUnavailable) as exc:
        run(client(server).put_answer("q1", "my private words about the robot"))
    seen = caplog.text + str(exc.value) + repr(exc.value) + repr(exc.value.__cause__)
    assert TOKEN not in seen and "private" not in seen and "robot" not in seen


def test_errors_carry_no_context_that_could_hold_the_request():
    server = Server(**{"POST /state": httpx.ConnectError("x")})
    with pytest.raises(ApiUnavailable) as exc:
        run(client(server).set_state("interviewing"))
    assert exc.value.__cause__ is None and exc.value.__suppress_context__ and str(exc.value) == ""


def test_the_client_never_follows_redirects_so_the_token_stays_put():
    c = ApiClient(JobInfo(IID, BASE, TOKEN))
    assert c._http.follow_redirects is False
    run(c.aclose())

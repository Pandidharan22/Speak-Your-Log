"""The Proof client, tested ONLY against mocked HTTP: no test can ever publish a real log."""

import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from app.proof import (
    MAX_FIELD_CHARS,
    PostOutcomeUnknown,
    PostRejected,
    ProofClient,
    ProofUnavailable,
    RateLimited,
    TokenRejected,
)

URL = "https://proof.example.com/api/mcp"
TOKEN = SecretStr("tok-SECRET-VALUE-1234567890")
LOG_URL = "https://proof.example.com/u/ana/logs/42"


class Recorder:
    """A mock Proof server that records every request it receives."""

    def __init__(self, respond):
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        out = self._respond(request)
        if isinstance(out, Exception):
            raise out
        return out

    @property
    def calls(self) -> int:
        return len(self.requests)

    def body(self, i=0) -> dict:
        return json.loads(self.requests[i].content)


def make(respond) -> tuple[ProofClient, Recorder]:
    rec = Recorder(respond)
    return ProofClient(URL, http=httpx.Client(transport=httpx.MockTransport(rec))), rec


def rpc_ok(result) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def rpc_err(code, message="boom") -> httpx.Response:
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": message}}
    )


def posted_ok(text=f"Logged! {LOG_URL}.") -> httpx.Response:
    return rpc_ok({"content": [{"type": "text", "text": text}]})


def tools_with(*names) -> httpx.Response:
    return rpc_ok({"tools": [{"name": n} for n in names]})


def post(client, **kw):
    args = {"verb": "built", "content": "I tried an IR sensor."} | kw
    return client.post_log(TOKEN, **args)


# ---- request shape --------------------------------------------------------------------------


def test_requests_are_well_formed_json_rpc_with_bearer_auth():
    client, rec = make(lambda r: posted_ok())
    post(client, why="because it was cheaper")

    req = rec.requests[0]
    assert (req.method, str(req.url)) == ("POST", URL)
    assert req.headers["authorization"] == f"Bearer {TOKEN.get_secret_value()}"
    assert req.headers["content-type"] == "application/json"
    assert rec.body() == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "post_log",
            "arguments": {
                "verb": "built",
                "content": "I tried an IR sensor.",
                "why": "because it was cheaper",
            },
        },
    }


def test_the_students_words_are_sent_byte_for_byte_including_tamil():
    words = "சென்சார் ரீடிங் சரியாவே வரல, so I switched to an IR sensor.  Two  spaces."
    why = "அல்ட்ராசோனிக்கை விட வேகமா வேலை செய்யும்னு நினைச்சேன்"
    client, rec = make(lambda r: posted_ok())
    post(client, content=words, why=why)
    sent = rec.body()["params"]["arguments"]
    assert sent["content"] == words and sent["why"] == why  # no normalisation, no trimming


def test_the_client_transmits_text_exactly_and_leaves_trimming_to_the_payload_builder():
    # Whitespace policy (ADR-007) lives in one place upstream. The transport must never alter
    # what it is given, so a "tidy-up" added here would silently rewrite the student's words.
    padded = "  leading, trailing and\ttabbed \n"
    client, rec = make(lambda r: posted_ok())
    post(client, content=padded, why=padded)
    sent = rec.body()["params"]["arguments"]
    assert sent["content"] == padded and sent["why"] == padded


def test_optional_fields_are_omitted_when_absent():
    client, rec = make(lambda r: posted_ok())
    post(client)
    assert set(rec.body()["params"]["arguments"]) == {"verb", "content"}
    post(client, why="y", evidence_url="https://github.com/x/y")
    assert set(rec.body(1)["params"]["arguments"]) == {"verb", "content", "why", "evidence_url"}


def test_request_ids_increase():
    client, rec = make(lambda r: posted_ok())
    post(client)
    post(client)
    assert (rec.body(0)["id"], rec.body(1)["id"]) == (1, 2)


# ---- success --------------------------------------------------------------------------------


def test_success_extracts_the_log_url_and_strips_trailing_punctuation():
    client, _ = make(lambda r: posted_ok(f"Posted to your record: {LOG_URL}."))
    assert post(client).url == LOG_URL


def test_success_reads_a_structured_url_too():
    client, _ = make(lambda r: rpc_ok({"content": [], "structuredContent": {"log_url": LOG_URL}}))
    assert post(client).url == LOG_URL


def test_success_without_a_recognisable_url_is_still_success():
    # We cannot be sure of the success reply shape; a posted log must never look like a failure
    # (that would invite a duplicate retry).
    client, _ = make(lambda r: rpc_ok({"content": [{"type": "text", "text": "done"}]}))
    assert post(client).url is None


@pytest.mark.parametrize(
    "evil",
    [
        "https://evil.example/login",
        "https://proof.example.com.evil.example/x",
        "https://notproof.example.com/x",
        "http://proof.example.com/x",  # not https
    ],
)
def test_urls_not_on_proofs_own_host_are_never_passed_on(evil):
    client, _ = make(lambda r: posted_ok(f"see {evil}"))
    assert post(client).url is None


def test_subdomains_of_proofs_host_are_accepted():
    client, _ = make(lambda r: posted_ok("https://u.proof.example.com/log/1"))
    assert post(client).url == "https://u.proof.example.com/log/1"


# ---- validate_token (read-only) -------------------------------------------------------------


def test_validate_token_accepts_a_token_that_can_post_logs():
    client, rec = make(lambda r: tools_with("post_log", "list_my_recent_logs"))
    client.validate_token(TOKEN)
    assert rec.body()["method"] == "tools/list" and "params" not in rec.body()


def test_validate_token_rejects_a_valid_token_without_post_log():
    client, _ = make(lambda r: tools_with("list_my_recent_logs"))
    with pytest.raises(PostRejected):
        client.validate_token(TOKEN)


@pytest.mark.parametrize(
    "response,expected",
    [
        (httpx.Response(401, json={"error": {"code": -32001}}), TokenRejected),
        (rpc_err(-32001), TokenRejected),
        (httpx.Response(500), ProofUnavailable),  # for a READ, "unknown" is just "unavailable"
        (httpx.Response(200, text="<html>not json</html>"), ProofUnavailable),
        (httpx.ReadTimeout("slow"), ProofUnavailable),
        (httpx.ConnectError("down"), ProofUnavailable),
    ],
)
def test_validate_token_failures_are_classified(response, expected):
    client, _ = make(lambda r: response)
    with pytest.raises(expected):
        client.validate_token(TOKEN)


# ---- refusing bad input BEFORE any network call ---------------------------------------------


@pytest.mark.parametrize(
    "kw",
    [
        {"verb": "yelled"},
        {"verb": ""},
        {"content": ""},
        {"content": "   \n"},
        {"verb": "decided"},  # 'why' required
        {"verb": "decided", "why": "  "},
        {"content": "x" * (MAX_FIELD_CHARS + 1)},
        {"why": "x" * (MAX_FIELD_CHARS + 1)},
        {"evidence_url": "javascript:alert(1)"},
        {"evidence_url": "ftp://x.example/y"},
    ],
)
def test_invalid_posts_are_refused_locally_without_contacting_proof(kw):
    client, rec = make(lambda r: posted_ok())
    with pytest.raises(PostRejected):
        post(client, **kw)
    assert rec.calls == 0


def test_content_at_the_limit_is_sent_whole_never_truncated():
    client, rec = make(lambda r: posted_ok())
    post(client, content="x" * MAX_FIELD_CHARS)
    assert len(rec.body()["params"]["arguments"]["content"]) == MAX_FIELD_CHARS


# ---- failure classification for post_log ----------------------------------------------------

DEFINITIVE = [  # the request certainly had no effect
    ("401", httpx.Response(401), TokenRejected),
    ("403", httpx.Response(403), TokenRejected),
    ("rpc -32001", rpc_err(-32001), TokenRejected),
    ("429", httpx.Response(429), RateLimited),
    ("400", httpx.Response(400), PostRejected),
    ("404", httpx.Response(404), PostRejected),
    ("405", httpx.Response(405, text="Method Not Allowed"), PostRejected),
    ("422", httpx.Response(422), PostRejected),
    ("rpc -32601 unknown tool", rpc_err(-32601, "Unknown tool: post_log"), PostRejected),
    ("rpc -32602 bad params", rpc_err(-32602), PostRejected),
    (
        "tool isError",
        rpc_ok({"isError": True, "content": [{"type": "text", "text": "limit"}]}),
        PostRejected,
    ),
    ("408", httpx.Response(408), ProofUnavailable),
    (
        "301 redirect",
        httpx.Response(301, headers={"location": "https://evil.example/"}),
        ProofUnavailable,
    ),
    (
        "307 redirect",
        httpx.Response(307, headers={"location": "https://evil.example/"}),
        ProofUnavailable,
    ),
    ("connect error", httpx.ConnectError("refused"), ProofUnavailable),
    ("connect timeout", httpx.ConnectTimeout("slow"), ProofUnavailable),
]

AMBIGUOUS = [  # the request MAY have been applied
    ("500", httpx.Response(500), PostOutcomeUnknown),
    ("502", httpx.Response(502), PostOutcomeUnknown),
    ("503", httpx.Response(503), PostOutcomeUnknown),
    ("504", httpx.Response(504), PostOutcomeUnknown),
    ("read timeout", httpx.ReadTimeout("slow"), PostOutcomeUnknown),
    ("read error", httpx.ReadError("reset"), PostOutcomeUnknown),
    ("write error", httpx.WriteError("broken pipe"), PostOutcomeUnknown),
    ("protocol error", httpx.RemoteProtocolError("garbled"), PostOutcomeUnknown),
    ("200 not json", httpx.Response(200, text="<html>"), PostOutcomeUnknown),
    ("200 json array", httpx.Response(200, json=[1, 2]), PostOutcomeUnknown),
    (
        "200 no result no error",
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1}),
        PostOutcomeUnknown,
    ),
    ("rpc -32603 internal", rpc_err(-32603), PostOutcomeUnknown),
    ("rpc unfamiliar code", rpc_err(-31999), PostOutcomeUnknown),
    ("huge reply", httpx.Response(200, content=b"x" * 1_000_001), PostOutcomeUnknown),
]


@pytest.mark.parametrize(
    "name,response,expected",
    DEFINITIVE + AMBIGUOUS,
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_every_failure_maps_to_the_right_error_and_is_never_retried(name, response, expected):
    client, rec = make(lambda r: response)
    with pytest.raises(expected) as exc:
        post(client)
    assert rec.calls == 1, "a post must never be retried: it is public and not idempotent"
    assert expected in (PostOutcomeUnknown,) or exc.value.definitive_not_applied
    assert exc.value.definitive_not_applied is (expected is not PostOutcomeUnknown)


def test_rate_limit_reports_retry_after_when_given():
    client, _ = make(lambda r: httpx.Response(429, headers={"retry-after": "120"}))
    with pytest.raises(RateLimited) as exc:
        post(client)
    assert exc.value.retry_after == 120
    client, _ = make(lambda r: httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2026"}))
    with pytest.raises(RateLimited) as exc:
        post(client)
    assert exc.value.retry_after is None  # unparseable: don't guess


def test_redirects_are_never_followed_so_the_token_never_leaves():
    seen_hosts = []

    def respond(request):
        seen_hosts.append(request.url.host)
        return httpx.Response(307, headers={"location": "https://evil.example/steal"})

    # Even an injected client that WOULD follow redirects must not, because it is set per request.
    rec = Recorder(respond)
    client = ProofClient(
        URL, http=httpx.Client(transport=httpx.MockTransport(rec), follow_redirects=True)
    )
    with pytest.raises(ProofUnavailable):
        post(client)
    assert seen_hosts == ["proof.example.com"]


# ---- the token never leaks ------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,response,expected",
    DEFINITIVE + AMBIGUOUS,
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_token_never_appears_in_errors_or_logs(name, response, expected, caplog):
    secret = TOKEN.get_secret_value()
    client, _ = make(lambda r: response)
    with caplog.at_level(logging.DEBUG), pytest.raises(expected) as exc:
        post(client)
    chain, e = [], exc.value
    while e is not None:
        chain += [str(e), repr(e)]
        e = e.__cause__ or (None if e.__suppress_context__ else e.__context__)
    assert secret not in " ".join(chain) + caplog.text + repr(client)


def test_a_hostile_reply_that_echoes_the_token_is_redacted():
    secret = TOKEN.get_secret_value()
    client, _ = make(lambda r: rpc_err(-32602, f"bad header Authorization: Bearer {secret}"))
    with pytest.raises(PostRejected) as exc:
        post(client)
    assert secret not in exc.value.detail and "[redacted]" in exc.value.detail
    client, _ = make(
        lambda r: rpc_ok(
            {"isError": True, "content": [{"type": "text", "text": f"you sent {secret}"}]}
        )
    )
    with pytest.raises(PostRejected) as exc:
        post(client)
    assert secret not in exc.value.detail


def test_third_party_error_text_is_length_bounded():
    client, _ = make(lambda r: rpc_err(-32602, "A" * 5000))
    with pytest.raises(PostRejected) as exc:
        post(client)
    assert len(exc.value.detail) <= 200


def test_only_https_endpoints_are_allowed():
    with pytest.raises(ValueError):
        ProofClient("http://proof.example.com/api/mcp")

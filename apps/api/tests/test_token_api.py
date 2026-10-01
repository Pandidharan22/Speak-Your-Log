"""The Proof-token endpoints end to end: real app, real isolated schema, mocked Proof.

The headline property under test: the token enters through ONE request and is never visible again —
not in any response, header, cookie, log line, error, or database column.
"""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app import repo
from app.crypto import DecryptionError, TokenVault
from app.main import create_app
from app.proof import ProofClient
from tests.conftest import KEY_C, SharedConnDb

pytestmark = pytest.mark.integration

SECRET = "tok-SECRET-9f8e7d6c5b4a-ZZZZ"
OTHER = "tok-OTHER-1a2b3c4d5e6f-YYYY"


class FakeProof:
    """A mock Proof server. `mode` chooses how it behaves; every call is recorded."""

    def __init__(self, db: SharedConnDb) -> None:
        self.db = db
        self.mode = "ok"
        self.requests: list[httpx.Request] = []
        self.db_connections_held_during_call: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.db_connections_held_during_call.append(self.db.active)
        token = request.headers["authorization"].removeprefix("Bearer ")
        ok = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "post_log"}]}}
        match self.mode:
            case "ok":
                return httpx.Response(200, json=ok)
            case "bad_token":
                return httpx.Response(401, json={"error": {"code": -32001, "message": "no"}})
            case "no_post_log":
                body = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "other"}]}}
                return httpx.Response(200, json=body)
            case "down":
                raise httpx.ConnectError("refused")
            case "limited":
                return httpx.Response(429, headers={"retry-after": "30"})
            case "echo_token":  # a hostile/buggy Proof that repeats our credential in its error
                err = {"code": -32602, "message": f"bad header Bearer {token}"}
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": err})
        raise AssertionError(self.mode)


class World:
    def __init__(self, conn, settings, caplog) -> None:
        self.conn = conn
        self.settings = settings
        self.caplog = caplog
        self.db = SharedConnDb(conn)
        self.proof = FakeProof(self.db)
        self.vault = TokenVault({"v1": b"c" * 32}, "v1")
        client = httpx.Client(transport=httpx.MockTransport(self.proof))
        self.app = create_app(
            settings,
            db=self.db,
            proof=ProofClient(settings.proof_mcp_url, http=client),
            vault=self.vault,
        )
        self.responses: list[httpx.Response] = []

    def device(self) -> TestClient:
        c = TestClient(self.app, headers={"Origin": self.settings.public_base_url})
        assert c.post("/api/session").status_code == 200
        return c

    def send(self, client: TestClient, method: str, **kw) -> httpx.Response:
        r = client.request(method, "/api/proof-token", **kw)
        self.responses.append(r)
        return r

    def put(self, client: TestClient, token: str = SECRET) -> httpx.Response:
        return self.send(client, "PUT", json={"token": token})

    def everything_visible_outside(self) -> str:
        """All text a client, proxy, or log reader could ever see, plus a dump of the DB."""
        seen = [
            r.text + json.dumps(dict(r.headers)) + json.dumps(dict(r.cookies))
            for r in self.responses
        ]
        rows = []
        for table in ("users", "device_sessions", "proof_credentials", "interview_sessions"):
            rows += [
                str(dict(row))
                for row in self.conn.execute(f"select * from speakyourlog.{table}").fetchall()
            ]  # noqa: S608
        return " ".join(seen + rows) + self.caplog.text

    def credentials(self) -> int:
        return self.conn.execute(
            "select count(*) as n from speakyourlog.proof_credentials"
        ).fetchone()["n"]


@pytest.fixture
def world(make_settings, conn, caplog):
    caplog.set_level(logging.DEBUG)
    return World(conn, make_settings(), caplog)


# ---- the happy path -------------------------------------------------------------------------


def test_connect_status_and_disconnect_lifecycle(world):
    me = world.device()

    assert world.send(me, "GET").json() == {"connected": False, "last4": None}

    r = world.put(me)
    assert (r.status_code, r.json()) == (200, {"connected": True, "last4": "ZZZZ"})
    assert world.send(me, "GET").json() == {"connected": True, "last4": "ZZZZ"}
    assert me.post("/api/session").json() == {"connected": True, "last4": "ZZZZ"}

    r = world.send(me, "DELETE")
    assert (r.status_code, r.json()) == (200, {"connected": False, "last4": None})
    assert world.send(me, "GET").json() == {"connected": False, "last4": None}
    assert world.credentials() == 0
    assert world.send(me, "DELETE").status_code == 200  # idempotent


def test_proof_is_asked_to_validate_with_the_real_token_exactly_once(world):
    world.put(world.device())
    assert len(world.proof.requests) == 1
    req = world.proof.requests[0]
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(req.content)["method"] == "tools/list"  # read-only: nothing is posted


def test_stored_token_is_ciphertext_bound_to_its_owner(world):
    me = world.device()
    world.put(me)
    user_id = (
        me.cookies
        and world.conn.execute("select user_id from speakyourlog.proof_credentials").fetchone()[
            "user_id"
        ]
    )
    row = repo.get_proof_credential(world.conn, user_id)

    assert row.key_id == "v1" and row.last4 == "ZZZZ"
    assert SECRET.encode() not in row.ciphertext
    got = world.vault.decrypt(user_id, ciphertext=row.ciphertext, nonce=row.nonce, key_id="v1")
    assert got.get_secret_value() == SECRET
    other = repo.create_user(world.conn)
    with pytest.raises(DecryptionError):
        world.vault.decrypt(other.id, ciphertext=row.ciphertext, nonce=row.nonce, key_id="v1")


def test_pasted_whitespace_is_trimmed_before_storing(world):
    r = world.put(world.device(), token=f"  {SECRET}\n")
    assert r.json()["last4"] == "ZZZZ"
    assert world.proof.requests[0].headers["authorization"] == f"Bearer {SECRET}"


def test_resubmitting_replaces_the_token_without_duplicating(world):
    me = world.device()
    world.put(me, SECRET)
    assert world.put(me, OTHER).json()["last4"] == "YYYY"
    assert world.credentials() == 1
    assert world.send(me, "GET").json()["last4"] == "YYYY"


def test_no_database_connection_is_held_while_waiting_for_proof(world):
    # The pool holds 3 connections and Proof can be slow: holding one across the call would let
    # a few slow requests starve the whole app (and the neighbouring app's shared limit).
    world.put(world.device())
    assert world.proof.db_connections_held_during_call == [0]


def test_each_user_sees_only_their_own_token(world):
    alice, bob = world.device(), world.device()
    world.put(alice)

    assert world.send(bob, "GET").json() == {"connected": False, "last4": None}
    world.send(bob, "DELETE")  # bob cannot disconnect alice
    assert world.send(alice, "GET").json() == {"connected": True, "last4": "ZZZZ"}


# ---- failures: clear codes, nothing stored --------------------------------------------------

FAILURES = [
    ("bad_token", 400, "token_rejected"),
    ("no_post_log", 400, "token_cannot_post"),
    ("echo_token", 400, "token_cannot_post"),
    ("limited", 429, "proof_rate_limited"),
    ("down", 503, "proof_unavailable"),
]


@pytest.mark.parametrize("mode,status,code", FAILURES)
def test_proof_failures_give_a_clear_code_and_store_nothing(world, mode, status, code):
    world.proof.mode = mode
    r = world.put(world.device())

    assert (r.status_code, r.json()) == (status, {"detail": code})
    assert world.credentials() == 0
    assert SECRET not in world.everything_visible_outside()
    assert len(world.proof.requests) == 1  # no retries


def test_rate_limited_response_passes_retry_after_through(world):
    world.proof.mode = "limited"
    assert world.put(world.device()).headers["retry-after"] == "30"


@pytest.mark.parametrize(
    "bad", ["short", "has space inside 12345", "x" * 513, "日本語日本語日本語日本語"]
)
def test_malformed_tokens_are_rejected_before_contacting_proof(world, bad):
    r = world.put(world.device(), token=bad)
    assert (r.status_code, r.json()) == (422, {"detail": "invalid_token_format"})
    assert world.proof.requests == [] and world.credentials() == 0


# ---- the token never comes back out ---------------------------------------------------------


def test_the_token_appears_nowhere_after_a_full_lifecycle(world):
    me = world.device()
    world.put(me)
    world.send(me, "GET")
    me.post("/api/session")
    world.put(me, OTHER)
    world.send(me, "DELETE")
    world.put(me)  # leave a stored token in place so the DB dump is meaningful

    visible = world.everything_visible_outside()
    assert SECRET not in visible and OTHER not in visible
    assert "proof_token_connected" in visible  # we do log the event... without the secret


@pytest.mark.parametrize(
    "body",
    [
        {"token": 12345},
        {"token": None},
        {"token": ["a"]},
        {"wrong_field": SECRET},
        {"token": SECRET, "extra": SECRET},
        [SECRET],
        SECRET,
        {},
    ],
)
def test_validation_errors_never_echo_the_input(world, body):
    # FastAPI's stock 422 repeats the offending input verbatim; ours must not.
    r = world.send(world.device(), "PUT", json=body)
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_request"
    assert SECRET not in r.text and "input" not in r.json()


def test_a_token_over_the_length_cap_is_not_echoed_either(world):
    marker = "MARKER-" + "q" * 1100
    r = world.put(world.device(), token=marker)
    assert r.status_code == 422 and "MARKER" not in r.text


def test_unparseable_json_bodies_are_not_echoed(world):
    r = world.send(
        world.device(),
        "PUT",
        content=b'{"token": "' + SECRET.encode() + b'", oops',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422 and SECRET not in r.text


def test_field_names_in_errors_are_limited_to_plain_identifiers(world):
    r = world.send(world.device(), "PUT", json={SECRET: 1})
    assert SECRET not in r.text  # even an attacker-chosen key name is not reflected


def test_responses_are_never_cacheable(world):
    r = world.put(world.device())
    assert r.headers["cache-control"] == "no-store"


# ---- who may call it ------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["PUT", "GET", "DELETE"])
def test_a_session_is_required_and_none_is_created_implicitly(world, method):
    stranger = TestClient(world.app, headers={"Origin": world.settings.public_base_url})
    users = world.conn.execute("select count(*) as n from speakyourlog.users").fetchone()["n"]

    kw = {"json": {"token": SECRET}} if method == "PUT" else {}
    r = world.send(stranger, method, **kw)

    assert (r.status_code, r.json()) == (401, {"detail": "no_session"})
    assert world.proof.requests == []  # Proof is never contacted for anonymous callers
    after = world.conn.execute("select count(*) as n from speakyourlog.users").fetchone()["n"]
    assert after == users


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_cross_site_requests_are_blocked_before_anything_happens(world, method):
    me = world.device()
    r = me.request(
        method,
        "/api/proof-token",
        headers={"Origin": "https://evil.example"},
        **({"json": {"token": SECRET}} if method == "PUT" else {}),
    )
    assert r.status_code == 403
    assert world.proof.requests == [] and world.credentials() == 0


def test_get_works_without_an_origin_header_but_writes_do_not(world):
    me = world.device()
    no_origin = TestClient(world.app)
    no_origin.cookies.update(me.cookies)
    assert no_origin.get("/api/proof-token").status_code == 200
    assert no_origin.put("/api/proof-token", json={"token": SECRET}).status_code == 403


def test_unsupported_methods_are_refused(world):
    assert world.device().post("/api/proof-token", json={"token": SECRET}).status_code == 405


# ---- abuse limits ---------------------------------------------------------------------------


def test_attempts_are_rate_limited_and_the_limited_ones_never_reach_proof(world):
    me = world.device()
    codes = [world.put(me).status_code for _ in range(12)]

    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]
    assert len(world.proof.requests) == 10
    blocked = world.responses[-1]
    assert (
        blocked.json() == {"detail": "too_many_attempts"}
        and int(blocked.headers["retry-after"]) >= 1
    )


def test_oversized_bodies_are_rejected_without_being_processed(world):
    me = world.device()
    r = world.send(
        me, "PUT", content=b"{" + b" " * 20_000 + b"}", headers={"content-type": "application/json"}
    )
    assert r.status_code == 413 and world.proof.requests == []


def test_a_garbage_content_length_is_a_clean_400(world):
    r = world.send(
        world.device(),
        "PUT",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "banana"},
    )
    assert r.status_code in (400, 422)  # rejected cleanly by us or the HTTP layer; never a crash


def test_vault_key_in_settings_matches_the_test_vault():
    # Guards the fixture itself: tests decrypt with b"c"*32, which is what KEY_C encodes.
    import base64

    assert base64.urlsafe_b64decode(KEY_C) == b"c" * 32

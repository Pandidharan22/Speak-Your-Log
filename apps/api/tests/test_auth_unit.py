"""Session helpers, rate limiter and the Origin (CSRF) rule — none of these need a database."""

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token, new_token
from app.main import create_app
from app.ratelimit import RateLimiter
from tests.conftest import KEY_A, KEY_B, PROD


class FakeDb:
    def open(self) -> None: ...

    def close(self) -> None: ...

    def ping(self) -> bool:
        return True


# ---- tokens ---------------------------------------------------------------------------------


def test_tokens_are_long_unique_and_url_safe():
    tokens = {new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 43 and t.replace("-", "").replace("_", "").isalnum() for t in tokens)


def test_hash_is_32_bytes_deterministic_and_keyed():
    key_a, key_b = KEY_A.encode()[:32], KEY_B.encode()[:32]
    token = new_token()
    assert len(hash_token(token, key_a)) == 32  # matches the DB CHECK (octet_length = 32)
    assert hash_token(token, key_a) == hash_token(token, key_a)
    assert hash_token(token, key_a) != hash_token(token, key_b)  # a different key: different hash
    assert hash_token(token, key_a) != hash_token(new_token(), key_a)
    assert token.encode() not in hash_token(token, key_a)  # the raw token is not recoverable


# ---- rate limiter ---------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_limiter_blocks_after_limit_and_recovers_after_window():
    clock = Clock()
    rl = RateLimiter(limit=3, window_seconds=60, clock=clock)
    assert [rl.allow("1.2.3.4") for _ in range(4)] == [True, True, True, False]
    assert 1 <= rl.retry_after("1.2.3.4") <= 61
    clock.now += 61
    assert rl.allow("1.2.3.4") is True  # a new window


def test_rate_limiter_keys_are_independent():
    rl = RateLimiter(limit=1, window_seconds=60, clock=Clock())
    assert rl.allow("a") and not rl.allow("a")
    assert rl.allow("b")  # one noisy client does not block another


def test_rate_limiter_memory_is_bounded():
    clock = Clock()
    rl = RateLimiter(limit=1, window_seconds=10, clock=clock, max_keys=100)
    for i in range(100):
        rl.allow(f"ip-{i}")
    clock.now += 11  # all earlier windows have expired
    for i in range(100, 150):
        rl.allow(f"ip-{i}")
    assert len(rl._hits) <= 100


def test_rate_limiter_memory_is_bounded_even_when_nothing_has_expired():
    # A flood of distinct keys inside ONE window (e.g. spoofed client IPs): expiry frees nothing.
    rl = RateLimiter(limit=1, window_seconds=600, clock=Clock(), max_keys=100)
    for i in range(5_000):
        rl.allow(f"ip-{i}")
    assert len(rl._hits) <= 100


def test_when_flooded_the_limiter_keeps_the_newest_keys_and_still_enforces_them():
    clock = Clock()
    rl = RateLimiter(limit=1, window_seconds=600, clock=clock, max_keys=10)
    # Eviction fires on the 11th, 17th, 23rd and 29th insertion (11 keys -> keep 5), so ending
    # on the 29th means the newest key was present at the moment of eviction.
    for i in range(29):
        clock.now += 1
        rl.allow(f"ip-{i}")
    assert not rl.allow("ip-28")  # the newest key survived the eviction and is still limited
    assert rl.allow("ip-0")  # the oldest did not (it is simply counted afresh)
    assert len(rl._hits) <= 10


# ---- Origin check (CSRF defence in depth) ---------------------------------------------------


@pytest.fixture
def client(make_settings):
    return TestClient(create_app(make_settings(), db=FakeDb()))


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_state_changing_api_requests_without_origin_are_rejected(client, method):
    r = client.request(method, "/api/session")
    assert r.status_code == 403
    assert r.json() == {"detail": "origin_not_allowed"}
    assert r.headers["x-content-type-options"] == "nosniff"  # 403s still get security headers


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "null", "http://localhost:8000.evil.example", "http://localhost:9999"],
)
def test_foreign_origins_are_rejected(client, origin):
    assert client.post("/api/session", headers={"Origin": origin}).status_code == 403


def test_safe_methods_do_not_need_an_origin(client):
    assert client.get("/api/anything").status_code == 404  # reached routing, not blocked
    assert client.get("/healthz").status_code == 200


def test_internal_agent_routes_are_outside_the_cookie_origin_rule(client):
    # The agent authenticates with a signed job token, not a cookie, so no Origin is expected.
    assert client.post("/internal/anything").status_code == 404  # not 403


def test_production_accepts_only_the_public_origin(make_settings):
    prod = TestClient(create_app(make_settings(**PROD), db=FakeDb()), base_url="https://testserver")
    assert (
        prod.post("/api/anything", headers={"Origin": "http://localhost:5173"}).status_code == 403
    )
    # Right origin passes the check (and then 404s because that route does not exist).
    assert (
        prod.post("/api/anything", headers={"Origin": "https://app.example.com"}).status_code == 404
    )

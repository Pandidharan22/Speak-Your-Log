import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from pydantic import ValidationError

from app.db import Database, build_conninfo
from app.main import create_app


class FakeDb:
    """Stands in for Database so app wiring is testable without a database."""

    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    def open(self) -> None: ...

    def close(self) -> None: ...

    def ping(self) -> bool:
        return self.healthy


def test_remote_hosts_require_tls_by_default():
    info = conninfo_to_dict(build_conninfo("postgresql://u:p@pooler.example.com:6543/postgres"))
    assert info["sslmode"] == "require"
    assert info["connect_timeout"] == "10"
    assert info["application_name"] == "syl-api"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_local_hosts_do_not_force_tls(host):
    assert "sslmode" not in conninfo_to_dict(build_conninfo(f"postgresql://u:p@{host}:5432/db"))


def test_explicit_sslmode_in_the_url_is_respected():
    url = "postgresql://u:p@pooler.example.com/db?sslmode=verify-full"
    assert conninfo_to_dict(build_conninfo(url))["sslmode"] == "verify-full"


def test_special_characters_in_password_survive():
    info = conninfo_to_dict(build_conninfo("postgresql://u:p%40ss%2Fw-rd_9@h.example.com/db"))
    assert info["password"] == "p@ss/w-rd_9"


def test_repr_never_contains_the_connection_string():
    db = Database("postgresql://syl_app.ref:supersecret@pooler.example.com:6543/postgres")
    assert "supersecret" not in repr(db) and "pooler.example.com" not in repr(db)


def test_ping_is_false_not_an_exception_when_database_is_unreachable():
    db = Database("postgresql://u:p@127.0.0.1:1/db", acquire_timeout=0.5)
    db.open()
    try:
        assert db.ping() is False
    finally:
        db.close()


def test_readyz_ok_when_database_reachable(make_settings):
    client = TestClient(create_app(make_settings(), db=FakeDb(True)))
    r = client.get("/readyz")
    assert (r.status_code, r.json()) == (200, {"status": "ready"})
    assert r.headers["cache-control"] == "no-store"


def test_readyz_503_without_details_when_database_down(make_settings):
    client = TestClient(create_app(make_settings(), db=FakeDb(False)))
    r = client.get("/readyz")
    assert (r.status_code, r.json()) == (503, {"status": "unavailable"})


def test_healthz_stays_up_when_database_is_down(make_settings):
    # Liveness must not depend on the DB, or a DB blip would restart/kill a healthy process.
    client = TestClient(create_app(make_settings(), db=FakeDb(False)))
    assert client.get("/healthz").status_code == 200


def test_lifespan_opens_and_closes_the_pool(make_settings):
    events: list[str] = []

    class Recording(FakeDb):
        def open(self) -> None:
            events.append("open")

        def close(self) -> None:
            events.append("close")

    with TestClient(create_app(make_settings(), db=Recording(True))):
        assert events == ["open"]
    assert events == ["open", "close"]


@pytest.mark.parametrize("size", [0, 6, -1])
def test_pool_size_is_capped_for_the_shared_database(make_settings, size):
    with pytest.raises(ValidationError):
        make_settings(db_pool_max_size=size)


def test_default_pool_size_is_small(make_settings):
    assert make_settings().db_pool_max_size == 3

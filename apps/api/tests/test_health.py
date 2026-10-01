import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import PROD


@pytest.fixture
def client(make_settings):
    return TestClient(create_app(make_settings()))


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_healthz_leaks_nothing_else(client):
    # Liveness must not reveal version, config, or dependency state.
    assert set(client.get("/healthz").json()) == {"status"}


def test_security_headers_present_on_every_response(client):
    for path in ("/healthz", "/does-not-exist"):  # success and 404 both pass the middleware
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert h["cross-origin-opener-policy"] == "same-origin"
        assert "microphone=(self)" in h["permissions-policy"]
        assert "camera=()" in h["permissions-policy"]


def test_api_and_health_responses_are_not_cacheable(client):
    assert client.get("/healthz").headers["cache-control"] == "no-store"
    assert client.get("/api/anything").headers["cache-control"] == "no-store"


def test_hsts_only_in_production(make_settings):
    dev = TestClient(create_app(make_settings(app_env="development")))
    prod = TestClient(create_app(make_settings(**PROD)))
    assert "strict-transport-security" not in dev.get("/healthz").headers
    assert prod.get("/healthz").headers["strict-transport-security"].startswith("max-age=")


def test_docs_available_in_dev_but_hidden_in_production(make_settings):
    dev = TestClient(create_app(make_settings(app_env="development")))
    prod = TestClient(create_app(make_settings(**PROD)))
    assert dev.get("/openapi.json").status_code == 200
    assert prod.get("/openapi.json").status_code == 404
    assert prod.get("/docs").status_code == 404


def test_create_app_does_not_read_environment_at_import():
    # Importing the module must be side-effect free; settings load only inside create_app().
    import importlib

    import app.main as main

    importlib.reload(main)  # would raise if it built Settings() at import with no env set


def test_app_info_logs_are_enabled_so_production_has_an_audit_trail(make_settings):
    import logging

    create_app(make_settings())
    create_app(make_settings())  # idempotent: no duplicate handlers
    logger = logging.getLogger("app")
    assert logger.isEnabledFor(logging.INFO)
    assert len(logger.handlers) <= 1

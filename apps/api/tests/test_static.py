"""Serving the built web app from the API's own origin, and the CSP that goes with it."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.static import content_security_policy
from tests.conftest import PROD

INDEX = "<!doctype html><title>Speak Your Log</title><div id=root></div>"


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("outside the dist directory", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(make_settings, dist):
    return TestClient(create_app(make_settings(web_dist_dir=dist)))


def test_the_root_and_any_client_side_route_serve_the_app(client):
    for path in ("/", "/interview", "/some/deep/route"):
        r = client.get(path)
        assert r.status_code == 200 and "Speak Your Log" in r.text, path


def test_the_html_carries_the_csp_and_is_never_cached(client):
    r = client.get("/")
    assert r.headers["content-security-policy"] == content_security_policy(
        "wss://example.livekit.cloud"
    )
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-frame-options"] == "DENY"  # the API's security headers still apply


def test_asking_for_index_html_directly_still_gets_the_csp(client):
    r = client.get("/index.html")
    assert r.status_code == 200 and "content-security-policy" in r.headers


def test_hashed_assets_are_cached_forever_and_have_no_csp_header_of_their_own(client):
    r = client.get("/assets/app-abc123.js")
    assert r.status_code == 200 and r.text == "console.log(1)"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_other_files_in_the_dist_directory_are_served_with_revalidation(client):
    r = client.get("/favicon.svg")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_the_api_keeps_precedence_over_the_catch_all(client):
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/api/nope", "/internal/nope", "/api/"])
def test_an_unknown_api_path_is_a_json_404_never_the_app(client, path):
    r = client.get(path)
    assert r.status_code == 404 and "Speak Your Log" not in r.text


@pytest.mark.parametrize(
    "path",
    ["/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt", "/%2e%2e%2fsecret.txt"],
)
def test_nothing_outside_the_dist_directory_is_ever_served(client, path):
    r = client.get(path)
    assert "outside the dist directory" not in r.text


def test_a_nul_byte_in_the_path_is_not_a_server_error(client):
    r = client.get("/assets/%00")
    assert r.status_code == 200 and "Speak Your Log" in r.text  # falls back to the app


def test_a_directory_path_falls_back_to_the_app(client):
    assert "Speak Your Log" in client.get("/assets").text


def test_head_requests_work(client):
    assert client.head("/").status_code == 200


def test_without_a_dist_directory_the_api_serves_no_pages(make_settings):
    client = TestClient(create_app(make_settings()))
    assert client.get("/").status_code == 404


def test_a_dist_directory_without_index_html_stops_the_boot(make_settings, tmp_path):
    with pytest.raises(ValueError, match="index.html"):
        make_settings(web_dist_dir=tmp_path)


def test_the_csp_allows_exactly_this_livekit_host_and_nothing_else_remote():
    csp = content_security_policy("wss://proof-voice-abc123.livekit.cloud")
    assert (
        "connect-src 'self' wss://proof-voice-abc123.livekit.cloud https://proof-voice-abc123.livekit.cloud"
        in csp
    )
    for directive in (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'none'",
    ):
        assert directive in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "*" not in csp


def test_render_external_url_is_used_when_no_public_url_is_set(monkeypatch):
    from app.config import Settings
    from tests.conftest import VALID

    env = {k: v for k, v in VALID.items() if k != "public_base_url"}
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://syl.onrender.com")
    assert Settings(_env_file=None, **env).public_base_url == "https://syl.onrender.com"  # type: ignore[arg-type]


def test_an_explicit_public_base_url_wins_over_renders(monkeypatch):
    from app.config import Settings
    from tests.conftest import VALID

    env = {k: v for k, v in VALID.items() if k != "public_base_url"}
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://syl.onrender.com")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://logs.example.org")
    assert Settings(_env_file=None, **env).public_base_url == "https://logs.example.org"  # type: ignore[arg-type]


def test_in_production_the_api_docs_paths_are_not_the_app_either(make_settings, dist):
    client = TestClient(create_app(make_settings(**PROD, web_dist_dir=dist)))
    for path in ("/docs", "/openapi.json"):
        r = client.get(path)
        assert r.status_code == 404 and "Speak Your Log" not in r.text, path


def test_the_exact_origin_check_uses_the_render_url(monkeypatch):
    from app.config import Settings
    from tests.conftest import VALID

    env = {k: v for k, v in VALID.items() if k != "public_base_url"}
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://syl.onrender.com")
    s = Settings(_env_file=None, **{**env, "app_env": "production"})  # type: ignore[arg-type]
    assert s.allowed_origins == frozenset({"https://syl.onrender.com"})

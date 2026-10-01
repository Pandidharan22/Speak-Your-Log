import base64

import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.conftest import KEY_A, KEY_B, KEY_C, PROD, VALID


def test_valid_settings_load(make_settings):
    s = make_settings()
    assert s.app_env == "test"
    assert s.session_hmac_key_bytes == b"a" * 32
    assert s.agent_job_secret_bytes == b"b" * 32
    assert s.token_enc_keys == {"v1": b"c" * 32}
    assert s.proof_mcp_url.startswith("https://")


@pytest.mark.parametrize(
    "missing",
    [
        "database_url",
        "session_hmac_key",
        "agent_job_secret",
        "token_enc_key_v1",
        "token_enc_key_id",
        "livekit_url",
        "livekit_api_key",
        "livekit_api_secret",
    ],
)
def test_missing_required_setting_fails_fast(missing):
    values = {k: v for k, v in VALID.items() if k != missing}
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **values)  # type: ignore[arg-type]
    assert missing in str(exc.value)


@pytest.mark.parametrize("break_field", ["agent_job_secret", "token_enc_key_v1", "database_url"])
def test_validation_errors_never_echo_secret_values(break_field):
    # Regression: pydantic's default error text prints `input_value={...}` — a fragment of the
    # real secrets — into boot logs when any one setting is wrong or missing.
    values = {k: v for k, v in VALID.items() if k != break_field}
    if break_field == "token_enc_key_v1":
        values[break_field] = "short"
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **values)  # type: ignore[arg-type]
    # str/repr is what tracebacks and boot logs print. (`.json()`/`.errors()` still carry the
    # input by design; never log those for Settings.)
    text = str(exc.value) + repr(exc.value)
    for secret in (KEY_A, KEY_B, KEY_C, "pw@pooler", "lk-secret", "short"):
        assert secret not in text


def test_settings_read_from_environment(monkeypatch):
    for key, value in VALID.items():
        monkeypatch.setenv(key.upper(), value)
    s = Settings(_env_file=None)
    assert s.token_enc_key_id == "v1"


def test_agent_only_secrets_in_dotenv_are_not_loaded(tmp_path):
    # Least privilege: a developer's shared .env also holds agent-only secrets (Gemini key).
    # The API must not load them. (Only the dotenv path can leak extras; plain env vars that
    # are not declared fields are never read.)
    lines = [f"{k.upper()}={v}" for k, v in VALID.items()] + ["GEMINI_API_KEY=should-be-ignored"]
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(lines), encoding="utf-8")
    s = Settings(_env_file=env_file)
    assert s.token_enc_key_id == "v1"  # the file was really read
    assert not hasattr(s, "gemini_api_key")
    assert "should-be-ignored" not in repr(s) + s.model_dump_json()


@pytest.mark.parametrize(
    "bad",
    [
        "not base64!!",
        "",
        base64.urlsafe_b64encode(b"x" * 16).decode(),
        base64.urlsafe_b64encode(b"x" * 33).decode(),
    ],
)
def test_malformed_or_short_keys_rejected(make_settings, bad):
    with pytest.raises(ValidationError):
        make_settings(token_enc_key_v1=bad)


def test_reusing_one_secret_for_two_purposes_is_rejected(make_settings):
    with pytest.raises(ValidationError, match="must differ"):
        make_settings(agent_job_secret=KEY_A)  # same as session_hmac_key


def test_unknown_encryption_key_id_rejected(make_settings):
    with pytest.raises(ValidationError, match="no encryption key"):
        make_settings(token_enc_key_id="v2")


@pytest.mark.parametrize("bad_id", ["V1", "v 1", "", "x" * 17, "../etc"])
def test_malformed_key_id_rejected(make_settings, bad_id):
    with pytest.raises(ValidationError):
        make_settings(token_enc_key_id=bad_id)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("database_url", "mysql://u:p@h/db"),
        ("livekit_url", "https://example.livekit.cloud"),
        ("proof_mcp_url", "http://proof.example.com/api/mcp"),
    ],
)
def test_wrong_url_schemes_rejected(make_settings, field, bad):
    with pytest.raises(ValidationError):
        make_settings(**{field: bad})


def test_production_requires_secure_websocket(make_settings):
    with pytest.raises(ValidationError, match="wss://"):
        make_settings(**PROD, livekit_url="ws://localhost:7880")
    assert make_settings(app_env="development", livekit_url="ws://localhost:7880")


def test_secrets_never_appear_in_repr_or_str(make_settings):
    s = make_settings()
    text = repr(s) + str(s) + s.model_dump_json()
    for secret in (KEY_A, KEY_B, KEY_C, "pw@pooler", "lk-secret", "lk-key"):
        assert secret not in text


def test_settings_are_immutable(make_settings):
    s = make_settings()
    with pytest.raises(ValidationError):
        s.app_env = "production"  # type: ignore[misc]


def test_production_requires_https_public_url(make_settings):
    with pytest.raises(ValidationError, match="https://"):
        make_settings(app_env="production", public_base_url="http://app.example.com")


@pytest.mark.parametrize(
    "bad", ["app.example.com", "https://app.example.com/path", "https://a b.com", "ftp://x.com", ""]
)
def test_public_base_url_must_be_a_bare_origin(make_settings, bad):
    with pytest.raises(ValidationError):
        make_settings(public_base_url=bad)


def test_trailing_slash_on_public_base_url_is_normalised(make_settings):
    assert make_settings(public_base_url="https://app.example.com/").public_base_url == (
        "https://app.example.com"
    )


def test_cookie_and_origin_policy_differs_between_dev_and_production(make_settings):
    dev, prod = make_settings(), make_settings(**PROD)
    assert (dev.cookie_name, dev.cookie_secure) == ("syl_session", False)
    assert (prod.cookie_name, prod.cookie_secure) == ("__Host-syl_session", True)
    assert "http://localhost:5173" in dev.allowed_origins  # Vite dev server
    assert prod.allowed_origins == frozenset({"https://app.example.com"})  # nothing else

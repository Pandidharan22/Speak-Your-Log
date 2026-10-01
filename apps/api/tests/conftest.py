import base64

import pytest

from app.config import Settings

# Obviously-fake, deterministic 32-byte keys. Real keys live only in .env / platform settings.
KEY_A = base64.urlsafe_b64encode(b"a" * 32).decode()
KEY_B = base64.urlsafe_b64encode(b"b" * 32).decode()
KEY_C = base64.urlsafe_b64encode(b"c" * 32).decode()

VALID = {
    "app_env": "test",
    "database_url": "postgresql://syl_app.ref:pw@pooler.example.com:6543/postgres",
    "session_hmac_key": KEY_A,
    "agent_job_secret": KEY_B,
    "token_enc_key_id": "v1",
    "token_enc_key_v1": KEY_C,
    "livekit_url": "wss://example.livekit.cloud",
    "livekit_api_key": "lk-key",
    "livekit_api_secret": "lk-secret",
}

SETTING_ENV_VARS = [k.upper() for k in VALID] + ["PROOF_MCP_URL", "GEMINI_API_KEY"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never depend on (or leak) the developer's real environment."""
    for name in SETTING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def make_settings():
    def _make(**overrides: object) -> Settings:
        return Settings(_env_file=None, **{**VALID, **overrides})  # type: ignore[arg-type]

    return _make

import base64
import os
from contextlib import contextmanager

import pytest
from dotenv import dotenv_values

from app.config import REPO_ROOT, Settings
from app.db import Database

# Captured at import time, BEFORE the autouse fixture below scrubs the environment. Integration
# tests use the developer's real DATABASE_URL (from the env or the repo-root .env); in CI there is
# none, so they skip.
REAL_DATABASE_URL = os.environ.get("DATABASE_URL") or dotenv_values(REPO_ROOT / ".env").get(
    "DATABASE_URL"
)

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
    "public_base_url": "http://localhost:8000",
}

# Production is stricter (https public URL, wss LiveKit); tests that need it spread this in.
PROD = {"app_env": "production", "public_base_url": "https://app.example.com"}

SETTING_ENV_VARS = [k.upper() for k in VALID] + [
    "PROOF_MCP_URL",
    "GEMINI_API_KEY",
    "RENDER_EXTERNAL_URL",
    "WEB_DIST_DIR",
]


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


class SharedConnDb:
    """Test double for Database: every request reuses one connection inside a rollback.

    `active` counts connections currently checked out, so a test can prove that no database
    connection is held while the app waits on an external service.
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self.active = 0

    @contextmanager
    def connection(self):
        self.active += 1
        try:
            yield self._conn
        finally:
            self.active -= 1

    def open(self) -> None: ...

    def close(self) -> None: ...

    def ping(self) -> bool:
        return True


@pytest.fixture(scope="session")
def real_db():
    if not REAL_DATABASE_URL:
        pytest.skip("no DATABASE_URL available (integration test)")
    db = Database(REAL_DATABASE_URL, max_size=3)
    db.open()
    with db.connection() as conn:
        # Safety rail: integration tests must only ever run as the limited app role.
        assert conn.execute("select current_user as u").fetchone()["u"] == "syl_app"
    yield db
    db.close()


@pytest.fixture
def conn(real_db):
    """A connection inside a transaction that is ALWAYS rolled back: tests leave no data."""
    with real_db.connection() as c, c.transaction(force_rollback=True):
        yield c

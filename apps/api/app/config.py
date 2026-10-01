"""Application settings: read once from the environment, validated at startup (fail fast).

Only variables this service needs are declared. `extra="ignore"` means a developer's local `.env`
may also hold agent-only secrets (e.g. GEMINI_API_KEY) without the API ever loading them —
least privilege: the API process simply does not know them.
"""

import base64
import binascii
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
KEY_BYTES = 32
_ORIGIN_RE = re.compile(r"^https?://[^/\s?#]+$")
# Browsers' origins during local development (Vite dev server and the API itself).
_DEV_ORIGINS = frozenset(
    {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }
)


def _decode_key(name: str, value: SecretStr) -> bytes:
    """Decode a urlsafe-base64 secret and require exactly 32 bytes (256 bits)."""
    try:
        key = base64.b64decode(value.get_secret_value().strip(), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{name} must be urlsafe base64") from exc
    if len(key) != KEY_BYTES:
        raise ValueError(f"{name} must decode to exactly {KEY_BYTES} bytes, got {len(key)}")
    return key


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",  # local dev only; real env vars always win
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        # Validation errors must never echo input values: on a bad config they would print
        # fragments of real secrets into boot logs.
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"] = "development"

    # Postgres: the limited `syl_app` role via the Supabase transaction pooler.
    database_url: SecretStr
    # Hard ceiling of 5: the project is shared with another live app and the role is limited to 10.
    db_pool_max_size: int = Field(default=3, ge=1, le=5)

    # One key per purpose (key separation): never reuse a secret across roles.
    session_hmac_key: SecretStr  # hashes device-session cookies
    agent_job_secret: SecretStr  # signs the per-interview job token given to the agent
    token_enc_key_id: str = Field(pattern=r"^[a-z0-9_]{1,16}$")  # which key encrypts NEW tokens
    token_enc_key_v1: SecretStr  # AES-256-GCM key for the Proof-token vault

    livekit_url: str
    livekit_api_key: SecretStr
    livekit_api_secret: SecretStr

    proof_mcp_url: str = "https://proof.zeromaintenanceengineer.in/api/mcp"

    # The one public origin the browser app is served from (scheme://host[:port], no path).
    # State-changing /api requests must carry exactly this Origin (CSRF defence in depth).
    public_base_url: str = "http://localhost:8000"

    @field_validator("database_url")
    @classmethod
    def _database_scheme(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().startswith(("postgresql://", "postgres://")):
            raise ValueError("database_url must be a postgresql:// URL")
        return v

    @field_validator("livekit_url")
    @classmethod
    def _livekit_scheme(cls, v: str) -> str:
        if not v.startswith(("wss://", "ws://")):
            raise ValueError("livekit_url must start with wss:// (or ws:// for local dev)")
        return v

    @field_validator("public_base_url")
    @classmethod
    def _public_origin(cls, v: str) -> str:
        v = v.rstrip("/")
        if not _ORIGIN_RE.fullmatch(v):
            raise ValueError("public_base_url must look like https://host[:port] with no path")
        return v

    @field_validator("proof_mcp_url")
    @classmethod
    def _proof_scheme(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("proof_mcp_url must be https://")
        return v

    @model_validator(mode="after")
    def _validate_secrets(self) -> "Settings":
        # Fail at boot, not at first request, if any key is malformed or reused.
        keys = {
            "session_hmac_key": _decode_key("session_hmac_key", self.session_hmac_key),
            "agent_job_secret": _decode_key("agent_job_secret", self.agent_job_secret),
            "token_enc_key_v1": _decode_key("token_enc_key_v1", self.token_enc_key_v1),
        }
        if len(set(keys.values())) != len(keys):
            raise ValueError("session_hmac_key, agent_job_secret and token_enc_key_v1 must differ")
        if self.token_enc_key_id not in self.token_enc_keys:
            raise ValueError(f"no encryption key configured for id {self.token_enc_key_id!r}")
        if self.app_env == "production":
            if not self.livekit_url.startswith("wss://"):
                raise ValueError("livekit_url must be wss:// in production")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("public_base_url must be https:// in production")
        return self

    @property
    def cookie_secure(self) -> bool:
        return self.app_env == "production"

    @property
    def cookie_name(self) -> str:
        # The __Host- prefix makes browsers require Secure + Path=/ and reject a Domain
        # attribute, so a sibling subdomain cannot plant or overwrite our session cookie.
        return "__Host-syl_session" if self.cookie_secure else "syl_session"

    @property
    def allowed_origins(self) -> frozenset[str]:
        if self.app_env == "production":
            return frozenset({self.public_base_url})
        return frozenset({self.public_base_url}) | _DEV_ORIGINS

    @property
    def token_enc_keys(self) -> dict[str, bytes]:
        """All known vault keys by id. Add `token_enc_key_v2` etc. here when rotating."""
        return {"v1": _decode_key("token_enc_key_v1", self.token_enc_key_v1)}

    @property
    def session_hmac_key_bytes(self) -> bytes:
        return _decode_key("session_hmac_key", self.session_hmac_key)

    @property
    def agent_job_secret_bytes(self) -> bytes:
        return _decode_key("agent_job_secret", self.agent_job_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment

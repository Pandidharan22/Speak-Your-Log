"""Agent settings from the environment. The agent holds ONLY what it needs: the Gemini key and the
model choice (LiveKit's own credentials are read by the LiveKit SDK from LIVEKIT_*). It never loads
the database URL, the vault key, or any Proof token (docs/Architecture.md §3)."""

import os
from dataclasses import dataclass, field

DEFAULT_LIVE_MODEL = "gemini-3.1-flash-live-preview"  # fallback: gemini-3.8-live (see ADR-001)
DEFAULT_VOICE = "Kore"


class SettingsError(Exception):
    """A configuration problem. The message names the variable, never its value."""


@dataclass(frozen=True)
class AgentSettings:
    gemini_api_key: str = field(repr=False)
    live_model: str = DEFAULT_LIVE_MODEL
    voice: str = DEFAULT_VOICE

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AgentSettings":
        env = os.environ if env is None else env
        key = (env.get("GEMINI_API_KEY") or "").strip()
        if not key:
            raise SettingsError("GEMINI_API_KEY is not set")
        return cls(
            gemini_api_key=key,
            live_model=(env.get("GEMINI_LIVE_MODEL") or DEFAULT_LIVE_MODEL).strip(),
            voice=(env.get("GEMINI_VOICE") or DEFAULT_VOICE).strip(),
        )

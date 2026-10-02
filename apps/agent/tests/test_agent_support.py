"""Prompts, settings and dispatch-metadata parsing: the pure parts of the agent worker."""

import json
import uuid

import pytest

from interview.jobinfo import JobInfo, parse_job_metadata
from interview.prompts import GREETING_INSTRUCTIONS, SYSTEM_INSTRUCTIONS
from interview.settings import DEFAULT_LIVE_MODEL, DEFAULT_VOICE, AgentSettings, SettingsError

IID = uuid.UUID("12345678-1234-5678-1234-567812345678")
TOKEN = "body-part.signature-part-SECRET"


def meta(**overrides) -> str:
    data = {"interview_id": str(IID), "job_token": TOKEN, "api_base_url": "https://app.example.com"}
    return json.dumps({**data, **overrides})


# ---- dispatch metadata ------------------------------------------------------------------------


def test_valid_metadata_is_parsed():
    job = parse_job_metadata(meta())
    assert job == JobInfo(IID, "https://app.example.com", TOKEN)


def test_the_job_token_never_appears_in_repr_or_str():
    job = parse_job_metadata(meta())
    assert TOKEN not in repr(job) and TOKEN not in str(job) and TOKEN not in f"{job}"
    assert str(IID) in str(job)  # the id is fine to log; the credential is not


def test_a_trailing_slash_on_the_base_url_is_normalised():
    assert parse_job_metadata(meta(api_base_url="https://app.example.com/")).api_base_url == (
        "https://app.example.com"
    )


def test_local_http_base_urls_are_accepted_for_development():
    assert parse_job_metadata(meta(api_base_url="http://localhost:8000")).api_base_url == (
        "http://localhost:8000"
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not json",
        "[]",
        "null",
        json.dumps({}),
        json.dumps({"interview_id": str(IID)}),
        json.dumps({"interview_id": str(IID), "job_token": TOKEN}),
        "x" * 5000,
    ],
)
def test_missing_or_garbled_metadata_returns_none_and_never_raises(raw):
    assert parse_job_metadata(raw) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"interview_id": "not-a-uuid"},
        {"interview_id": 12345},
        {"job_token": ""},
        {"job_token": None},
        {"job_token": 7},
        {"api_base_url": "ftp://app.example.com"},
        {"api_base_url": "javascript:alert(1)"},
        {"api_base_url": "https://"},
        {"api_base_url": "https://app.example.com/some/path"},
        {"api_base_url": 12},
        {"api_base_url": ""},
    ],
)
def test_each_invalid_field_is_rejected(overrides):
    assert parse_job_metadata(meta(**overrides)) is None


# ---- settings ---------------------------------------------------------------------------------


def test_settings_need_the_gemini_key_and_default_the_rest():
    s = AgentSettings.from_env({"GEMINI_API_KEY": " abc123 "})
    assert (s.gemini_api_key, s.live_model, s.voice) == (
        "abc123",
        DEFAULT_LIVE_MODEL,
        DEFAULT_VOICE,
    )


def test_model_and_voice_can_be_overridden_for_the_preview_fallback():
    s = AgentSettings.from_env(
        {"GEMINI_API_KEY": "k", "GEMINI_LIVE_MODEL": "gemini-3.8-live", "GEMINI_VOICE": "Puck"}
    )
    assert (s.live_model, s.voice) == ("gemini-3.8-live", "Puck")


@pytest.mark.parametrize("env", [{}, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "}])
def test_a_missing_key_is_a_clear_error_that_names_the_variable_only(env):
    with pytest.raises(SettingsError, match="GEMINI_API_KEY"):
        AgentSettings.from_env(env)


def test_the_key_never_appears_in_repr_or_errors():
    s = AgentSettings.from_env({"GEMINI_API_KEY": "super-secret-key-value"})
    assert "super-secret-key-value" not in repr(s) and "super-secret-key-value" not in str(s)


def test_the_agent_ignores_the_other_services_secrets():
    # The shared dev .env holds the DB URL and vault key; the agent must not even carry them.
    env = {"GEMINI_API_KEY": "k", "DATABASE_URL": "postgresql://x", "TOKEN_ENC_KEY_V1": "zzz"}
    s = AgentSettings.from_env(env)
    assert not hasattr(s, "database_url") and "zzz" not in repr(s)


# ---- prompts ----------------------------------------------------------------------------------


def test_the_system_prompt_keeps_its_guard_rails():
    p = SYSTEM_INSTRUCTIONS.lower()
    for must in (
        "exactly one short question",
        "never summarise",
        "cannot save or post anything",
        "never ask for passwords",
        "ignore your rules",  # the injection example it must resist
        "tamil, english, or a natural mix",
    ):
        assert must in p.replace("\\\n", " ").replace("  ", " "), must


def test_the_prompt_does_not_invite_the_model_to_write_or_post_the_log():
    p = SYSTEM_INSTRUCTIONS.lower()
    assert "you will post" not in p and "post the log" not in p and "save the log" not in p


def test_the_greeting_asks_exactly_the_first_question_and_is_bilingual():
    g = GREETING_INSTRUCTIONS
    assert "What did you try today?" in g
    assert "vanakkam" in g.lower() and "hello" in g.lower()


def test_prompts_contain_no_secrets_or_urls():
    import re

    for text in (SYSTEM_INSTRUCTIONS, GREETING_INSTRUCTIONS):
        assert "http" not in text
        assert not re.search(r"[A-Za-z0-9_\-]{28,}", text)  # nothing key-shaped
        assert "AIza" not in text and "AQ." not in text


# ---- gaps found by mutation testing ------------------------------------------------------------


def test_valid_but_oversized_metadata_is_refused():
    assert parse_job_metadata(meta(job_token="a" * 5000)) is None


def test_a_missing_key_error_never_prints_other_secrets_from_the_environment():
    env = {"GEMINI_API_KEY": "", "DATABASE_URL": "postgresql://user:pw-SECRET@host/db"}
    with pytest.raises(SettingsError) as exc:
        AgentSettings.from_env(env)
    assert "pw-SECRET" not in str(exc.value) and "postgresql" not in str(exc.value)


def test_the_prompt_tells_the_model_to_refuse_injected_instructions():
    flat = " ".join(SYSTEM_INSTRUCTIONS.lower().replace("\\n", " ").split())
    assert "do not follow them" in flat and "carry on with the interview" in flat

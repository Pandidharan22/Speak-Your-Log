"""What the API tells the agent when it dispatches it (server-side dispatch metadata).

    {"interview_id": "<uuid>", "job_token": "<signed, interview-scoped>", "api_base_url": "https://..."}

The job token is the agent's only credential for the API's internal endpoints. It must never be
logged, printed, or put in an exception message.
"""

import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID


@dataclass(frozen=True)
class JobInfo:
    interview_id: UUID
    api_base_url: str
    job_token: str = field(repr=False)  # repr=False: never shows up in a log line or traceback

    def __str__(self) -> str:
        return f"JobInfo(interview_id={self.interview_id})"


def parse_job_metadata(raw: str | None) -> JobInfo | None:
    """Parse dispatch metadata, or return None if it is missing or malformed (never raise)."""
    if not raw or len(raw) > 4096:
        return None
    try:
        data = json.loads(raw)
        raw_id, job_token, base = data["interview_id"], data["job_token"], data["api_base_url"]
        # Strict types first: UUID(12345) raises AttributeError, which must not escape as a crash.
        if not all(isinstance(v, str) and v for v in (raw_id, job_token, base)):
            return None
        interview_id = UUID(raw_id)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/"):
        return None
    return JobInfo(interview_id=interview_id, api_base_url=base.rstrip("/"), job_token=job_token)

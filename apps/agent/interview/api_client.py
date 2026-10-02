"""The agent's client for the API's internal endpoints (apps/api/app/routers/internal.py).

Authenticated with the per-interview job token only. Rules this client follows:

  * idempotent calls (state, answers, preview, status) are retried a couple of times on network
    errors;
  * the POST that publishes is NEVER retried. If its outcome is unclear (timeout, 5xx), the client
    asks the API what state the interview ended in and reports what it finds; if even that fails,
    the answer is UNKNOWN, which the flow treats as "never retry";
  * the job token and the student's words never appear in logs or exception messages.
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from interview.flow import PostResult
from interview.jobinfo import JobInfo

log = logging.getLogger("syl.api")

_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)  # post waits on Proof
_RETRIES = 2  # for idempotent calls only
_RESOLVE_ATTEMPTS = 4
_RESOLVE_DELAY_S = 1.5


class ApiError(Exception):
    """Base class. Messages carry no token and no student text."""


class ApiAuthError(ApiError):
    """The API rejected the job token (expired, wrong interview)."""


class ApiUnavailable(ApiError):
    """The API could not be reached or answered unexpectedly."""


@dataclass(frozen=True, slots=True)
class Preview:
    content: str
    why: str


@dataclass(frozen=True, slots=True)
class PostOutcome:
    result: PostResult
    url: str | None = None
    reason: str | None = None


_RESULT_MAP = {
    "posted": PostResult.POSTED,
    "retryable": PostResult.RETRYABLE,
    "rejected": PostResult.REJECTED,
    "unknown": PostResult.UNKNOWN,
    "not_confirming": PostResult.REJECTED,  # nothing was posted and this interview cannot be
}
# What a final interview state means for a post whose reply we never saw.
_STATE_MAP = {
    "posted": PostResult.POSTED,
    "confirming": PostResult.RETRYABLE,  # sent back to confirming: definitely not applied
    "failed": PostResult.REJECTED,
    "post_unknown": PostResult.UNKNOWN,
}


class ApiClient:
    def __init__(self, job: JobInfo, *, http: httpx.AsyncClient | None = None) -> None:
        self._base = f"{job.api_base_url}/internal/interviews/{job.interview_id}"
        self._headers = {"Authorization": f"Bearer {job.job_token}"}
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- idempotent calls -------------------------------------------------------------------

    async def set_state(self, state: str) -> bool:
        r = await self._call("POST", "/state", {"state": state}, retry=True)
        return r.status_code == 200

    async def put_answer(self, key: str, text: str) -> bool:
        r = await self._call("PUT", f"/answers/{key}", {"text": text}, retry=True)
        return r.status_code == 200

    async def clear_answers(self) -> bool:
        return (await self._call("DELETE", "/answers", None, retry=True)).status_code == 200

    async def preview(self) -> Preview | None:
        r = await self._call("GET", "/preview", None, retry=True)
        if r.status_code != 200:
            return None
        body = r.json()
        return Preview(content=body["content"], why=body["why"])

    async def status(self) -> str | None:
        r = await self._call("GET", "", None, retry=True)
        return r.json().get("state") if r.status_code == 200 else None

    # ---- the one call that publishes --------------------------------------------------------

    async def post(self, confirmation_text: str) -> PostOutcome:
        """Report a validated confirmation. Never retried; ambiguity is resolved by asking."""
        try:
            r = await self._call(
                "POST", "/post", {"confirmation_text": confirmation_text}, retry=False
            )
        except ApiUnavailable:
            return await self._resolve_unknown()
        if r.status_code != 200:
            return await self._resolve_unknown()
        body = r.json()
        result = body.get("result")
        if result == "in_progress":  # another request is mid-post: wait for it to resolve
            return await self._resolve_unknown()
        if result not in _RESULT_MAP:
            return await self._resolve_unknown()
        return PostOutcome(_RESULT_MAP[result], url=body.get("url"), reason=body.get("reason"))

    async def _resolve_unknown(self) -> PostOutcome:
        """The reply was lost. Ask where the interview ended up; never guess "not posted"."""
        for attempt in range(_RESOLVE_ATTEMPTS):
            try:
                state = await self.status()
            except ApiError:
                state = None
            if state in _STATE_MAP:
                return PostOutcome(_STATE_MAP[state])
            if attempt < _RESOLVE_ATTEMPTS - 1:
                await asyncio.sleep(_RESOLVE_DELAY_S)  # `posting`: still in flight
        return PostOutcome(PostResult.UNKNOWN)

    # ---- plumbing ---------------------------------------------------------------------------

    async def _call(
        self, method: str, path: str, body: dict | None, *, retry: bool
    ) -> httpx.Response:
        attempts = (_RETRIES + 1) if retry else 1
        for attempt in range(attempts):
            try:
                r = await self._http.request(
                    method, self._base + path, json=body, headers=self._headers
                )
            except httpx.RequestError as exc:
                log.warning(
                    "api_call_failed method=%s path=%s error=%s", method, path, type(exc).__name__
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise ApiUnavailable from None
            if r.status_code == 401:
                raise ApiAuthError from None
            if r.status_code >= 500 and attempt + 1 < attempts:
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            if r.status_code >= 500:
                raise ApiUnavailable from None
            return r
        raise ApiUnavailable  # pragma: no cover - the loop always returns or raises

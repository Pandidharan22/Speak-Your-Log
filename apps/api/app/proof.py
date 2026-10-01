"""Client for Proof's log connector (JSON-RPC 2.0 over HTTPS, `POST /api/mcp`).

The one rule that shapes this module: **a post is public and Proof gives us no idempotency key,
so we must never retry a post whose outcome we cannot be sure of.** Every failure therefore maps to
exactly one of two kinds:

  * *definitive* — the request was certainly not applied (bad token, refused, rate limited,
    could not connect). Safe to tell the user and to retry later.
  * *ambiguous*  — the request may have been applied (timeout after sending, 5xx, garbled reply).
    `PostOutcomeUnknown`: never retried automatically; the user is told to check their profile.

Observed behaviour (spike + probes, see spike/RESULTS.md): a bad/absent token is HTTP 401 with
JSON-RPC code -32001; protocol/tool errors are HTTP 200 with a JSON-RPC `error`; GET is 405.
The *successful* post_log response shape has not been observed (it would need a real public post),
so success parsing is deliberately tolerant — a post is treated as successful whenever Proof says
so, even if we cannot find the URL in the reply.
"""

import itertools
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

# Verbs accepted by post_log (from `tools/list`, recorded in spike/RESULTS.md).
VERBS = frozenset(
    {
        "built", "stuck", "mistake", "thinking", "decided", "nothing", "quiet", "changed",
        "flagged", "thank", "learned", "freely", "assumed", "noticed", "ask", "wonder",
        "figure_out", "interview",
    }
)  # fmt: skip
MAX_FIELD_CHARS = 4000  # our own guard; we refuse rather than silently truncate someone's words
MAX_RESPONSE_BYTES = 1_000_000
_URL_RE = re.compile(r"https://[^\s<>\"')\]]+")
_TRAILING = ".,;:!?"

_POST_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
_LIST_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


# ---- errors ---------------------------------------------------------------------------------


class ProofError(Exception):
    """Base. Messages never contain the token."""

    #: True when we are certain the request had no effect on Proof.
    definitive_not_applied = True


class TokenRejected(ProofError):
    """Proof says the token is missing, wrong, or revoked. The user must reconnect."""


class RateLimited(ProofError):
    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__("Proof rate limit reached")
        self.retry_after = retry_after


class PostRejected(ProofError):
    """Proof understood the request and refused it (validation, tool error)."""

    def __init__(self, detail: str) -> None:
        super().__init__("Proof refused the request")
        self.detail = detail[:200]  # third-party text: bounded, shown to the user at most


class ProofUnavailable(ProofError):
    """Could not reach Proof, or it was not in a state to take the request. Nothing was applied."""


class PostOutcomeUnknown(ProofError):
    """The request MAY have been applied. Never retry automatically."""

    definitive_not_applied = False


@dataclass(frozen=True, slots=True)
class PostResult:
    url: str | None  # None if Proof's reply did not contain a recognisable log URL


# ---- client ---------------------------------------------------------------------------------


class ProofClient:
    def __init__(self, base_url: str, *, http: httpx.Client | None = None) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Proof URL must be https://")
        self._url = base_url
        self._host = urlsplit(base_url).hostname or ""
        # follow_redirects=False: never forward the bearer token to a redirect target.
        self._http = http or httpx.Client(follow_redirects=False, verify=True)
        self._ids = itertools.count(1)

    def close(self) -> None:
        self._http.close()

    # -- public API --

    def validate_token(self, token: SecretStr) -> None:
        """Raise unless `token` is accepted by Proof AND the account can use post_log.

        Read-only (`tools/list`), so a failure here never needs the ambiguity handling of a post.
        """
        try:
            body = self._rpc(token, "tools/list", None, _LIST_TIMEOUT)
        except PostOutcomeUnknown as exc:  # for a read, "unknown" simply means unavailable
            raise ProofUnavailable("Proof is not responding properly") from exc
        tools = (body.get("result") or {}).get("tools")
        names = (
            {t.get("name") for t in tools if isinstance(t, dict)}
            if isinstance(tools, list)
            else set()
        )
        if "post_log" not in names:
            raise PostRejected("this token cannot post logs")

    def post_log(
        self,
        token: SecretStr,
        *,
        verb: str,
        content: str,
        why: str | None = None,
        evidence_url: str | None = None,
    ) -> PostResult:
        """Publish ONE log. Not idempotent and never retried — see the module docstring."""
        arguments = self._validated_arguments(verb, content, why, evidence_url)
        body = self._rpc(
            token, "tools/call", {"name": "post_log", "arguments": arguments}, _POST_TIMEOUT
        )
        result = body.get("result")
        if not isinstance(result, dict):
            # HTTP 200 but no result and no error: we cannot tell what happened.
            raise PostOutcomeUnknown("Proof replied without a result")
        if result.get("isError"):
            raise PostRejected(_redact(_text_of(result), token) or "Proof reported an error")
        return PostResult(url=self._find_url(result))

    # -- internals --

    @staticmethod
    def _validated_arguments(
        verb: str, content: str, why: str | None, evidence_url: str | None
    ) -> dict[str, str]:
        if verb not in VERBS:
            raise PostRejected(f"unknown verb {verb!r}")
        if not content.strip():
            raise PostRejected("content is empty")
        if verb == "decided" and not (why and why.strip()):
            raise PostRejected("'why' is required when verb is 'decided'")
        for name, value in (("content", content), ("why", why), ("evidence_url", evidence_url)):
            if value is not None and len(value) > MAX_FIELD_CHARS:
                raise PostRejected(f"{name} is too long ({len(value)} > {MAX_FIELD_CHARS})")
        if evidence_url is not None and not evidence_url.startswith(("https://", "http://")):
            raise PostRejected("evidence_url must be an http(s) link")
        args = {"verb": verb, "content": content}
        if why:
            args["why"] = why
        if evidence_url:
            args["evidence_url"] = evidence_url
        return args

    def _rpc(
        self, token: SecretStr, method: str, params: dict | None, timeout: httpx.Timeout
    ) -> dict:
        payload: dict = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            payload["params"] = params
        headers = {
            "Authorization": f"Bearer {token.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "speak-your-log/0.1",
        }
        try:
            response = self._http.post(
                self._url,
                json=payload,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,  # per request, so an injected client cannot override it
            )
        # Failures BEFORE the request could have been processed: definitely not applied.
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise ProofUnavailable("could not connect to Proof") from _scrub(exc)
        # Everything else (timeouts mid-flight, resets, undecodable replies): Proof may have acted.
        except httpx.RequestError as exc:
            raise PostOutcomeUnknown(
                "lost contact with Proof while the request was in flight"
            ) from _scrub(exc)
        return self._interpret(response, token)

    def _interpret(self, response: httpx.Response, token: SecretStr) -> dict:
        status = response.status_code
        if status in (401, 403):
            raise TokenRejected("Proof rejected the token")
        if status == 429:
            raise RateLimited(_retry_after(response))
        if status in (408,) or 300 <= status < 400:
            raise ProofUnavailable(f"Proof returned HTTP {status}")  # not processed
        if status >= 500:
            raise PostOutcomeUnknown(f"Proof returned HTTP {status}")  # may have been processed
        if status >= 400:
            raise PostRejected(f"Proof returned HTTP {status}")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise PostOutcomeUnknown("Proof reply was unexpectedly large")
        try:
            body = response.json()
        except ValueError:
            raise PostOutcomeUnknown("Proof reply was not valid JSON") from None
        if not isinstance(body, dict):
            raise PostOutcomeUnknown("Proof reply had an unexpected shape")
        error = body.get("error")
        if error is not None:
            self._raise_for_rpc_error(error, token)
        return body

    @staticmethod
    def _raise_for_rpc_error(error: object, token: SecretStr) -> None:
        code = error.get("code") if isinstance(error, dict) else None
        message = _redact(str(error.get("message", "")), token) if isinstance(error, dict) else ""
        if code == -32001:
            raise TokenRejected("Proof rejected the token")
        if code in (-32600, -32601, -32602, -32700):  # request/method/params/parse: not executed
            raise PostRejected(message or f"JSON-RPC error {code}")
        # -32603 (internal error) or anything unfamiliar: the server may have acted before failing.
        raise PostOutcomeUnknown(f"Proof returned JSON-RPC error {code}")

    def _find_url(self, result: dict) -> str | None:
        """Best-effort: the log URL, only if it points at Proof itself."""
        candidates: list[str] = []
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            candidates += [str(v) for k, v in structured.items() if "url" in k.lower()]
        candidates.append(_text_of(result))
        for text in candidates:
            for match in _URL_RE.findall(text):
                url = match.rstrip(_TRAILING)
                host = urlsplit(url).hostname or ""
                if host == self._host or host.endswith("." + self._host):
                    return url
        return None

    def __repr__(self) -> str:
        return f"ProofClient(host={self._host!r})"


# ---- helpers --------------------------------------------------------------------------------


def _text_of(result: dict) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    return " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))


def _redact(text: str, token: SecretStr) -> str:
    """Third-party text may echo what we sent; never let it carry the token onward."""
    secret = token.get_secret_value()
    return text.replace(secret, "[redacted]") if secret else text


def _retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after", "")
    return int(raw) if raw.isdigit() else None


def _scrub(exc: Exception) -> Exception:
    """Drop the original exception's text: httpx messages can echo URLs/headers."""
    return type(exc)(type(exc).__name__)

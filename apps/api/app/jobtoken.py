"""Per-interview job token: how the voice agent proves "I was started for interview X".

    token = base64url(json {"iid": <interview id>, "exp": <unix seconds>}) + "." + base64url(mac)
    mac   = HMAC-SHA256(AGENT_JOB_SECRET, domain label + the first part)

It is minted by the API and handed to the agent through LiveKit's *server-side* dispatch metadata,
never through the browser's room token: if the browser held it, the student's own page could call
the internal endpoints and skip the spoken "yes". It authorises exactly one interview and expires.
The HMAC key (AGENT_JOB_SECRET) is used for nothing else.
"""

import base64
import binascii
import hashlib
import hmac
import json
import time
from datetime import timedelta
from uuid import UUID

_DOMAIN = b"syl-job-token-v1."
DEFAULT_TTL = timedelta(minutes=20)  # an interview is capped at 5 minutes; this is generous
_MAX_TOKEN_CHARS = 512


class InvalidJobToken(Exception):
    """Deliberately says nothing about *why*: bad format, bad signature and expiry look the same."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)


def _sign(secret: bytes, body: str) -> bytes:
    return hmac.new(secret, _DOMAIN + body.encode("ascii"), hashlib.sha256).digest()


def sign_job_token(
    secret: bytes, interview_id: UUID, *, ttl: timedelta = DEFAULT_TTL, now: float | None = None
) -> str:
    expires = int((time.time() if now is None else now) + ttl.total_seconds())
    payload = json.dumps({"iid": str(interview_id), "exp": expires}, separators=(",", ":"))
    body = _b64(payload.encode("utf-8"))
    return f"{body}.{_b64(_sign(secret, body))}"


def verify_job_token(secret: bytes, token: str, *, now: float | None = None) -> UUID:
    """Return the interview id the token authorises, or raise InvalidJobToken."""
    try:
        if not isinstance(token, str) or len(token) > _MAX_TOKEN_CHARS or token.count(".") != 1:
            raise InvalidJobToken
        body, signature = token.split(".")
        if not hmac.compare_digest(_unb64(signature), _sign(secret, body)):
            raise InvalidJobToken
        payload = json.loads(_unb64(body))
        expires, iid = payload["exp"], payload["iid"]
        # Strict types: a correctly signed but oddly shaped payload must be refused, not coerced.
        if type(expires) is not int or not isinstance(iid, str):
            raise InvalidJobToken
        if expires < (time.time() if now is None else now):
            raise InvalidJobToken
        return UUID(iid)
    except InvalidJobToken:
        raise InvalidJobToken from None
    except (ValueError, KeyError, TypeError, AttributeError, binascii.Error, UnicodeError):
        raise InvalidJobToken from None

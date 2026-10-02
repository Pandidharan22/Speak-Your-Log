import base64
import json
import time
import uuid
from datetime import timedelta

import pytest

from app.jobtoken import InvalidJobToken, sign_job_token, verify_job_token

SECRET = b"s" * 32
OTHER_SECRET = b"t" * 32
IID = uuid.UUID("12345678-1234-5678-1234-567812345678")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_roundtrip_returns_the_authorised_interview_id():
    assert verify_job_token(SECRET, sign_job_token(SECRET, IID)) == IID


def test_token_has_two_parts_and_does_not_contain_the_secret():
    token = sign_job_token(SECRET, IID)
    assert token.count(".") == 1
    assert SECRET.decode() not in token
    assert str(IID).encode() in base64.urlsafe_b64decode(token.split(".")[0] + "==")  # readable id


def test_tokens_differ_per_interview():
    assert sign_job_token(SECRET, IID) != sign_job_token(SECRET, uuid.uuid4())


def test_expiry_is_enforced():
    now = time.time()
    token = sign_job_token(SECRET, IID, ttl=timedelta(minutes=20), now=now)
    assert verify_job_token(SECRET, token, now=now + 19 * 60) == IID
    with pytest.raises(InvalidJobToken):
        verify_job_token(SECRET, token, now=now + 21 * 60)


def test_a_token_that_is_already_expired_is_rejected():
    token = sign_job_token(SECRET, IID, ttl=timedelta(seconds=-1))
    with pytest.raises(InvalidJobToken):
        verify_job_token(SECRET, token)


def test_wrong_secret_is_rejected():
    with pytest.raises(InvalidJobToken):
        verify_job_token(OTHER_SECRET, sign_job_token(SECRET, IID))


def test_changing_the_interview_id_breaks_the_signature():
    body, sig = sign_job_token(SECRET, IID).split(".")
    forged_payload = json.loads(base64.urlsafe_b64decode(body + "=="))
    forged_payload["iid"] = str(uuid.uuid4())
    forged = b64(json.dumps(forged_payload, separators=(",", ":")).encode()) + "." + sig
    with pytest.raises(InvalidJobToken):
        verify_job_token(SECRET, forged)


def test_extending_the_expiry_breaks_the_signature():
    body, sig = sign_job_token(SECRET, IID, ttl=timedelta(seconds=1)).split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=="))
    payload["exp"] += 10_000_000
    forged = b64(json.dumps(payload, separators=(",", ":")).encode()) + "." + sig
    with pytest.raises(InvalidJobToken):
        verify_job_token(SECRET, forged)


def test_flipping_any_character_of_a_token_is_detected():
    token = sign_job_token(SECRET, IID)
    for i in range(0, len(token), 3):  # every third position keeps this fast
        mutated = token[:i] + ("A" if token[i] != "A" else "B") + token[i + 1 :]
        with pytest.raises(InvalidJobToken):
            verify_job_token(SECRET, mutated)


@pytest.mark.parametrize(
    "junk",
    ["", ".", "a.b.c", "nodot", "....", "é.ü", "x" * 600, " ", "a.", ".b", "!!!.@@@", "e30.e30"],
)
def test_junk_never_verifies_and_never_crashes(junk):
    with pytest.raises(InvalidJobToken):
        verify_job_token(SECRET, junk)


@pytest.mark.parametrize("junk", [None, 123, b"bytes", ["a"], {"a": 1}])
def test_non_string_input_is_rejected_cleanly(junk):
    with pytest.raises(InvalidJobToken):
        verify_job_token(SECRET, junk)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [
        {"iid": "not-a-uuid", "exp": 9999999999},
        {"iid": str(IID)},
        {"exp": 9999999999},
        {"iid": str(IID), "exp": "soon"},
        {"iid": str(IID), "exp": 9999999999.5},  # a float expiry: strict types, no coercion
        {"iid": str(IID), "exp": None},
        {"iid": 12345, "exp": 9999999999},
        [],
        "just a string",
    ],
)
def test_validly_signed_but_malformed_payloads_are_rejected(payload):
    # Signed with the real key, so only the structural checks stand between this and acceptance.
    import hashlib
    import hmac

    body = b64(json.dumps(payload).encode())
    mac = hmac.new(SECRET, b"syl-job-token-v1." + body.encode(), hashlib.sha256).digest()
    with pytest.raises(InvalidJobToken):
        verify_job_token(SECRET, f"{body}.{b64(mac)}")


def test_the_signature_is_bound_to_this_purpose():
    # A MAC over the same body WITHOUT our domain label (as another feature might make) is invalid.
    import hashlib
    import hmac

    body = b64(json.dumps({"iid": str(IID), "exp": 9999999999}).encode())
    plain_mac = hmac.new(SECRET, body.encode(), hashlib.sha256).digest()
    with pytest.raises(InvalidJobToken):
        verify_job_token(SECRET, f"{body}.{b64(plain_mac)}")


def test_failures_reveal_nothing_about_the_reason():
    reasons = []
    for secret, token in [
        (OTHER_SECRET, sign_job_token(SECRET, IID)),
        (SECRET, sign_job_token(SECRET, IID, ttl=timedelta(seconds=-5))),
        (SECRET, "garbage"),
    ]:
        with pytest.raises(InvalidJobToken) as exc:
            verify_job_token(secret, token)
        reasons.append((str(exc.value), exc.value.__cause__, exc.value.__suppress_context__))
    assert len(set(reasons)) == 1 and reasons[0] == ("", None, True)

"""LiveKit token claims and failure handling. No network: the browser token is pure computation."""

import time

import jwt as pyjwt
import pytest
from livekit import api

from app.livekit_service import AGENT_NAME, LiveKitService, LiveKitUnavailable, _http_url

KEY, SECRET = "lk-test-key", "lk-test-secret-" + "x" * 20
ROOM = "syl-abc123"


@pytest.fixture
def service() -> LiveKitService:
    return LiveKitService("wss://demo.livekit.cloud", KEY, SECRET)


def claims(service: LiveKitService, room: str = ROOM, identity: str = "student-1a2b"):
    jwt = service.student_token(room, identity)
    return api.TokenVerifier(KEY, SECRET).verify(jwt), jwt


def test_browser_token_joins_exactly_one_room_with_the_microphone_only(service):
    c, _ = claims(service)
    v = c.video
    assert (v.room_join, v.room) == (True, ROOM)
    assert (v.can_publish, v.can_subscribe) == (True, True)
    assert v.can_publish_data is False
    assert list(v.can_publish_sources) == ["microphone"]  # no camera, no screen share


def test_browser_token_has_no_administrative_powers(service):
    v = claims(service)[0].video
    for power in (
        "room_create",
        "room_admin",
        "room_list",
        "room_record",
        "ingress_admin",
        "hidden",
        "recorder",
    ):
        assert not getattr(v, power), power


def test_browser_token_is_short_lived_and_carries_the_given_identity(service):
    c, jwt_text = claims(service, identity="student-ffff")
    assert c.identity == "student-ffff"
    expires = pyjwt.decode(jwt_text, options={"verify_signature": False})["exp"]
    assert 0 < expires - time.time() <= 10 * 60 + 5


def test_browser_token_is_signed_with_our_secret_only(service):
    jwt = service.student_token(ROOM, "student-1")
    with pytest.raises(Exception):  # noqa: B017 - any verification failure is fine
        api.TokenVerifier(KEY, "a-different-secret-" + "y" * 20).verify(jwt)


def test_browser_token_does_not_contain_our_secret_or_the_agent_name(service):
    jwt = service.student_token(ROOM, "student-1")
    assert SECRET not in jwt and AGENT_NAME not in jwt


def test_tokens_for_different_rooms_cannot_be_swapped(service):
    a, b = claims(service, room="syl-a")[0], claims(service, room="syl-b")[0]
    assert a.video.room != b.video.room


@pytest.mark.parametrize(
    "given,expected",
    [
        ("wss://x.livekit.cloud", "https://x.livekit.cloud"),
        ("ws://localhost:7880", "http://localhost:7880"),
        ("https://already.http", "https://already.http"),
    ],
)
def test_realtime_urls_are_converted_for_the_server_api(given, expected):
    assert _http_url(given) == expected


def test_an_unreachable_server_raises_one_opaque_error():
    dead = LiveKitService("ws://127.0.0.1:1", KEY, SECRET)
    for call in (
        lambda: dead.start_interview(ROOM, "{}"),
        lambda: dead.delete_room(ROOM),
        lambda: dead.list_room_names(),
    ):
        with pytest.raises(LiveKitUnavailable) as exc:
            call()
        assert str(exc.value) == "" and exc.value.__cause__ is None  # no URL, no credentials

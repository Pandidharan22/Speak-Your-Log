"""LiveKit server-side operations: create the room, dispatch the agent, mint the browser token.

The agent is dispatched *explicitly* by name, with its job token in server-side metadata (see
app/jobtoken.py). The browser only ever receives a room token that can join one room and publish
its microphone — nothing else.
"""

import asyncio
import logging
from datetime import timedelta

from livekit import api

AGENT_NAME = "syl-interviewer"  # the agent worker registers under this name (step 3.3)
_CALL_TIMEOUT_S = 10.0
_BROWSER_TOKEN_TTL = timedelta(minutes=10)

log = logging.getLogger(__name__)


class LiveKitUnavailable(Exception):
    """A LiveKit call failed. Carries no detail: errors can echo URLs and credentials."""


def _http_url(url: str) -> str:
    return url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)


class LiveKitService:
    def __init__(self, url: str, api_key: str, api_secret: str) -> None:
        self._url = url
        self._key = api_key
        self._secret = api_secret

    # -- server calls (sync wrappers: our endpoints run in FastAPI's threadpool) --

    def start_interview(self, room_name: str, metadata: str) -> None:
        """Create the room and explicitly dispatch the interviewer agent into it."""
        self._run(self._start(room_name, metadata))

    def delete_room(self, room_name: str) -> None:
        self._run(self._delete(room_name))

    def list_room_names(self) -> list[str]:
        return self._run(self._list())

    def _run(self, coro):
        try:
            return asyncio.run(asyncio.wait_for(coro, timeout=_CALL_TIMEOUT_S))
        except Exception as exc:  # noqa: BLE001 - everything maps to one opaque error
            log.warning("livekit_call_failed error=%s", type(exc).__name__)
            raise LiveKitUnavailable from None

    async def _start(self, room_name: str, metadata: str) -> None:
        async with api.LiveKitAPI(_http_url(self._url), self._key, self._secret) as lk:
            await lk.room.create_room(
                api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=120,  # abandoned rooms close themselves
                    departure_timeout=30,
                    max_participants=3,  # the student, the agent, and one spare
                )
            )
            await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=AGENT_NAME, room=room_name, metadata=metadata
                )
            )

    async def _delete(self, room_name: str) -> None:
        async with api.LiveKitAPI(_http_url(self._url), self._key, self._secret) as lk:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))

    async def _list(self) -> list[str]:
        async with api.LiveKitAPI(_http_url(self._url), self._key, self._secret) as lk:
            result = await lk.room.list_rooms(api.ListRoomsRequest())
            return [room.name for room in result.rooms]

    # -- the browser's token (pure computation, no network) --

    def student_token(self, room_name: str, identity: str) -> str:
        grants = api.VideoGrants(
            room_join=True,
            room=room_name,  # this room only
            can_publish=True,
            can_subscribe=True,
            can_publish_data=False,
            can_publish_sources=["microphone"],  # no camera, no screen share
        )
        return (
            api.AccessToken(self._key, self._secret)
            .with_identity(identity)
            .with_name("Student")
            .with_grants(grants)
            .with_ttl(_BROWSER_TOKEN_TTL)
            .to_jwt()
        )

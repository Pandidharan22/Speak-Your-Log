"""Session lifecycle around the driver: error containment, the time cap, and ending the session.

Plain asyncio, no LiveKit imports, so it is testable with fakes. agent.py supplies the real events
(student utterance, silence, disconnect) and the `end_session` callback that closes the room.

Why it exists: a worker that never exits keeps burning free-tier agent minutes (1,000/month), and
an unexpected exception in one event must not kill an interview that is otherwise fine.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

log = logging.getLogger("syl.lifecycle")

HARD_CAP_SECONDS = 5 * 60  # SRS: an interview is ~2 minutes; nothing may run longer than this
CLOSING_GRACE_SECONDS = 1.5  # let the last words reach the student's speaker before the room closes


class _Driver(Protocol):
    @property
    def done(self) -> bool: ...
    async def on_hard_cap(self) -> None: ...


class Lifecycle:
    def __init__(
        self,
        driver: _Driver,
        end_session: Callable[[], None],
        *,
        hard_cap_s: float = HARD_CAP_SECONDS,
        grace_s: float = CLOSING_GRACE_SECONDS,
    ) -> None:
        self._driver = driver
        self._end_session = end_session
        self._hard_cap_s = hard_cap_s
        self._grace_s = grace_s
        self._ended = False
        self._cap_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    def start(self) -> None:
        self._cap_task = asyncio.create_task(self._hard_cap())

    def spawn(self, event: Callable[[], Awaitable[None]], name: str) -> None:
        """Run one driver event in the background (LiveKit callbacks are synchronous)."""
        task = asyncio.create_task(self.run(event, name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run(self, event: Callable[[], Awaitable[None]], name: str) -> None:
        try:
            await event()
        except Exception:  # noqa: BLE001 - one failed event must not kill the interview
            # Traceback only: the student's words are never logged (they are not in the message).
            log.exception("event_failed event=%s", name)
        await self._end_if_done()

    async def shutdown(self) -> None:
        """Cancel timers and pending events (called when the room is closing)."""
        self._ended = True
        pending = [t for t in (self._cap_task, *self._tasks) if t is not None]
        for task in pending:
            if task is not asyncio.current_task():
                task.cancel()

    async def _hard_cap(self) -> None:
        await asyncio.sleep(self._hard_cap_s)
        if not self._driver.done:
            log.info("hard_cap_reached")
            await self.run(self._driver.on_hard_cap, "hard_cap")

    async def _end_if_done(self) -> None:
        if self._ended or not self._driver.done:
            return
        self._ended = True
        await asyncio.sleep(self._grace_s)
        log.info("session_ending")
        self._end_session()

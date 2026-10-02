"""The lifecycle wrapper: contains failures, enforces the time cap, and ends the session once."""

import asyncio
import functools
import logging

from interview.lifecycle import Lifecycle


def sync(fn):
    """Run an async test with asyncio.run (the project has no pytest-asyncio)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


class FakeDriver:
    def __init__(self) -> None:
        self.done = False
        self.hard_capped = 0

    async def on_hard_cap(self) -> None:
        self.hard_capped += 1
        self.done = True


class Ended:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


def make(driver=None, **kw):
    driver = driver or FakeDriver()
    ended = Ended()
    return driver, ended, Lifecycle(driver, ended, grace_s=0, **kw)


@sync
async def test_a_finished_interview_ends_the_session_exactly_once():
    driver, ended, life = make()

    async def finish() -> None:
        driver.done = True

    await life.run(finish, "turn")
    await life.run(finish, "turn")  # a late event after the end must not end it twice
    assert ended.count == 1


@sync
async def test_an_unfinished_interview_keeps_the_session_open():
    _, ended, life = make()

    async def noop() -> None:
        pass

    await life.run(noop, "turn")
    assert ended.count == 0


@sync
async def test_a_failing_event_is_contained_and_logged_without_its_message(caplog):
    _, ended, life = make()

    async def boom() -> None:
        raise RuntimeError("the student said SECRET-WORDS")

    with caplog.at_level(logging.INFO):
        await life.run(boom, "turn")  # must not raise
    assert ended.count == 0
    assert "event_failed event=turn" in caplog.text
    # The traceback names the exception class; the formatted message line must not carry the words.
    assert not any("SECRET-WORDS" in r.getMessage() for r in caplog.records), (
        "the log message itself must not include exception text"
    )


@sync
async def test_the_hard_cap_ends_a_runaway_interview():
    driver, ended, life = make(hard_cap_s=0.01)
    life.start()
    await asyncio.sleep(0.1)
    assert driver.hard_capped == 1 and ended.count == 1


@sync
async def test_the_hard_cap_does_nothing_if_the_interview_already_finished():
    driver, ended, life = make(hard_cap_s=0.05)
    driver.done = True
    life.start()
    await asyncio.sleep(0.15)
    assert driver.hard_capped == 0 and ended.count == 0  # nothing left to cap


@sync
async def test_shutdown_cancels_the_timer_and_pending_events():
    driver, ended, life = make(hard_cap_s=0.05)
    life.start()
    gate = asyncio.Event()

    async def slow() -> None:
        await gate.wait()

    life.spawn(slow, "turn")
    await life.shutdown()
    await asyncio.sleep(0.15)
    assert driver.hard_capped == 0 and ended.count == 0


@sync
async def test_spawn_runs_the_event_in_the_background():
    driver, ended, life = make()
    seen = []

    async def event() -> None:
        seen.append(1)
        driver.done = True

    life.spawn(event, "turn")
    await asyncio.sleep(0.05)
    assert seen == [1] and ended.count == 1


@sync
async def test_the_session_is_not_ended_before_the_grace_period_passes():
    driver = FakeDriver()
    ended = Ended()
    life = Lifecycle(driver, ended, grace_s=0.2)
    driver.done = True

    async def noop() -> None:
        pass

    task = asyncio.create_task(life.run(noop, "turn"))
    await asyncio.sleep(0.05)
    assert ended.count == 0  # the closing words are still reaching the student
    await task
    assert ended.count == 1


def test_by_default_an_interview_is_capped_at_five_minutes():
    # The free tier has 1,000 agent-minutes a month: a forgotten session must not eat them.
    life = Lifecycle(FakeDriver(), Ended())
    assert life._hard_cap_s == 300

"""The voice interviewer worker.

Run locally (reads the repo-root .env):   python agent.py start
It registers with LiveKit under AGENT_NAME and waits. The API dispatches it into a student's room
(POST /api/interviews); it then speaks to the student through Gemini Live.

This process holds the Gemini key and LiveKit's credentials and nothing else: no database, no vault
key, no Proof token. It reaches the API only with the per-interview job token it was handed.
"""

import asyncio
import logging
import time
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import google

from interview.api_client import ApiClient
from interview.driver import InterviewDriver
from interview.jobinfo import parse_job_metadata
from interview.lifecycle import Lifecycle
from interview.settings import AgentSettings
from interview.verify import coverage, was_spoken

SPEAK_TIMEOUT_SECONDS = 90  # a read-back is ~30 s; this only guards against a hung generation
IDLE_QUIET_SECONDS = 1.2  # how long the model must be quiet before we speak over its own reply slot
IDLE_MAX_WAIT_SECONDS = 8
SILENCE_SECONDS = 30  # quiet this long (both sides) -> nudge once, then end (SRS FR-9)
AGENT_NAME = "syl-interviewer"  # must match the API's explicit dispatch (apps/api livekit_service)

# Local development only: in production the platform provides the environment. Real env vars win.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

log = logging.getLogger("syl.agent")
server = AgentServer()


class Interviewer(Agent):
    def __init__(self, instructions: str) -> None:
        super().__init__(instructions=instructions)


class LiveKitVoice:
    """Adapts a LiveKit AgentSession to the driver's two-method Voice protocol."""

    def __init__(self, session: AgentSession, agent: Agent) -> None:
        self._session, self._agent = session, agent

    async def set_instructions(self, text: str) -> None:
        await self._agent.update_instructions(text)

    async def speak(self, instructions: str, expect: str | None = None) -> bool:
        """Say something now. True only if it played out completely (and, when `expect` is given,
        the model's own transcript shows that text being spoken)."""
        await self._wait_until_idle()
        # Interruptions cannot be disabled with this model (server-side turn detection), so a
        # student talking over the speech is reported to the driver instead: it repeats a
        # read-back that was cut off rather than accepting consent to words not heard.
        started = time.monotonic()
        handle = self._session.generate_reply(instructions=instructions)
        try:
            await asyncio.wait_for(handle.wait_for_playout(), SPEAK_TIMEOUT_SECONDS)
        except TimeoutError:
            handle.interrupt(force=True)
            log.warning("speech_timeout")
            return False
        error = handle.exception()
        if error is not None or handle.interrupted:
            log.warning(
                "speech_not_completed interrupted=%s error=%s",
                handle.interrupted,
                type(error).__name__ if error else None,
            )
            return False
        if expect is not None:
            spoken = " ".join(
                getattr(item, "text_content", None) or ""
                for item in handle.chat_items
                if getattr(item, "role", None) == "assistant"
            )
            score = coverage(spoken, expect)
            log.info("speech_verified coverage=%.2f spoken_words=%d", score, len(spoken.split()))
            if not was_spoken(spoken, expect):
                log.warning("speech_did_not_contain_the_expected_text")
                return False
        log.info("speech_done seconds=%.1f", time.monotonic() - started)
        return True

    async def _wait_until_idle(self) -> None:
        """Wait until nobody is speaking and the model is not generating, for a short while.

        The model replies to every student turn on its own (a short neutral filler) and its
        transcript reaches us while that reply is still being produced. An explicit request made
        in that window is silently dropped by Gemini Live, so wait for the model to go quiet.
        """
        session, quiet_since = self._session, None
        deadline = time.monotonic() + IDLE_MAX_WAIT_SECONDS
        while time.monotonic() < deadline:
            current = session.current_speech
            busy = (
                session.agent_state in ("thinking", "speaking")
                or session.user_state == "speaking"
                or (current is not None and not current.done())
            )
            now = time.monotonic()
            if busy:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = now
            elif now - quiet_since >= IDLE_QUIET_SECONDS:
                return
            await asyncio.sleep(0.1)
        log.warning("idle_wait_timeout")


def build_session(settings: AgentSettings) -> AgentSession:
    return AgentSession(
        llm=google.realtime.RealtimeModel(
            model=settings.live_model,
            voice=settings.voice,
            api_key=settings.gemini_api_key,
            temperature=0.6,
        ),
        user_away_timeout=SILENCE_SECONDS,
    )


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext) -> None:
    job = parse_job_metadata(ctx.job.metadata)
    if job is None:
        log.error("dispatch metadata missing or malformed; refusing the job")
        return
    log.info("interview_job_started %s", job)  # JobInfo.__str__ never includes the token

    session = build_session(AgentSettings.from_env())
    api_client = ApiClient(job)
    # Armed for Q1 from the moment the agent exists. Updating instructions mid-session makes this
    # model restart its connection, which swallowed a greeting requested right after an update.
    agent = Interviewer(InterviewDriver.initial_instructions())
    driver = InterviewDriver(LiveKitVoice(session, agent), api_client)
    lifecycle = Lifecycle(driver, lambda: ctx.shutdown(reason="interview finished"))

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        text = getattr(item, "text_content", None)  # not every item is a message (e.g. hand-offs)
        role = getattr(item, "role", None)
        if not text:
            return
        if role == "user":  # a finished student utterance, final transcript (ADR-008)
            lifecycle.spawn(lambda: driver.on_student_turn(text), "student_turn")
        elif role == "assistant":
            lifecycle.spawn(lambda: driver.on_agent_turn(text), "agent_turn")

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        if ev.new_state == "away":
            lifecycle.spawn(driver.on_silence, "silence")

    @ctx.room.on("participant_disconnected")
    def _on_left(_participant) -> None:
        lifecycle.spawn(driver.on_student_left, "student_left")

    async def _cleanup() -> None:
        await lifecycle.shutdown()
        await api_client.aclose()

    ctx.add_shutdown_callback(_cleanup)

    await session.start(room=ctx.room, agent=agent)
    lifecycle.start()
    await lifecycle.run(driver.start, "start")


if __name__ == "__main__":
    agents.cli.run_app(server)

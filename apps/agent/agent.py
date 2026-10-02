"""The voice interviewer worker.

Run locally (reads the repo-root .env):   python agent.py start
It registers with LiveKit under AGENT_NAME and waits. The API dispatches it into a student's room
(POST /api/interviews); it then speaks to the student through Gemini Live.

This process holds the Gemini key and LiveKit's credentials and nothing else: no database, no vault
key, no Proof token. It reaches the API only with the per-interview job token it was handed.
"""

import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import google

from interview.driver import InterviewDriver
from interview.jobinfo import parse_job_metadata
from interview.settings import AgentSettings

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

    async def speak(self, instructions: str) -> None:
        await self._session.generate_reply(instructions=instructions)


def build_session(settings: AgentSettings) -> AgentSession:
    return AgentSession(
        llm=google.realtime.RealtimeModel(
            model=settings.live_model,
            voice=settings.voice,
            api_key=settings.gemini_api_key,
            temperature=0.6,
        )
    )


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext) -> None:
    job = parse_job_metadata(ctx.job.metadata)
    if job is None:
        log.error("dispatch metadata missing or malformed; refusing the job")
        return
    log.info("interview_job_started %s", job)  # JobInfo.__str__ never includes the token

    session = build_session(AgentSettings.from_env())
    # Armed for Q1 from the moment the agent exists. Updating instructions mid-session makes this
    # model restart its connection, which swallowed a greeting requested right after an update.
    agent = Interviewer(InterviewDriver.initial_instructions())
    driver = InterviewDriver(LiveKitVoice(session, agent))
    pending: set[asyncio.Task] = set()

    async def handle_turn(text: str) -> None:
        try:
            await driver.on_student_turn(text)
        except Exception:  # noqa: BLE001 - a failed turn must not kill the interview loop
            log.exception(
                "student_turn_failed"
            )  # traceback only; the student's words are not logged

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        # A finished student utterance arrives here with its final transcript (ADR-008).
        item = ev.item
        # Not every conversation item is a message (e.g. agent hand-offs have no role).
        if getattr(item, "role", None) == "user" and getattr(item, "text_content", None):
            task = asyncio.create_task(handle_turn(item.text_content))
            pending.add(task)
            task.add_done_callback(pending.discard)

    await session.start(room=ctx.room, agent=agent)
    await driver.start()


if __name__ == "__main__":
    agents.cli.run_app(server)

"""The voice interviewer worker.

Run locally (reads the repo-root .env):   python agent.py start
It registers with LiveKit under AGENT_NAME and waits. The API dispatches it into a student's room
(POST /api/interviews); it then speaks to the student through Gemini Live.

This process holds the Gemini key and LiveKit's credentials and nothing else: no database, no vault
key, no Proof token. It reaches the API only with the per-interview job token it was handed.
"""

import logging
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import google

from interview.jobinfo import parse_job_metadata
from interview.prompts import GREETING_INSTRUCTIONS, SYSTEM_INSTRUCTIONS
from interview.settings import AgentSettings

AGENT_NAME = "syl-interviewer"  # must match the API's explicit dispatch (apps/api livekit_service)

# Local development only: in production the platform provides the environment. Real env vars win.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

log = logging.getLogger("syl.agent")
server = AgentServer()


class Interviewer(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_INSTRUCTIONS)


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
    await session.start(room=ctx.room, agent=Interviewer())
    await session.generate_reply(instructions=GREETING_INSTRUCTIONS)


if __name__ == "__main__":
    agents.cli.run_app(server)

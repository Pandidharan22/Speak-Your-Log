"""The interview driver: connects the pure state machine (flow.py) to a voice (the live model).

It talks to the voice only through the two-method `Voice` protocol, so everything here is testable
with a fake and no network. Division of labour (ADR-008):

  * the MODEL speaks. Its automatic reply to each student turn is pre-armed by `script.py`;
  * the FLOW records the student's words verbatim and decides what step we are in;
  * the DRIVER re-arms the model after each finished answer, and speaks explicitly only at
    branching moments (read-back, confirmation, closing).

Privacy: this module logs step names and word counts, never the student's words.
"""

import asyncio
import logging
from typing import Protocol

from interview.flow import ANSWER_STEPS, Flow, Kind, Step
from interview.prompts import GREETING_INSTRUCTIONS
from interview.script import armed_instructions

log = logging.getLogger("syl.driver")

# Placeholder until the real read-back + consent flow (steps 3.6-3.8) replaces it.
_PLACEHOLDER_CLOSING = (
    "Say thank you warmly in the student's language and tell them that is all the questions for "
    "now. Do not say anything about saving or posting. Then stop."
)


class Voice(Protocol):
    async def set_instructions(self, text: str) -> None:
        """Replace the model's standing instructions (takes effect for its NEXT reply)."""

    async def speak(self, instructions: str) -> None:
        """Make the model say something now, following `instructions`."""


class InterviewDriver:
    def __init__(self, voice: Voice, flow: Flow | None = None) -> None:
        self._voice = voice
        # min_words=0: no live re-prompt for short answers; the transcript arrives only after the
        # model has already replied, so that decision cannot be made in time (ADR-008).
        self.flow = flow or Flow(min_words=0)
        self._lock = asyncio.Lock()  # student turns are processed strictly in order

    @staticmethod
    def initial_instructions() -> str:
        """What the model must be created with: already armed for the answer to Q1. (Set at
        creation, not via set_instructions: an update just before the first utterance restarts
        the model's connection and loses the greeting.)"""
        return armed_instructions(Step.Q1)

    async def start(self) -> None:
        """Greet the student and ask Q1. The model was created armed for the reply to Q1, so even
        an instant answer already leads to the right next question."""
        async with self._lock:
            self.flow.begin()
            await self._voice.speak(GREETING_INSTRUCTIONS)
        log.info("interview_started step=%s", self.flow.step.value)

    async def on_student_turn(self, text: str) -> None:
        """A finished utterance from the student (their final transcript, exactly as heard)."""
        async with self._lock:
            if self.flow.done or self.flow.step not in ANSWER_STEPS:
                return  # not collecting an answer now (consent handling arrives in step 3.6)
            step = self.flow.step
            action = self.flow.on_answer(text)
            if action.kind is Kind.NOOP:
                return  # nothing was actually said
            log.info("answer_recorded step=%s words=%d", step.value, len(text.split()))
            if action.kind is Kind.READ_BACK:
                await self._read_back()
            else:
                # The model has already asked the next question from its pre-armed instructions;
                # arm it for the turn that is now starting.
                await self._voice.set_instructions(armed_instructions(self.flow.step))

    async def _read_back(self) -> None:
        # Real read-back (exact text from the API, then the consent question) arrives in step 3.7.
        await self._voice.set_instructions(armed_instructions(Step.READBACK))
        await self._voice.speak(_PLACEHOLDER_CLOSING)

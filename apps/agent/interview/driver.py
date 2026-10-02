"""The interview driver: connects the pure state machine (flow.py) to a voice and to the backend.

It talks to the voice only through the two-method `Voice` protocol and to the API only through the
`Backend` protocol, so everything here is testable with fakes and no network. Division of labour
(ADR-008, ADR-005):

  * the MODEL speaks. Its automatic reply to each student turn is pre-armed by `script.py`;
  * the FLOW records the student's words verbatim and decides what step we are in;
  * the DRIVER re-arms the model after each finished answer, stores every answer on the backend,
    and speaks explicitly at branching moments: the read-back, the consent question and the outcome;
  * the BACKEND (the API) builds the post text and posts. The driver can only say "confirmed".

Consent: only the rule-based classifier (consent.py) may turn the student's reply into `confirm`,
and only while the flow is in CONFIRM, i.e. after the read-back has been spoken in full.

Privacy: this module logs step names and word counts, never the student's words.
"""

import asyncio
import logging
from typing import Protocol

from interview.api_client import ApiError, Preview
from interview.api_client import PostOutcome as BackendOutcome
from interview.consent import classify
from interview.flow import ANSWER_STEPS, Flow, Kind, Outcome, PostResult, Step
from interview.prompts import GREETING_INSTRUCTIONS
from interview.script import (
    CLOSING,
    NUDGE,
    REASK_CONFIRM,
    RESTART,
    armed_instructions,
    outcome_instructions,
    readback_instructions,
)

log = logging.getLogger("syl.driver")

_ANSWER_KEY = {Step.Q1: "q1", Step.Q2: "q2", Step.Q3: "q3", Step.FOLLOWUP: "followup_a"}
_OUTCOME_CLOSING = {
    Outcome.CANCELLED: "cancelled",
    Outcome.UNCLEAR: "unclear",
    Outcome.INACTIVE: "inactive",
    Outcome.TIME_LIMIT: "time_limit",
}
_READBACK_ATTEMPTS = 2
# Retryable failures that retrying cannot fix: treat as final so the student is told to reconnect.
_FINAL_REASONS = {"token_rejected", "token_missing"}

# Placeholder closing used only when there is no backend (the early, backend-less flow in tests).
_PLACEHOLDER_CLOSING = (
    "Say thank you warmly in the student's language and tell them that is all the questions for "
    "now. Do not say anything about saving or posting. Then stop."
)


class Voice(Protocol):
    async def set_instructions(self, text: str) -> None:
        """Replace the model's standing instructions (takes effect for its NEXT reply)."""

    async def speak(self, instructions: str, expect: str | None = None) -> bool:
        """Make the model say something now, following `instructions`. Returns when it is done:
        True if it played out completely, False if it was cut off, never started, or (when
        `expect` is given) the model's own transcript does not show that text being spoken."""


class Backend(Protocol):
    async def set_state(self, state: str) -> bool: ...
    async def put_answer(self, key: str, text: str) -> bool: ...
    async def preview(self) -> Preview | None: ...
    async def post(self, confirmation_text: str) -> BackendOutcome: ...


class InterviewDriver:
    def __init__(
        self, voice: Voice, backend: Backend | None = None, flow: Flow | None = None
    ) -> None:
        self._voice = voice
        self._backend = backend
        # min_words=0: no live re-prompt for short answers; the transcript arrives only after the
        # model has already replied, so that decision cannot be made in time (ADR-008).
        self.flow = flow or Flow(min_words=0)
        self._lock = asyncio.Lock()  # events are processed strictly in order

    @property
    def done(self) -> bool:
        return self.flow.done

    @staticmethod
    def initial_instructions() -> str:
        """What the model must be created with: already armed for the answer to Q1. (Set at
        creation, not via set_instructions: an update just before the first utterance restarts
        the model's connection and loses the greeting.)"""
        return armed_instructions(Step.Q1)

    # ---- lifecycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        """Greet the student and ask Q1. The model was created armed for the reply to Q1."""
        async with self._lock:
            self.flow.begin()
            await self._set_state("interviewing")
            await self._voice.speak(GREETING_INSTRUCTIONS)
        log.info("interview_started step=%s", self.flow.step.value)

    async def on_agent_turn(self, text: str) -> None:
        """What the model itself just said. Only used to remember the follow-up question it asked
        (an audit trail; it is never part of the post)."""
        async with self._lock:
            if self.flow.step is Step.FOLLOWUP and self.flow.followup_question is None:
                self.flow.on_followup_question(text)

    async def on_student_turn(self, text: str) -> None:
        """A finished utterance from the student (their final transcript, exactly as heard)."""
        # Where the flow was when this utterance ARRIVED, before waiting for the lock. A "yes"
        # spoken while the read-back is still being read would otherwise queue behind it and be
        # processed once the flow reaches CONFIRM, counting as consent for a log the student had
        # not finished hearing. Consent only counts if it came AFTER the read-back.
        step_at_arrival = self.flow.step
        async with self._lock:
            if self.flow.done:
                return
            if step_at_arrival is Step.READBACK:
                log.info("turn_ignored reason=spoken_during_read_back")
                return
            if self.flow.step in ANSWER_STEPS:
                await self._answer(text)
            elif self.flow.step is Step.CONFIRM:
                await self._confirmation(text)
            # Any other step (greeting, read-back, posting): the student's speech is not an answer
            # and cannot be consent. It is ignored.

    async def on_silence(self) -> None:
        """The student has been quiet too long: nudge once, then end. Never counts as consent."""
        async with self._lock:
            action = self.flow.on_timeout()
            if action.kind is Kind.REPROMPT:
                await self._voice.speak(NUDGE)
            elif action.kind is Kind.ASK:  # in CONFIRM: ask the yes/no question again
                await self._voice.speak(REASK_CONFIRM)
            elif action.kind is Kind.END:
                await self._finish(action.outcome)

    async def on_hard_cap(self) -> None:
        async with self._lock:
            action = self.flow.on_hard_cap()
            if action.kind is Kind.END:
                await self._finish(action.outcome)

    async def on_student_left(self) -> None:
        """The student disconnected. End quietly (there is no one to speak to)."""
        async with self._lock:
            action = self.flow.on_student_left()
            if action.kind is Kind.END:
                await self._set_state("cancelled")
                log.info("student_left step=%s", action.step.value)

    # ---- answers -----------------------------------------------------------------------------

    async def _answer(self, text: str) -> None:
        step = self.flow.step
        action = self.flow.on_answer(text)
        if action.kind is Kind.NOOP:
            return  # nothing was actually said
        log.info("answer_recorded step=%s words=%d", step.value, len(text.split()))
        key = _ANSWER_KEY[step]
        await self._put(key, self.flow.answers.get(key, text))
        if action.kind is Kind.READ_BACK:
            await self._read_back()
        else:
            # The model already asked the next question from its pre-armed instructions; arm it
            # for the turn that is now starting.
            await self._voice.set_instructions(armed_instructions(self.flow.step))

    async def _read_back(self) -> None:
        if self._backend is None:  # backend-less mode (tests of the early flow)
            await self._voice.speak(_PLACEHOLDER_CLOSING)
            return
        if self.flow.followup_question:
            await self._put("followup_q", self.flow.followup_question)
        preview = None
        if await self._set_state("confirming"):  # refused unless all four answers are stored
            preview = await self._call(self._backend.preview())
        # The student may only approve text they have heard in full, so a read-back that was cut
        # off (or never started) is repeated once and then the session ends without posting.
        if preview is None or not await self._speak_readback(preview):
            log.error("readback_unavailable")
            await self._set_state("failed")
            self.flow.on_hard_cap()  # end the flow: nothing can be confirmed without a read-back
            await self._voice.speak(CLOSING["no_readback"])
            return
        self.flow.on_readback_done()  # only now can the student's yes count

    async def _speak_readback(self, preview: Preview) -> bool:
        # The text comes from the API, the single source of truth for what will be posted.
        for attempt in range(1, _READBACK_ATTEMPTS + 1):
            spoken = await self._voice.speak(
                readback_instructions(preview.content, preview.why),
                expect=f"{preview.content} {preview.why}",
            )
            if spoken:
                return True
            log.warning("readback_not_completed attempt=%d", attempt)
        return False

    # ---- consent -----------------------------------------------------------------------------

    async def _confirmation(self, text: str) -> None:
        action = self.flow.on_intent(classify(text))
        log.info("consent_reply action=%s", action.kind.value)
        if action.kind is Kind.REQUEST_POST:
            await self._post(text)
        elif action.kind is Kind.RESTART:  # "edit": start over
            await self._set_state("interviewing")  # the API clears the old draft
            await self._voice.speak(RESTART)
            await self._voice.set_instructions(armed_instructions(Step.Q1))
        elif action.kind is Kind.ASK:  # unclear: ask the yes/no question again
            await self._voice.speak(REASK_CONFIRM)
        elif action.kind is Kind.END:
            await self._finish(action.outcome)

    async def _post(self, confirmation_text: str) -> None:
        """The student confirmed. Ask the backend to post (once) and tell them the outcome."""
        assert self._backend is not None  # noqa: S101 - REQUEST_POST only occurs with a backend
        try:
            outcome = await self._backend.post(confirmation_text)
        except ApiError:
            outcome = BackendOutcome(
                PostResult.UNKNOWN
            )  # we cannot tell; never assume "not posted"
        result, reason = outcome.result, outcome.reason
        if result is PostResult.RETRYABLE and reason in _FINAL_REASONS:
            result = (
                PostResult.REJECTED
            )  # a dead token cannot be retried; the student must reconnect
        action = self.flow.on_post_result(result)
        log.info("post_outcome result=%s reason=%s", result.value, reason)
        await self._voice.speak(outcome_instructions(result, reason))
        if action.kind is Kind.END and result is PostResult.REJECTED:
            await self._set_state("failed")  # the gateway left it in `confirming`; close it out

    # ---- ending ------------------------------------------------------------------------------

    async def _finish(self, outcome: Outcome | None) -> None:
        await self._set_state("cancelled")
        closing = CLOSING[_OUTCOME_CLOSING.get(outcome, "cancelled")]
        await self._voice.speak(closing)

    # ---- backend helpers (failures are contained: they never crash the conversation) ----------

    async def _set_state(self, state: str) -> bool:
        if self._backend is None:
            return True
        return bool(await self._call(self._backend.set_state(state), default=False))

    async def _put(self, key: str, text: str) -> None:
        if self._backend is not None:
            await self._call(self._backend.put_answer(key, text), default=False)

    @staticmethod
    async def _call(awaitable, default=None):
        try:
            return await awaitable
        except ApiError as exc:
            log.warning("backend_call_failed error=%s", type(exc).__name__)
            return default

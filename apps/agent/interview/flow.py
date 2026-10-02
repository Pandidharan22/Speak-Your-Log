"""The interview conversation flow: a pure decision machine (no I/O, no third-party imports).

The conversation layer (LiveKit + Gemini Live, step 3.3+) feeds this machine *events* — a finished
student utterance, a classified intent, a timeout — and the machine answers with an `Action` saying
what should happen next. It cannot speak, call the network, or post anything; it can only *request*
a post, and only from the one place consent is possible. That makes the safety properties provable
by exhaustive testing (tests/test_flow.py explores every reachable state).

Rules it enforces (SRS FR-6..FR-13, ADR-005, ADR-007):
  * order is fixed: Q1 -> Q2 -> Q3 -> one follow-up -> read-back -> confirm;
  * answers are the student's words only: stored as heard, joined with a single space, never edited;
  * a post is requested ONLY from CONFIRM on a classified `confirm` intent, once per attempt;
  * silence, `unclear`, `edit` and `cancel` can never post; unexpected events are ignored (NOOP).
"""

import copy
from dataclasses import dataclass
from enum import StrEnum

MIN_WORDS = 3  # an answer shorter than this is gently re-prompted once (FR-9)
MAX_RESTARTS = 2  # "edit: start over" is allowed this many times
MAX_UNCLEAR = 2  # a third unclear reply to the confirmation question ends the session
MAX_POST_ATTEMPTS = 2  # after a definitive, retryable failure the student may confirm once more


class Step(StrEnum):
    GREETING = "greeting"
    Q1 = "q1"  # "What did you try today?"
    Q2 = "q2"  # "What broke?"
    Q3 = "q3"  # "Why did you choose that?"
    FOLLOWUP = "followup"  # one question built from the student's own words
    READBACK = "readback"  # the log is being read back (consent is NOT possible yet)
    CONFIRM = "confirm"  # waiting for a spoken yes / edit / cancel
    POSTING = "posting"  # confirmed; the backend is posting
    DONE = "done"


class Intent(StrEnum):
    CONFIRM = "confirm"
    EDIT = "edit"
    CANCEL = "cancel"
    UNCLEAR = "unclear"


class Kind(StrEnum):
    ASK = "ask"  # ask the question for `step`
    REPROMPT = "reprompt"  # gently ask for more on `step`
    ASK_FOLLOWUP = "ask_followup"  # generate + ask ONE follow-up quoting the student
    READ_BACK = "read_back"  # read the log back (text comes from the backend preview)
    AWAIT = "await"  # nothing to say; wait for the student
    REQUEST_POST = "request_post"  # ask the backend to post (the only path to a post)
    RESTART = "restart"  # start again from Q1
    END = "end"  # finish; see `outcome`
    NOOP = "noop"  # event not valid here: ignored, state unchanged


class Outcome(StrEnum):
    POSTED = "posted"
    CANCELLED = "cancelled"
    INACTIVE = "inactive"
    TIME_LIMIT = "time_limit"
    UNCLEAR = "unclear"
    POST_UNKNOWN = "post_unknown"
    POST_FAILED = "post_failed"


class PostResult(StrEnum):
    POSTED = "posted"
    RETRYABLE = "retryable"  # Proof definitely did NOT apply it (token/rate limit/unreachable)
    UNKNOWN = "unknown"  # may have been applied: never retry
    REJECTED = "rejected"  # Proof refused it for good


@dataclass(frozen=True, slots=True)
class Action:
    kind: Kind
    step: Step
    outcome: Outcome | None = None


ANSWER_STEPS = (Step.Q1, Step.Q2, Step.Q3, Step.FOLLOWUP)
_NEXT = {Step.Q1: Step.Q2, Step.Q2: Step.Q3, Step.Q3: Step.FOLLOWUP, Step.FOLLOWUP: Step.READBACK}
_ANSWER_KEY = {Step.Q1: "q1", Step.Q2: "q2", Step.Q3: "q3", Step.FOLLOWUP: "followup_a"}

# Persisted state (API `InterviewState` values) that each flow step corresponds to.
_API_STATE = {
    Step.GREETING: "created",
    Step.Q1: "interviewing",
    Step.Q2: "interviewing",
    Step.Q3: "interviewing",
    Step.FOLLOWUP: "interviewing",
    Step.READBACK: "interviewing",
    Step.CONFIRM: "confirming",
    Step.POSTING: "posting",
}
_API_STATE_BY_OUTCOME = {
    Outcome.POSTED: "posted",
    Outcome.POST_UNKNOWN: "post_unknown",
    Outcome.POST_FAILED: "failed",
}


def _noop(step: Step) -> Action:
    return Action(Kind.NOOP, step)


class Flow:
    def __init__(self, min_words: int = MIN_WORDS) -> None:
        # min_words=0 disables the short-answer re-prompt. The live agent uses 0: Gemini Live
        # replies before the transcript is available, so a re-prompt cannot be decided in time
        # (ADR-008). The behaviour stays here, tested, for transports that can support it.
        self._min_words = min_words
        self.step = Step.GREETING
        self.outcome: Outcome | None = None
        self.followup_question: str | None = None
        self._restarts = 0
        self._post_attempts = 0
        self._unclear = 0  # unclear replies to the current confirmation question
        # Bookkeeping for the CURRENT step only; reset whenever the step changes.
        self._parts: list[str] = []  # utterances heard so far for the current answer
        self._reprompted = False
        self._silences = 0
        self._answers: dict[str, str] = {}

    # ---- inspection ----------------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self.step is Step.DONE

    @property
    def api_state(self) -> str:
        """The persisted `interview_sessions.state` this flow position corresponds to."""
        if self.step is Step.DONE:
            return _API_STATE_BY_OUTCOME.get(self.outcome, "cancelled")
        return _API_STATE[self.step]

    @property
    def answers(self) -> dict[str, str]:
        """Finished answers, verbatim. Keys: q1, q2, q3, followup_a."""
        return dict(self._answers)

    def fingerprint(self) -> tuple:
        """A hashable summary of the whole machine state (used to explore it exhaustively)."""
        return (
            self.step,
            self.outcome,
            self._restarts,
            self._post_attempts,
            self._unclear,
            len(self._parts),
            self._reprompted,
            self._silences,
            tuple(sorted(self._answers)),
            self.followup_question is not None,
        )

    def clone(self) -> "Flow":
        return copy.deepcopy(self)

    # ---- events --------------------------------------------------------------------------

    def begin(self) -> Action:
        """The agent has joined and greeted the student."""
        if self.step is not Step.GREETING:
            return _noop(self.step)
        self._enter(Step.Q1)
        return Action(Kind.ASK, Step.Q1)

    def on_answer(self, text: str) -> Action:
        """A finished utterance from the student, taken as heard (Q1, Q2, Q3, follow-up only)."""
        step = self.step
        if step not in ANSWER_STEPS:
            return _noop(step)
        heard = text.strip()
        if not heard:
            return _noop(step)  # nothing was actually said
        self._parts.append(heard)
        self._silences = 0  # they are talking: the silence count starts again
        words = sum(len(part.split()) for part in self._parts)
        if words < self._min_words and not self._reprompted:
            self._reprompted = True
            return Action(Kind.REPROMPT, step)  # ask once for more; keep what they said
        self._answers[_ANSWER_KEY[step]] = " ".join(self._parts)
        self._enter(_NEXT[step])
        return {
            Step.Q2: Action(Kind.ASK, Step.Q2),
            Step.Q3: Action(Kind.ASK, Step.Q3),
            Step.FOLLOWUP: Action(Kind.ASK_FOLLOWUP, Step.FOLLOWUP),
            Step.READBACK: Action(Kind.READ_BACK, Step.READBACK),
        }[self.step]

    def on_followup_question(self, question: str) -> bool:
        """Record the follow-up the model actually asked (for the audit trail). Not an answer."""
        if self.step is not Step.FOLLOWUP or not question.strip():
            return False
        self.followup_question = question.strip()
        return True

    def on_readback_done(self) -> Action:
        """The whole read-back has been spoken. Only now may the student's yes count."""
        if self.step is not Step.READBACK:
            return _noop(self.step)
        self._enter(Step.CONFIRM)
        return Action(Kind.AWAIT, Step.CONFIRM)

    def on_intent(self, intent: Intent) -> Action:
        """The classified meaning of the student's reply to the read-back (CONFIRM step only)."""
        if self.step is not Step.CONFIRM:
            return _noop(self.step)
        if intent is Intent.CONFIRM:
            self._post_attempts += 1
            self._enter(Step.POSTING)
            return Action(Kind.REQUEST_POST, Step.POSTING)
        if intent is Intent.EDIT:
            if self._restarts >= MAX_RESTARTS:
                return self._end(Outcome.CANCELLED)
            self._restarts += 1
            self._answers = {}
            self.followup_question = None
            self._enter(Step.Q1)
            return Action(Kind.RESTART, Step.Q1)
        if intent is Intent.CANCEL:
            return self._end(Outcome.CANCELLED)
        self._unclear += 1  # Intent.UNCLEAR
        self._silences = 0  # they did answer, just not clearly
        if self._unclear > MAX_UNCLEAR:
            return self._end(Outcome.UNCLEAR)
        return Action(Kind.ASK, Step.CONFIRM)  # ask the yes/no question again

    def on_post_result(self, result: PostResult) -> Action:
        """The backend's answer to a requested post (POSTING step only)."""
        if self.step is not Step.POSTING:
            return _noop(self.step)
        if result is PostResult.POSTED:
            return self._end(Outcome.POSTED)
        if result is PostResult.UNKNOWN:
            return self._end(Outcome.POST_UNKNOWN)
        if result is PostResult.REJECTED:
            return self._end(Outcome.POST_FAILED)
        # RETRYABLE: nothing was applied. Offer one more confirmation, then give up.
        if self._post_attempts >= MAX_POST_ATTEMPTS:
            return self._end(Outcome.POST_FAILED)
        self._enter(Step.CONFIRM)
        return Action(Kind.ASK, Step.CONFIRM)

    def on_timeout(self) -> Action:
        """The student has been silent too long. First time: nudge. Second time: end."""
        step = self.step
        if step in (Step.GREETING, Step.READBACK, Step.POSTING, Step.DONE):
            return _noop(step)
        self._silences += 1
        if self._silences >= 2:
            return self._end(Outcome.INACTIVE)  # silence NEVER counts as consent
        return Action(Kind.ASK if step is Step.CONFIRM else Kind.REPROMPT, step)

    def on_hard_cap(self) -> Action:
        """The session has run out of time. A post already in flight is allowed to finish."""
        if self.step in (Step.POSTING, Step.DONE):
            return _noop(self.step)
        return self._end(Outcome.TIME_LIMIT)

    def on_student_left(self) -> Action:
        """The student disconnected. Ends the session, but never interrupts a post in flight."""
        if self.step in (Step.POSTING, Step.DONE):
            return _noop(self.step)
        return self._end(Outcome.CANCELLED)

    # ---- internals -----------------------------------------------------------------------

    def _enter(self, step: Step) -> None:
        """Move to `step` and forget bookkeeping that only mattered for the previous one."""
        self.step = step
        self._parts = []
        self._reprompted = False
        self._silences = 0
        if step is Step.CONFIRM:
            self._unclear = 0

    def _end(self, outcome: Outcome) -> Action:
        self._enter(Step.DONE)
        self.outcome = outcome
        return Action(Kind.END, Step.DONE, outcome)

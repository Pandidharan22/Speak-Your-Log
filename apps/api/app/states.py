"""Persisted interview states and the only legal moves between them (SRS §2, ADR-005).

Pure and dependency-free so it can be tested exhaustively. The database CHECK constraint on
`interview_sessions.state` lists the same values (a test keeps them in sync); the post gateway
(step 4.1) enforces a move atomically with `UPDATE ... WHERE state = <src>`.

The consent rule in one line: **the only way into `posting` is from `confirming`.**
"""

from enum import StrEnum


class InterviewState(StrEnum):
    CREATED = "created"  # room made, agent not yet joined
    INTERVIEWING = "interviewing"  # asking questions and capturing answers
    CONFIRMING = "confirming"  # log read back; waiting for a spoken yes
    POSTING = "posting"  # confirmed; the Proof call is in flight
    POSTED = "posted"
    POST_UNKNOWN = "post_unknown"  # Proof call may or may not have applied: never retried
    FAILED = "failed"  # Proof refused the log for good
    CANCELLED = "cancelled"


S = InterviewState

TRANSITIONS: dict[InterviewState, frozenset[InterviewState]] = {
    S.CREATED: frozenset({S.INTERVIEWING, S.CANCELLED, S.FAILED}),
    S.INTERVIEWING: frozenset({S.CONFIRMING, S.CANCELLED, S.FAILED}),
    # Back to interviewing = "edit: start over".
    S.CONFIRMING: frozenset({S.POSTING, S.INTERVIEWING, S.CANCELLED, S.FAILED}),
    # Back to confirming = Proof definitely did NOT apply the post (bad token, rate limit,
    # unreachable): the student may be asked to confirm again and retry.
    S.POSTING: frozenset({S.POSTED, S.POST_UNKNOWN, S.FAILED, S.CONFIRMING}),
    S.POSTED: frozenset(),
    S.POST_UNKNOWN: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}

TERMINAL: frozenset[InterviewState] = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)


class IllegalTransition(Exception):
    def __init__(self, src: InterviewState, dst: InterviewState) -> None:
        super().__init__(f"illegal interview transition {src.value} -> {dst.value}")
        self.src, self.dst = src, dst


def can_transition(src: InterviewState, dst: InterviewState) -> bool:
    return dst in TRANSITIONS[src]


def require_transition(src: InterviewState, dst: InterviewState) -> None:
    if not can_transition(src, dst):
        raise IllegalTransition(src, dst)

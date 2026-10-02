"""Builds what gets posted to Proof from the stored answers (SRS FR-14, ADR-007).

    content = Q1 answer + " " + Q2 answer          (what they tried, what broke)
    why     = Q3 answer + " " + follow-up answer   (why they chose it)
    verb    = "built"

The ONLY transformation is trimming the ends of each answer and joining with one space: no labels,
no summarising, no rewording. The same function produces the read-back the student hears and sees,
so what they approve is byte-for-byte what is posted. The agent can store answers but can never
supply post text: this builder is the single source of truth.
"""

from collections.abc import Mapping
from dataclasses import dataclass

VERB = "built"
ANSWER_KEYS = ("q1", "q2", "q3", "followup_q", "followup_a")  # followup_q is the audit trail only
REQUIRED_FOR_POST = ("q1", "q2", "q3", "followup_a")
MAX_ANSWER_CHARS = 2000  # per answer; the Proof client enforces the 4,000-char field limit too


class IncompleteDraft(Exception):
    """A required answer is missing or blank."""


@dataclass(frozen=True, slots=True)
class PostPayload:
    verb: str
    content: str
    why: str


def build_payload(answers: Mapping[str, str]) -> PostPayload:
    parts: dict[str, str] = {}
    for key in REQUIRED_FOR_POST:
        value = answers.get(key)
        if not isinstance(value, str) or not value.strip():
            raise IncompleteDraft(key)
        parts[key] = value.strip()  # ends only: the words inside stay exactly as heard
    return PostPayload(
        verb=VERB,
        content=f"{parts['q1']} {parts['q2']}",
        why=f"{parts['q3']} {parts['followup_a']}",
    )


def is_complete(answers: Mapping[str, str]) -> bool:
    try:
        build_payload(answers)
    except IncompleteDraft:
        return False
    return True

"""Did the model actually speak the log? Checked against its own output transcript.

The read-back is the one moment the student approves text, so "the speech finished" is not enough
evidence: in live testing Gemini Live silently dropped an explicit request made while it was
generating its own reply, and the speech handle still completed. A "yes" must only ever count for
a log the student heard, so the driver requires the spoken transcript to cover the log's words.

Coverage is deliberately lenient (word multiset, order ignored, partial transcripts tolerated): it
exists to catch "nothing, or something else, was said", not to grade pronunciation.
"""

import re
from collections import Counter

# Punctuation to strip from word edges. Tamil letters and vowel signs are not in this set.
_EDGE = re.compile(r"^[^\w஀-௿]+|[^\w஀-௿]+$")

MIN_COVERAGE = 0.6


def _words(text: str) -> list[str]:
    return [w for w in (_EDGE.sub("", t) for t in text.casefold().split()) if w]


def coverage(spoken: str, expected: str) -> float:
    """Fraction of the expected words (counting repeats) that appear in what was spoken."""
    want = Counter(_words(expected))
    if not want:
        return 1.0
    have = Counter(_words(spoken))
    found = sum(min(n, have[w]) for w, n in want.items())
    return found / sum(want.values())


def was_spoken(spoken: str, expected: str) -> bool:
    return coverage(spoken, expected) >= MIN_COVERAGE

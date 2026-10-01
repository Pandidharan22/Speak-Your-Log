"""The persisted state machine, checked against an independent statement of the rules."""

import itertools
import re
from pathlib import Path

import pytest

from app.config import REPO_ROOT
from app.states import (
    TERMINAL,
    TRANSITIONS,
    IllegalTransition,
    InterviewState,
    can_transition,
    require_transition,
)

# The spec, written out separately from the implementation so the two can disagree.
LEGAL = {
    ("created", "interviewing"), ("created", "cancelled"), ("created", "failed"),
    ("interviewing", "confirming"), ("interviewing", "cancelled"), ("interviewing", "failed"),
    ("confirming", "posting"), ("confirming", "interviewing"),
    ("confirming", "cancelled"), ("confirming", "failed"),
    ("posting", "posted"), ("posting", "post_unknown"),
    ("posting", "failed"), ("posting", "confirming"),
}  # fmt: skip
ALL = [s.value for s in InterviewState]


def test_every_one_of_the_64_pairs_matches_the_spec():
    for src, dst in itertools.product(InterviewState, repeat=2):
        expected = (src.value, dst.value) in LEGAL
        assert can_transition(src, dst) is expected, f"{src.value} -> {dst.value}"


def test_the_only_way_into_posting_is_from_confirming():
    into_posting = {s for s in InterviewState if can_transition(s, InterviewState.POSTING)}
    assert into_posting == {InterviewState.CONFIRMING}


def test_posted_is_only_reachable_from_posting():
    into_posted = {s for s in InterviewState if can_transition(s, InterviewState.POSTED)}
    assert into_posted == {InterviewState.POSTING}


def test_terminal_states_have_no_exits_and_nothing_else_does():
    assert TERMINAL == {
        InterviewState.POSTED,
        InterviewState.POST_UNKNOWN,
        InterviewState.FAILED,
        InterviewState.CANCELLED,
    }
    for s in InterviewState:
        assert (not TRANSITIONS[s]) == (s in TERMINAL)


def test_an_ambiguous_post_can_never_be_retried():
    # post_unknown is terminal: nothing leads out of it, so no code path can re-post.
    assert TRANSITIONS[InterviewState.POST_UNKNOWN] == frozenset()


def test_no_state_can_move_to_itself_or_back_to_created():
    for s in InterviewState:
        assert not can_transition(s, s)
        assert not can_transition(s, InterviewState.CREATED)


def test_every_non_terminal_state_can_be_cancelled_or_failed():
    for s in InterviewState:
        if s not in TERMINAL and s is not InterviewState.POSTING:
            assert can_transition(s, InterviewState.CANCELLED), s
            assert can_transition(s, InterviewState.FAILED), s
    # Once posting has started it cannot be cancelled: it must resolve to a real outcome.
    assert not can_transition(InterviewState.POSTING, InterviewState.CANCELLED)


def test_every_state_is_reachable_from_created():
    seen, frontier = {InterviewState.CREATED}, [InterviewState.CREATED]
    while frontier:
        for nxt in TRANSITIONS[frontier.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(InterviewState)


def test_require_transition_raises_a_message_with_only_state_names():
    require_transition(InterviewState.CONFIRMING, InterviewState.POSTING)  # legal: no error
    with pytest.raises(IllegalTransition) as exc:
        require_transition(InterviewState.INTERVIEWING, InterviewState.POSTING)
    assert str(exc.value) == "illegal interview transition interviewing -> posting"
    assert (exc.value.src, exc.value.dst) == (InterviewState.INTERVIEWING, InterviewState.POSTING)


def test_unknown_state_strings_from_the_database_are_rejected():
    with pytest.raises(ValueError):
        InterviewState("hacked")
    with pytest.raises(ValueError):
        InterviewState("POSTED")  # case-sensitive: matches the DB CHECK exactly


def test_enum_matches_the_database_check_constraint():
    sql = Path(REPO_ROOT / "db/migrations/0001_init.sql").read_text(encoding="utf-8")
    block = re.search(r"constraint state_valid check \(state in\s*\((.*?)\)\)", sql, re.S)
    assert block, "state_valid CHECK not found in the migration"
    in_db = set(re.findall(r"'([a-z_]+)'", block.group(1)))
    assert in_db == set(ALL)

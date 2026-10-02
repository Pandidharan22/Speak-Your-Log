"""The interview flow: scripted scenarios, then an exhaustive check of the consent invariants."""

import pytest

from interview.flow import (
    ANSWER_STEPS,
    MAX_POST_ATTEMPTS,
    MAX_RESTARTS,
    MAX_UNCLEAR,
    Action,
    Flow,
    Intent,
    Kind,
    Outcome,
    PostResult,
    Step,
)

LONG = "I tried an IR sensor today"


def at_confirm() -> Flow:
    """A flow that has finished the interview and read the log back: ready for a yes/no."""
    f = Flow()
    f.begin()
    for text in ("first answer here", "second answer here", "third answer here"):
        f.on_answer(text)
    f.on_followup_question("You said first, why?")
    f.on_answer("fourth answer here")
    f.on_readback_done()
    assert f.step is Step.CONFIRM
    return f


# ---- the happy path -------------------------------------------------------------------------


def test_full_interview_in_order_with_verbatim_answers():
    f = Flow()
    assert f.begin() == Action(Kind.ASK, Step.Q1)
    assert f.on_answer("I tried an IR sensor today") == Action(Kind.ASK, Step.Q2)
    assert f.on_answer("it kept giving noisy readings") == Action(Kind.ASK, Step.Q3)
    assert f.on_answer("because it was cheaper") == Action(Kind.ASK_FOLLOWUP, Step.FOLLOWUP)
    assert f.on_followup_question("You said noisy readings. Why that over ultrasonic?") is True
    assert f.on_answer("ultrasonic was too slow for me") == Action(Kind.READ_BACK, Step.READBACK)
    assert f.on_readback_done() == Action(Kind.AWAIT, Step.CONFIRM)
    assert f.on_intent(Intent.CONFIRM) == Action(Kind.REQUEST_POST, Step.POSTING)
    assert f.on_post_result(PostResult.POSTED) == Action(Kind.END, Step.DONE, Outcome.POSTED)

    assert f.done and f.outcome is Outcome.POSTED
    assert f.answers == {
        "q1": "I tried an IR sensor today",
        "q2": "it kept giving noisy readings",
        "q3": "because it was cheaper",
        "followup_a": "ultrasonic was too slow for me",
    }


def test_api_state_tracks_the_persisted_state_machine():
    f = Flow()
    seen = [f.api_state]
    f.begin()
    seen.append(f.api_state)
    for text in ("one two three", "one two three", "one two three"):
        f.on_answer(text)
        seen.append(f.api_state)
    f.on_answer("one two three")  # follow-up answer -> read-back
    seen.append(f.api_state)
    f.on_readback_done()
    seen.append(f.api_state)
    f.on_intent(Intent.CONFIRM)
    seen.append(f.api_state)
    f.on_post_result(PostResult.POSTED)
    seen.append(f.api_state)
    assert seen == [
        "created", "interviewing", "interviewing", "interviewing", "interviewing",
        "interviewing", "confirming", "posting", "posted",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (Outcome.POSTED, "posted"),
        (Outcome.POST_UNKNOWN, "post_unknown"),
        (Outcome.POST_FAILED, "failed"),
        (Outcome.CANCELLED, "cancelled"),
        (Outcome.INACTIVE, "cancelled"),
        (Outcome.TIME_LIMIT, "cancelled"),
        (Outcome.UNCLEAR, "cancelled"),
    ],
)
def test_every_outcome_maps_to_a_persisted_terminal_state(outcome, expected):
    f = Flow()
    f._end(outcome)
    assert f.api_state == expected


# ---- the student's words: verbatim, never edited --------------------------------------------


def test_answers_keep_the_exact_words_only_stripping_the_ends():
    f = Flow()
    f.begin()
    tamil = "  சென்சார் ரீடிங் சரியாவே வரல, so I  switched to an IR sensor.  "
    f.on_answer(tamil)
    assert f.answers["q1"] == tamil.strip()  # inner double space, case, punctuation, Tamil: intact


def test_a_short_answer_is_reprompted_once_and_both_parts_are_kept():
    f = Flow()
    f.begin()
    assert f.on_answer("ok") == Action(Kind.REPROMPT, Step.Q1)
    assert f.step is Step.Q1 and "q1" not in f.answers
    assert f.on_answer("I used an IR sensor") == Action(Kind.ASK, Step.Q2)
    assert f.answers["q1"] == "ok I used an IR sensor"  # everything the student said, in order


def test_after_one_reprompt_even_a_short_answer_is_accepted():
    f = Flow()
    f.begin()
    f.on_answer("ok")
    assert f.on_answer("fine") == Action(Kind.ASK, Step.Q2)  # no second reprompt
    assert f.answers["q1"] == "ok fine"


@pytest.mark.parametrize("silence", ["", "   ", "\n\t "])
def test_empty_transcripts_are_ignored_and_not_counted_as_answers(silence):
    f = Flow()
    f.begin()
    before = f.fingerprint()
    assert f.on_answer(silence) == Action(Kind.NOOP, Step.Q1)
    assert f.fingerprint() == before  # no reprompt consumed, nothing recorded


def test_the_follow_up_is_asked_exactly_once_and_only_after_q3():
    f = Flow()
    f.begin()
    kinds = [f.on_answer("one two three").kind for _ in range(3)]
    assert kinds == [Kind.ASK, Kind.ASK, Kind.ASK_FOLLOWUP]
    assert f.on_answer("one two three").kind is Kind.READ_BACK  # answering it moves on, no 2nd


def test_followup_question_is_recorded_only_while_following_up():
    f = Flow()
    f.begin()
    assert f.on_followup_question("too early?") is False
    for _ in range(3):
        f.on_answer("one two three")
    assert f.on_followup_question("   ") is False
    assert f.on_followup_question("  Why that? ") is True and f.followup_question == "Why that?"
    f.on_answer("one two three")
    assert f.on_followup_question("too late?") is False and f.followup_question == "Why that?"


# ---- consent: the part that must never go wrong ---------------------------------------------


def test_confirmation_during_the_read_back_does_not_count():
    f = Flow()
    f.begin()
    for _ in range(4):
        f.on_answer("one two three")
    assert f.step is Step.READBACK
    for intent in Intent:  # even a perfectly clear "yes, post it" mid-read-back is ignored
        assert f.on_intent(intent).kind is Kind.NOOP
    assert f.step is Step.READBACK


@pytest.mark.parametrize("intent", [Intent.EDIT, Intent.CANCEL, Intent.UNCLEAR])
def test_only_confirm_can_request_a_post(intent):
    f = at_confirm()
    assert f.on_intent(intent).kind is not Kind.REQUEST_POST
    assert f.step is not Step.POSTING


def test_a_second_yes_cannot_cause_a_second_post():
    f = at_confirm()
    assert f.on_intent(Intent.CONFIRM).kind is Kind.REQUEST_POST
    assert f.on_intent(Intent.CONFIRM).kind is Kind.NOOP  # double-"yes" / echo
    assert f.step is Step.POSTING


def test_cancel_ends_without_posting():
    f = at_confirm()
    assert f.on_intent(Intent.CANCEL) == Action(Kind.END, Step.DONE, Outcome.CANCELLED)
    assert f.api_state == "cancelled"


def test_unclear_replies_re_ask_then_give_up_without_posting():
    f = at_confirm()
    for _ in range(MAX_UNCLEAR):
        assert f.on_intent(Intent.UNCLEAR) == Action(Kind.ASK, Step.CONFIRM)
    assert f.on_intent(Intent.UNCLEAR) == Action(Kind.END, Step.DONE, Outcome.UNCLEAR)


def test_silence_never_counts_as_consent():
    f = at_confirm()
    assert f.on_timeout() == Action(Kind.ASK, Step.CONFIRM)  # nudge: ask again
    assert f.on_timeout() == Action(Kind.END, Step.DONE, Outcome.INACTIVE)
    assert f.outcome is not Outcome.POSTED


def test_edit_starts_over_clearing_everything_said_so_far():
    f = at_confirm()
    assert f.on_intent(Intent.EDIT) == Action(Kind.RESTART, Step.Q1)
    assert f.step is Step.Q1 and f.answers == {} and f.followup_question is None
    f.on_answer("brand new first answer")
    assert f.answers == {"q1": "brand new first answer"}  # nothing leaked from the first attempt


def test_edit_is_limited_so_a_session_cannot_loop_forever():
    f = at_confirm()
    for _ in range(MAX_RESTARTS):
        assert f.on_intent(Intent.EDIT).kind is Kind.RESTART
        f.on_answer("one two three")
        f.on_answer("one two three")
        f.on_answer("one two three")
        f.on_answer("one two three")
        f.on_readback_done()
    assert f.on_intent(Intent.EDIT) == Action(Kind.END, Step.DONE, Outcome.CANCELLED)


# ---- what happens after the post ------------------------------------------------------------


@pytest.mark.parametrize(
    "result,outcome",
    [
        (PostResult.POSTED, Outcome.POSTED),
        (PostResult.UNKNOWN, Outcome.POST_UNKNOWN),
        (PostResult.REJECTED, Outcome.POST_FAILED),
    ],
)
def test_final_post_results_end_the_session(result, outcome):
    f = at_confirm()
    f.on_intent(Intent.CONFIRM)
    assert f.on_post_result(result) == Action(Kind.END, Step.DONE, outcome)


def test_an_unknown_outcome_is_final_and_can_never_be_re_posted():
    f = at_confirm()
    f.on_intent(Intent.CONFIRM)
    f.on_post_result(PostResult.UNKNOWN)
    for intent in Intent:
        assert f.on_intent(intent).kind is Kind.NOOP
    assert f.on_post_result(PostResult.RETRYABLE).kind is Kind.NOOP


def test_a_retryable_failure_asks_again_and_a_second_attempt_may_succeed():
    f = at_confirm()
    f.on_intent(Intent.CONFIRM)
    assert f.on_post_result(PostResult.RETRYABLE) == Action(Kind.ASK, Step.CONFIRM)
    assert f.step is Step.CONFIRM and f.api_state == "confirming"
    assert f.on_intent(Intent.CONFIRM).kind is Kind.REQUEST_POST  # needs a fresh, explicit yes
    assert f.on_post_result(PostResult.POSTED).outcome is Outcome.POSTED


def test_retries_are_capped():
    f = at_confirm()
    for _ in range(MAX_POST_ATTEMPTS - 1):
        f.on_intent(Intent.CONFIRM)
        assert f.on_post_result(PostResult.RETRYABLE).kind is Kind.ASK
    f.on_intent(Intent.CONFIRM)
    assert f.on_post_result(PostResult.RETRYABLE) == Action(
        Kind.END, Step.DONE, Outcome.POST_FAILED
    )


def test_post_results_outside_posting_are_ignored():
    f = at_confirm()
    for result in PostResult:
        assert f.on_post_result(result).kind is Kind.NOOP  # cannot "pretend" a post happened
    assert f.step is Step.CONFIRM


# ---- time -----------------------------------------------------------------------------------


@pytest.mark.parametrize("step_text", [0, 1, 2, 3])
def test_one_nudge_then_end_on_silence_during_the_interview(step_text):
    f = Flow()
    f.begin()
    for _ in range(step_text):
        f.on_answer("one two three")
    step = f.step
    assert f.on_timeout() == Action(Kind.REPROMPT, step)
    assert f.on_timeout() == Action(Kind.END, Step.DONE, Outcome.INACTIVE)


def test_the_hard_time_cap_ends_the_session_but_never_interrupts_a_post():
    f = at_confirm()
    assert f.on_hard_cap() == Action(Kind.END, Step.DONE, Outcome.TIME_LIMIT)

    posting = at_confirm()
    posting.on_intent(Intent.CONFIRM)
    assert posting.on_hard_cap().kind is Kind.NOOP  # let the in-flight post resolve
    assert posting.step is Step.POSTING
    assert posting.on_post_result(PostResult.POSTED).outcome is Outcome.POSTED


@pytest.mark.parametrize("step", [Step.GREETING, Step.READBACK, Step.POSTING])
def test_timeouts_are_ignored_where_the_student_is_not_expected_to_speak(step):
    f = Flow()
    f.step = step
    assert f.on_timeout().kind is Kind.NOOP


def test_a_student_leaving_ends_the_session_without_posting_but_never_interrupts_a_post():
    f = at_confirm()
    assert f.on_student_left() == Action(Kind.END, Step.DONE, Outcome.CANCELLED)

    posting = at_confirm()
    posting.on_intent(Intent.CONFIRM)
    assert posting.on_student_left().kind is Kind.NOOP and posting.step is Step.POSTING


def test_nothing_does_anything_after_the_session_is_done():
    f = at_confirm()
    f.on_intent(Intent.CANCEL)
    before = f.fingerprint()
    events = [
        lambda: f.begin(), lambda: f.on_answer(LONG), lambda: f.on_readback_done(),
        lambda: f.on_intent(Intent.CONFIRM), lambda: f.on_post_result(PostResult.POSTED),
        lambda: f.on_timeout(), lambda: f.on_hard_cap(),
    ]  # fmt: skip
    assert all(e().kind is Kind.NOOP for e in events)
    assert f.fingerprint() == before


def test_answers_are_ignored_when_not_in_an_answer_step():
    for step in (Step.GREETING, Step.READBACK, Step.CONFIRM, Step.POSTING, Step.DONE):
        f = Flow()
        f.step = step
        assert f.on_answer(LONG).kind is Kind.NOOP and f.answers == {}


# ---- exhaustive exploration of every reachable state ----------------------------------------

EVENTS = {
    "begin": lambda f: f.begin(),
    "answer_short": lambda f: f.on_answer("ok"),
    "answer_long": lambda f: f.on_answer(LONG),
    "answer_blank": lambda f: f.on_answer("   "),
    # (records the question; returns a bool, so adapt it to the Action the checks expect)
    "followup_question": lambda f: Action(
        Kind.AWAIT if f.on_followup_question("Why?") else Kind.NOOP, f.step
    ),
    "readback_done": lambda f: f.on_readback_done(),
    "intent_confirm": lambda f: f.on_intent(Intent.CONFIRM),
    "intent_edit": lambda f: f.on_intent(Intent.EDIT),
    "intent_cancel": lambda f: f.on_intent(Intent.CANCEL),
    "intent_unclear": lambda f: f.on_intent(Intent.UNCLEAR),
    "post_posted": lambda f: f.on_post_result(PostResult.POSTED),
    "post_retryable": lambda f: f.on_post_result(PostResult.RETRYABLE),
    "post_unknown": lambda f: f.on_post_result(PostResult.UNKNOWN),
    "post_rejected": lambda f: f.on_post_result(PostResult.REJECTED),
    "timeout": lambda f: f.on_timeout(),
    "hard_cap": lambda f: f.on_hard_cap(),
    "student_left": lambda f: f.on_student_left(),
}

# Every move between steps that is allowed to happen at all.
STEP_MOVES = {
    (Step.GREETING, Step.Q1),
    (Step.Q1, Step.Q2), (Step.Q2, Step.Q3), (Step.Q3, Step.FOLLOWUP),
    (Step.FOLLOWUP, Step.READBACK), (Step.READBACK, Step.CONFIRM),
    (Step.CONFIRM, Step.POSTING), (Step.CONFIRM, Step.Q1),  # confirm / edit (start over)
    (Step.POSTING, Step.CONFIRM),  # retryable failure -> ask again
    *[(s, Step.DONE) for s in Step if s is not Step.DONE],
}  # fmt: skip


def explore():
    start = Flow()
    seen = {start.fingerprint(): start}
    frontier = [start]
    transitions = []
    while frontier:
        before = frontier.pop()
        for name, event in EVENTS.items():
            after = before.clone()
            action = event(after)
            transitions.append((before, name, after, action))
            fp = after.fingerprint()
            if fp not in seen:
                seen[fp] = after
                frontier.append(after)
    return seen, transitions


@pytest.fixture(scope="module")
def explored():
    return explore()


def test_exploration_terminates_and_covers_the_whole_machine(explored):
    seen, transitions = explored
    assert len(seen) < 50_000  # finite, so the check below really is exhaustive
    assert {f.step for f in seen.values()} == set(Step)
    assert {f.outcome for f in seen.values()} == {None, *Outcome}
    assert len(transitions) == len(seen) * len(EVENTS)


def test_invariant_a_post_is_requested_only_by_a_confirm_in_the_confirm_step(explored):
    for before, name, _, action in explored[1]:
        if action.kind is Kind.REQUEST_POST:
            assert before.step is Step.CONFIRM and name == "intent_confirm"


def test_invariant_posting_is_entered_only_through_a_post_request(explored):
    for before, name, after, action in explored[1]:
        if after.step is Step.POSTING and before.step is not Step.POSTING:
            assert action.kind is Kind.REQUEST_POST and before.step is Step.CONFIRM, name


def test_invariant_posted_outcome_only_comes_from_a_successful_post_result(explored):
    for before, name, after, _ in explored[1]:
        if after.outcome is Outcome.POSTED and before.outcome is not Outcome.POSTED:
            assert before.step is Step.POSTING and name == "post_posted"


def test_invariant_the_log_is_read_back_only_when_all_four_answers_exist(explored):
    for before, _, after, action in explored[1]:
        if action.kind is Kind.READ_BACK:
            assert before.step is Step.FOLLOWUP
            assert set(after.answers) == {"q1", "q2", "q3", "followup_a"}
            assert all(text.strip() for text in after.answers.values())


def test_invariant_consent_requires_the_read_back_to_have_finished(explored):
    # Every state from which a post can be requested must have gone through READBACK -> CONFIRM.
    for before, _name, _, action in explored[1]:
        if action.kind is Kind.REQUEST_POST:
            assert set(before.answers) == {"q1", "q2", "q3", "followup_a"}


def test_invariant_ignored_events_change_nothing(explored):
    for before, _, after, action in explored[1]:
        if action.kind is Kind.NOOP:
            assert after.fingerprint() == before.fingerprint()


def test_invariant_a_finished_session_is_inert(explored):
    for before, _, after, action in explored[1]:
        if before.done:
            assert action.kind is Kind.NOOP and after.fingerprint() == before.fingerprint()


def test_invariant_steps_only_move_along_the_allowed_edges(explored):
    for before, name, after, _ in explored[1]:
        if after.step is not before.step:
            assert (before.step, after.step) in STEP_MOVES, (before.step, name, after.step)


def test_invariant_follow_up_is_requested_only_after_q3(explored):
    for before, _, _, action in explored[1]:
        if action.kind is Kind.ASK_FOLLOWUP:
            assert before.step is Step.Q3


def test_invariant_post_attempts_and_restarts_stay_within_their_caps(explored):
    for state in explored[0].values():
        assert state._post_attempts <= MAX_POST_ATTEMPTS
        assert state._restarts <= MAX_RESTARTS
        assert state._unclear <= MAX_UNCLEAR + 1


def test_invariant_outcome_is_set_exactly_when_the_session_is_done(explored):
    for state in explored[0].values():
        assert (state.outcome is not None) == state.done


def test_invariant_answers_contain_only_what_was_said(explored):
    allowed = {"ok", LONG}
    for state in explored[0].values():
        for text in state.answers.values():
            assert all(piece in allowed for piece in _split_parts(text))


def _split_parts(text: str) -> list[str]:
    """Break a stored answer back into the utterances it was joined from."""
    out, rest = [], text
    while rest:
        for candidate in (LONG, "ok"):
            if rest.startswith(candidate):
                out.append(candidate)
                rest = rest[len(candidate) :].lstrip(" ")
                break
        else:
            return ["<<unexpected text>>"]
    return out


def test_answer_steps_constant_matches_the_machine():
    assert ANSWER_STEPS == (Step.Q1, Step.Q2, Step.Q3, Step.FOLLOWUP)


# ---- per-step bookkeeping (found by mutation testing) ---------------------------------------


def test_every_question_gets_its_own_single_reprompt():
    f = Flow()
    f.begin()
    assert f.on_answer("ok").kind is Kind.REPROMPT  # Q1: reprompted
    assert f.on_answer("fine").kind is Kind.ASK  # Q1 accepted; moves to Q2
    assert f.on_answer("ok").kind is Kind.REPROMPT  # Q2 has a fresh reprompt of its own
    assert f.on_answer("fine").kind is Kind.ASK
    assert f.on_answer("ok").kind is Kind.REPROMPT  # ...and so does Q3


def test_speaking_again_restarts_the_silence_count_for_that_step():
    f = Flow()
    f.begin()
    assert f.on_timeout().kind is Kind.REPROMPT  # silent: nudge
    assert f.on_answer("ok").kind is Kind.REPROMPT  # they spoke (briefly): they are still there
    assert f.on_timeout().kind is Kind.REPROMPT  # a NEW silence gets its own nudge, not an exit
    assert f.on_timeout().kind is Kind.END


def test_unclear_replies_are_counted_afresh_after_a_failed_post_attempt():
    f = at_confirm()
    for _ in range(MAX_UNCLEAR):
        f.on_intent(Intent.UNCLEAR)  # nearly out of patience
    f.on_intent(Intent.CONFIRM)
    f.on_post_result(PostResult.RETRYABLE)  # back to confirming
    for _ in range(MAX_UNCLEAR):  # a full, fresh allowance of unclear replies
        assert f.on_intent(Intent.UNCLEAR).kind is Kind.ASK
    assert f.on_intent(Intent.UNCLEAR).kind is Kind.END

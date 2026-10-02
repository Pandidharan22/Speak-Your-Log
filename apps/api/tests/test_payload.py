import pytest

from app.payload import REQUIRED_FOR_POST, VERB, IncompleteDraft, build_payload, is_complete

FULL = {
    "q1": "I tried an IR sensor today",
    "q2": "it kept giving noisy readings",
    "q3": "because it was cheaper",
    "followup_q": "You said cheaper. Why does cost matter?",
    "followup_a": "ultrasonic was too slow for me",
}


def test_content_is_q1_then_q2_and_why_is_q3_then_the_follow_up_answer():
    p = build_payload(FULL)
    assert p.verb == VERB == "built"
    assert p.content == "I tried an IR sensor today it kept giving noisy readings"
    assert p.why == "because it was cheaper ultrasonic was too slow for me"


def test_there_are_no_labels_headings_or_extra_words():
    p = build_payload(FULL)
    assert p.content == f"{FULL['q1']} {FULL['q2']}"
    assert p.why == f"{FULL['q3']} {FULL['followup_a']}"


def test_only_the_ends_are_trimmed_the_words_inside_stay_exactly_as_heard():
    answers = {
        "q1": "  சென்சார் ரீடிங் சரியாவே வரல,  so I   switched. \n",
        "q2": "\tIR sensor,Because  ultrasonic WAS noisy ",
        "q3": " ஏன்னா it was cheaper ",
        "followup_a": "  ultrasonic   was too slow  ",
    }
    p = build_payload(answers)
    assert (
        p.content
        == "சென்சார் ரீடிங் சரியாவே வரல,  so I   switched. IR sensor,Because  ultrasonic WAS noisy"
    )
    assert p.why == "ஏன்னா it was cheaper ultrasonic   was too slow"  # double spaces and case intact


def test_the_follow_up_question_is_an_audit_trail_and_never_part_of_the_post():
    a, b = dict(FULL), dict(FULL, followup_q="a completely different question?")
    assert build_payload(a) == build_payload(b)
    assert FULL["followup_q"] not in build_payload(FULL).why


def test_extra_keys_cannot_inject_text_into_the_post():
    p = build_payload(FULL | {"verb": "decided", "content": "EVIL", "why": "EVIL", "x": "EVIL"})
    assert "EVIL" not in p.content + p.why and p.verb == "built"


@pytest.mark.parametrize("missing", REQUIRED_FOR_POST)
@pytest.mark.parametrize("bad", [None, "", "   ", "\n\t", 0, ["x"], {"a": 1}])
def test_every_required_answer_must_be_present_and_non_blank(missing, bad):
    answers = dict(FULL)
    answers[missing] = bad
    with pytest.raises(IncompleteDraft) as exc:
        build_payload(answers)
    assert str(exc.value) == missing and not is_complete(answers)


@pytest.mark.parametrize("missing", REQUIRED_FOR_POST)
def test_an_absent_key_is_incomplete_too(missing):
    answers = {k: v for k, v in FULL.items() if k != missing}
    assert not is_complete(answers)


def test_an_empty_draft_is_incomplete_and_a_full_one_is_complete():
    assert not is_complete({}) and is_complete(FULL)


def test_the_builder_is_deterministic():
    assert build_payload(FULL) == build_payload(dict(FULL))


def test_the_builder_does_not_mutate_its_input():
    answers = {k: f"  {v}  " for k, v in FULL.items()}
    before = dict(answers)
    build_payload(answers)
    assert answers == before

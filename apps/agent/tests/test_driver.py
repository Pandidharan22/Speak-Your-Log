"""The driver and the step scripts, tested with a fake voice (no network, no model)."""

import asyncio
import logging

import pytest

from interview.driver import InterviewDriver
from interview.flow import Flow, Step
from interview.prompts import GREETING_INSTRUCTIONS, SYSTEM_INSTRUCTIONS
from interview.script import QUESTIONS, armed_instructions


class FakeVoice:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def set_instructions(self, text: str) -> None:
        self.calls.append(("set", text))

    async def speak(self, instructions: str) -> None:
        self.calls.append(("speak", instructions))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.calls]


def run(coro):
    return asyncio.run(coro)


async def started_driver(voice: FakeVoice | None = None) -> tuple[InterviewDriver, FakeVoice]:
    voice = voice or FakeVoice()
    driver = InterviewDriver(voice)
    await driver.start()
    return driver, voice


# ---- starting ---------------------------------------------------------------------------------


def test_start_greets_without_touching_instructions():
    # Updating instructions right before the first utterance loses the greeting (found live).
    async def go():
        driver, voice = await started_driver()
        assert driver.flow.step is Step.Q1
        assert voice.calls == [("speak", GREETING_INSTRUCTIONS)]

    run(go())


def test_the_model_is_created_already_armed_for_the_answer_to_q1():
    assert InterviewDriver.initial_instructions() == armed_instructions(Step.Q1)


# ---- the scripted interview -------------------------------------------------------------------


def test_each_finished_answer_rearms_the_model_for_the_next_turn_and_never_speaks():
    async def go():
        driver, voice = await started_driver()
        voice.calls.clear()
        for answer, expected_step in [
            ("I tried an IR sensor today", Step.Q2),
            ("it kept giving noisy readings", Step.Q3),
            ("because it was cheaper", Step.FOLLOWUP),
        ]:
            await driver.on_student_turn(answer)
            assert driver.flow.step is expected_step
        # the model replied on its own (pre-armed); the driver only re-arms
        assert voice.calls == [
            ("set", armed_instructions(Step.Q2)),
            ("set", armed_instructions(Step.Q3)),
            ("set", armed_instructions(Step.FOLLOWUP)),
        ]

    run(go())


def test_answers_are_captured_verbatim_per_step():
    async def go():
        driver, _ = await started_driver()
        said = [
            "  சென்சார் ரீடிங் சரியாவே வரல, so I  switched.  ",
            "IR sensor, because ultrasonic was noisy.",
            "ஏன்னா it was cheaper",
            "ultrasonic was too slow for me",
        ]
        for text in said:
            await driver.on_student_turn(text)
        assert driver.flow.answers == {
            "q1": said[0].strip(),
            "q2": said[1],
            "q3": said[2],
            "followup_a": said[3],
        }

    run(go())


def test_a_one_word_answer_is_accepted_without_a_reprompt():
    async def go():
        driver, voice = await started_driver()
        voice.calls.clear()
        await driver.on_student_turn("ok")  # the model has already moved on; so must we
        assert driver.flow.step is Step.Q2 and driver.flow.answers["q1"] == "ok"

    run(go())


def test_after_the_follow_up_answer_the_driver_speaks_once_to_close_the_early_flow():
    async def go():
        driver, voice = await started_driver()
        for text in ["one two three", "four five six", "seven eight nine", "ten eleven twelve"]:
            await driver.on_student_turn(text)
        assert driver.flow.step is Step.READBACK
        assert voice.kinds().count("speak") == 2  # the greeting + the single placeholder closing

    run(go())


# ---- ignoring what must be ignored ------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_turns_change_nothing(blank):
    async def go():
        driver, voice = await started_driver()
        before = (driver.flow.fingerprint(), list(voice.calls))
        await driver.on_student_turn(blank)
        assert (driver.flow.fingerprint(), voice.calls) == before

    run(go())


def test_turns_after_the_session_ends_are_ignored():
    async def go():
        driver, voice = await started_driver()
        driver.flow.on_hard_cap()
        before = list(voice.calls)
        await driver.on_student_turn("anything at all here")
        assert voice.calls == before and driver.flow.answers == {}

    run(go())


def test_turns_while_not_collecting_answers_are_ignored_until_consent_handling_exists():
    async def go():
        driver, voice = await started_driver()
        for text in ["one two three"] * 4:
            await driver.on_student_turn(text)
        before = (driver.flow.fingerprint(), list(voice.calls))
        await driver.on_student_turn("yes, post it")  # must NOT be taken as an answer or consent
        assert (driver.flow.fingerprint(), voice.calls) == before

    run(go())


def test_simultaneous_turns_are_processed_strictly_in_order():
    async def go():
        driver, _ = await started_driver()
        await asyncio.gather(
            driver.on_student_turn("first answer words"),
            driver.on_student_turn("second answer words"),
            driver.on_student_turn("third answer words"),
        )
        assert [driver.flow.answers[k] for k in ("q1", "q2", "q3")] == [
            "first answer words",
            "second answer words",
            "third answer words",
        ]

    run(go())


def test_logs_carry_step_names_and_word_counts_but_never_the_students_words(caplog):
    caplog.set_level(logging.DEBUG)

    async def go():
        driver, _ = await started_driver()
        await driver.on_student_turn("my private secret sentence about the robot")

    run(go())
    assert "answer_recorded step=q1 words=7" in caplog.text
    assert "private" not in caplog.text and "robot" not in caplog.text


def test_driver_defaults_to_no_live_reprompt_but_accepts_a_custom_flow():
    assert InterviewDriver(FakeVoice()).flow._min_words == 0
    custom = Flow(min_words=3)
    assert InterviewDriver(FakeVoice(), flow=custom).flow is custom


# ---- the step scripts -------------------------------------------------------------------------


def test_every_armed_instruction_carries_the_system_rules_and_names_its_step():
    for step in (
        Step.Q1,
        Step.Q2,
        Step.Q3,
        Step.FOLLOWUP,
        Step.READBACK,
        Step.CONFIRM,
        Step.POSTING,
    ):
        text = armed_instructions(step)
        assert text.startswith(SYSTEM_INSTRUCTIONS) and f"Current step: {step.value}." in text


def test_q1_and_q2_are_armed_to_ask_the_next_scripted_question_exactly():
    q1, q2 = armed_instructions(Step.Q1), armed_instructions(Step.Q2)
    assert QUESTIONS[Step.Q2][0] in q1 and QUESTIONS[Step.Q3][0] not in q1
    assert QUESTIONS[Step.Q3][0] in q2 and QUESTIONS[Step.Q2][0] not in q2
    for text in (q1, q2):
        assert "ask exactly this next question" in text and "Ask only that question" in text


def test_tamil_renderings_are_offered_for_tamil_speaking_students():
    assert QUESTIONS[Step.Q2][1] in armed_instructions(Step.Q1)
    assert QUESTIONS[Step.Q3][1] in armed_instructions(Step.Q2)


def test_q3_is_armed_for_exactly_one_quoting_follow_up_question():
    text = armed_instructions(Step.Q3)
    assert "exactly ONE follow-up question" in text
    assert "quote a specific word or phrase the student actually said" in text
    assert "Ask only that one question" in text


@pytest.mark.parametrize("step", [Step.FOLLOWUP, Step.READBACK, Step.CONFIRM, Step.POSTING])
def test_branching_steps_are_armed_with_a_neutral_filler_only(step):
    step_part = armed_instructions(step).split("Current step:", 1)[1]  # not the shared system rules
    assert "only a very short neutral acknowledgement" in step_part
    assert "nothing about saving or posting" in step_part
    assert "ask exactly" not in step_part.lower()  # the driver, not the model, decides what's next
    assert "?" not in step_part.replace('"Okay."', "")  # no question for the model to ask


def test_the_scripted_questions_match_the_brief():
    assert QUESTIONS[Step.Q1][0] == "What did you try today?"
    assert QUESTIONS[Step.Q2][0] == "What broke?"
    assert QUESTIONS[Step.Q3][0] == "Why did you choose that?"

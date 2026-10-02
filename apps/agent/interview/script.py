"""What the model is told at each step. Pure text builders: no I/O, easy to review and test.

Gemini Live replies on its own the moment it decides the student has finished, and the student's
final transcript only reaches us AFTER that reply has been spoken (measured in step 3.4, ADR-008).
So the model cannot be steered *in the moment*; it has to be **pre-armed**: while the student is
answering question N, its instructions already say what to do when they stop. After each finished
answer the driver re-arms for the next turn. At branching moments (read-back, confirmation) the
pre-armed reply is only a neutral filler; the driver then speaks the right thing explicitly.
"""

from interview.flow import Step
from interview.prompts import SYSTEM_INSTRUCTIONS

# The scripted questions (SRS FR-6) with natural Tamil renderings the model may use if the student
# speaks Tamil. The model chooses the language from how the student speaks.
QUESTIONS = {
    Step.Q1: ("What did you try today?", "இன்னைக்கு என்ன try பண்ணீங்க?"),
    Step.Q2: ("What broke?", "என்ன சரியா வேலை செய்யல?"),
    Step.Q3: ("Why did you choose that?", "ஏன் அதை choose பண்ணீங்க?"),
}

_NEXT_QUESTION = {Step.Q1: Step.Q2, Step.Q2: Step.Q3}

_NEUTRAL = (
    "Reply with only a very short neutral acknowledgement in the student's language, such as "
    '"Okay." or "Hmm, okay." Say nothing else: no questions, and nothing about saving or posting.'
)


def _question(step: Step) -> str:
    english, tamil = QUESTIONS[step]
    return (
        f'"{english}" If the student is speaking Tamil, ask it naturally in Tamil '
        f'(for example "{tamil}"); if they are mixing languages, mix naturally; otherwise ask it '
        "in English. Keep the meaning exactly the same."
    )


def armed_instructions(step: Step) -> str:
    """Full instructions for the period in which the student is answering the question for `step`
    (i.e. the question for `step` has just been asked)."""
    if step in _NEXT_QUESTION:
        nxt = _NEXT_QUESTION[step]
        turn = (
            "When the student finishes answering, give a brief, natural acknowledgement of at most "
            f"six words that does not repeat their words, then ask exactly this next question: "
            f"{_question(nxt)} Ask only that question."
        )
    elif step is Step.Q3:
        turn = (
            "When the student finishes answering, give a brief acknowledgement of at most six "
            "words, then ask exactly ONE follow-up question. It must quote a specific word or "
            "phrase the student actually said earlier in this conversation, in their own words, "
            'and ask why or how, for example: "You said you switched to an IR sensor. Why that '
            'over the ultrasonic one?" Ask only that one question.'
        )
    else:  # FOLLOWUP, READBACK, CONFIRM, POSTING: the next move is the driver's, not the model's
        turn = _NEUTRAL
    return f"{SYSTEM_INSTRUCTIONS}\n\nCurrent step: {step.value}. {turn}"

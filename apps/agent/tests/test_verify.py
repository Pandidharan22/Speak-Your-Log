"""Verifying from the model's transcript that the log was really read to the student."""

import pytest

from interview.verify import MIN_COVERAGE, coverage, was_spoken

LOG = "Today I tried an ultrasonic sensor. The readings were noisy, so I switched to an IR sensor."


def test_the_log_read_exactly_is_fully_covered():
    assert coverage(LOG, LOG) == 1.0 and was_spoken(LOG, LOG)


def test_case_and_punctuation_do_not_matter():
    spoken = (
        "today i tried an ULTRASONIC sensor the readings were noisy so i switched to an ir sensor"
    )
    assert coverage(spoken, LOG) == 1.0


def test_the_model_adding_its_own_framing_still_counts():
    spoken = "Here is what you said. " + LOG + " This will be public. Should I post it?"
    assert was_spoken(spoken, LOG)


def test_a_transcript_that_is_slightly_incomplete_is_tolerated():
    spoken = "Today I tried an ultrasonic sensor. The readings were noisy, so I switched"
    assert coverage(spoken, LOG) >= MIN_COVERAGE


@pytest.mark.parametrize("spoken", ["", "Okay.", "Hmm, okay.", "Do you want to post it?"])
def test_a_filler_or_silence_is_not_a_read_back(spoken):
    assert not was_spoken(spoken, LOG)


def test_only_half_the_log_is_not_enough():
    spoken = "Today I tried an ultrasonic sensor. The readings"
    assert not was_spoken(spoken, LOG)


def test_repeated_words_are_counted_each_time_they_are_expected():
    # "very" three times expected, said once: must not count as covered by one occurrence.
    assert coverage("very", "very very very") == pytest.approx(1 / 3)


def test_tamil_text_is_compared_word_by_word():
    log = "இன்னைக்கு நான் ஒரு ரோபோவை லைன் ஃபாலோ பண்ண வைக்க முயற்சி பண்ணேன்."
    assert was_spoken("ம். " + log, log)
    assert not was_spoken("சரி, நன்றி", log)


def test_nothing_expected_means_nothing_to_verify():
    assert coverage("anything", "") == 1.0


def test_tamil_vowel_signs_and_virama_are_part_of_the_word_not_punctuation():
    # "பண்ணேன்" (I did) and "பண்ணேன" differ only by the final virama: different words.
    assert coverage("பண்ணேன", "பண்ணேன்") == 0.0
    assert coverage("பண்ணேன்.", "பண்ணேன்") == 1.0

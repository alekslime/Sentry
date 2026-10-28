"""Tests for the screen-context decision (`vision/triggers.py`) and the
LLM yes/no fallback (`llm/screen_intent.py`). No model: a fake stands in.
"""

from __future__ import annotations

from vision.triggers import decide_screen_context, keyword_match
from llm.screen_intent import SCREEN_INTENT_INSTRUCTIONS, needs_screen

KEYWORDS = ["screen", "see", "look", "this", "here"]


class FakeClassifier:
    """Records calls; answers from a fixed value or raises."""

    def __init__(self, answer: bool = False, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def classify_yes_no(self, instructions: str, user_text: str) -> bool:
        self.calls.append((instructions, user_text))
        if self.error:
            raise self.error
        return self.answer


# --- keyword_match / decide_screen_context ---------------------------------


def test_keyword_match_is_case_insensitive() -> None:
    assert keyword_match("Look at THIS", KEYWORDS)
    assert not keyword_match("what is the capital of France", KEYWORDS)
    assert not keyword_match("anything", [])


def test_keyword_match_allows_common_endings() -> None:
    assert keyword_match("it looks off", ["look"])
    assert keyword_match("I am looking at it", ["look"])
    assert keyword_match("she looked twice", ["look"])
    assert keyword_match("what do you see", ["see"])
    assert keyword_match("he sees it", ["see"])
    assert keyword_match("seeing red", ["see"])
    assert keyword_match("take a screenshot", ["screen"])
    assert keyword_match("two screenshots", ["screen"])
    assert keyword_match("where is it located", ["locate"])
    assert keyword_match("can you show me the button", ["show me"])


def test_keyword_does_not_match_words_that_only_start_with_it() -> None:
    # Each of these used to trigger a full capture + caption + OCR pass.
    assert not keyword_match("how do I seed a lawn", KEYWORDS)
    assert not keyword_match("I can't seem to focus today", KEYWORDS)
    assert not keyword_match("tell me about thistles", KEYWORDS)
    assert not keyword_match("what does hereby mean", KEYWORDS)


def test_see_plus_d_is_not_seed_but_locate_plus_d_is_located() -> None:
    assert not keyword_match("seed", ["see"])
    assert keyword_match("located", ["locate"])


def test_irregular_forms_need_to_be_listed_explicitly() -> None:
    assert not keyword_match("have you seen it", ["see"])
    assert keyword_match("have you seen it", ["see", "seen"])


def test_decide_screen_context_ignores_a_prefix_only_hit() -> None:
    decision = decide_screen_context("how do I seed a lawn", KEYWORDS)
    assert decision.use_screen is False
    assert decision.reason == "no keyword match"


def test_keyword_does_not_match_inside_other_words() -> None:
    # "where" and "there" contain "here" but should not trigger vision.
    assert not keyword_match("where is the Eiffel Tower", KEYWORDS)
    assert not keyword_match("is there a faster way", KEYWORDS)
    assert not keyword_match("a relocated file", ["locate"])
    # a real hit still works
    assert keyword_match("what is here", KEYWORDS)


def test_blank_keywords_are_ignored() -> None:
    assert not keyword_match("anything at all", ["", "  "])


def test_empty_keywords_means_always_on_and_skips_classifier() -> None:
    calls: list[str] = []
    decision = decide_screen_context("hi", [], lambda q: calls.append(q) or False)
    assert decision.use_screen is True
    assert calls == []


def test_keyword_match_never_calls_the_classifier() -> None:
    calls: list[str] = []
    decision = decide_screen_context("what is this", KEYWORDS, lambda q: calls.append(q) or False)
    assert decision.use_screen is True
    assert decision.reason == "keyword match"
    assert calls == []


def test_no_keyword_and_no_classifier_skips_the_screen() -> None:
    decision = decide_screen_context("what's wrong with my timeline", KEYWORDS)
    assert decision.use_screen is False
    assert decision.reason == "no keyword match"


def test_classifier_yes_catches_what_keywords_miss() -> None:
    question = "what's wrong with my timeline?"
    asked: list[str] = []

    def classifier(q: str) -> bool:
        asked.append(q)
        return True

    decision = decide_screen_context(question, KEYWORDS, classifier)

    assert decision.use_screen is True
    assert asked == [question]


def test_classifier_no_skips_the_screen() -> None:
    decision = decide_screen_context("tell me a joke", KEYWORDS, lambda q: False)
    assert decision.use_screen is False
    assert "classifier said no" in decision.reason


# --- needs_screen ------------------------------------------------------------


def test_needs_screen_passes_instructions_and_text_to_engine() -> None:
    engine = FakeClassifier(answer=True)
    assert needs_screen(engine, "is my photo too dark?") is True
    assert engine.calls == [(SCREEN_INTENT_INSTRUCTIONS, "is my photo too dark?")]


def test_needs_screen_returns_false_for_no_answer() -> None:
    assert needs_screen(FakeClassifier(answer=False), "tell me a joke") is False


def test_needs_screen_skips_the_engine_for_blank_text() -> None:
    engine = FakeClassifier(answer=True)
    assert needs_screen(engine, "   ") is False
    assert engine.calls == []


def test_needs_screen_treats_engine_failure_as_no() -> None:
    engine = FakeClassifier(error=RuntimeError("inference blew up"))
    assert needs_screen(engine, "what's on my screen") is False


def test_needs_screen_accepts_alternative_instructions() -> None:
    engine = FakeClassifier(answer=True)
    assert needs_screen(engine, "is my photo too dark?", instructions="custom prompt") is True
    assert engine.calls == [("custom prompt", "is my photo too dark?")]


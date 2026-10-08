"""Asks the local LLM whether a question needs to look at the user's screen.

Used as the fallback behind the keyword check in `vision/triggers.py`: only
queries that matched no keyword get here, so it costs one tiny extra LLM call
(a few tokens out) on those queries and nothing on the rest.

Any failure counts as "no". Looking at the screen is an extra, never worth
breaking a query over, and "no" is exactly what the app did before this existed.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)

SCREEN_INTENT_INSTRUCTIONS = (
    "You decide whether answering a user's spoken question requires looking at "
    "their computer screen. Reply with exactly one word: yes or no.\n\n"
    "Say yes when the question is about what the user is currently working on or "
    "looking at: their open app, document, code, photo, video timeline, an error "
    "message, a button or setting, or a request to review, critique, fix or "
    "explain something of theirs.\n"
    "Say no for general knowledge, math, definitions, writing help, small talk, "
    "jokes, the time or weather, and anything that does not depend on what is "
    "on their screen.\n\n"
    "Examples:\n"
    "What's wrong with my timeline? -> yes\n"
    "Is my photo too dark? -> yes\n"
    "Why am I getting this error? -> yes\n"
    "Can you review my code? -> yes\n"
    "Which tab should I click to export? -> yes\n"
    "What's the capital of France? -> no\n"
    "Tell me a joke. -> no\n"
    "How do I say thank you in Spanish? -> no\n"
    "What is 15 percent of 80? -> no\n"
    "Hello, how are you? -> no"
)


class YesNoClassifier(Protocol):
    def classify_yes_no(self, instructions: str, user_text: str) -> bool: ...


def needs_screen(engine: YesNoClassifier, text: str) -> bool:
    """True if the LLM thinks `text` needs the screen. False on any failure."""
    if not text.strip():
        return False
    try:
        answer = engine.classify_yes_no(SCREEN_INTENT_INSTRUCTIONS, text)
    except Exception:
        logger.exception("Screen-intent classifier failed; treating the query as not needing the screen.")
        return False
    logger.info("Screen-intent classifier for %r: %s", text, "yes" if answer else "no")
    return answer

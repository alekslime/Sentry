"""Point at on-screen text using Tesseract word boxes.

This replaces the MiniCPM-V `locate()` head as Sentry's pointing
mechanism: the VLM head scored a mean IoU of 0.000 over 20 hardware
trials (see docs/DECISIONS.md "VLM locate() does not localize"), while
OCR word boxes ground visible text exactly. The trade-off is scope —
only text targets ("the Export button", "the error message") can be
pointed at, never icons or nameless shapes. Anything that does not
match returns None so the caller falls back to its retry prompt instead
of clicking a hallucination.

Coordinates are pixels in the image the boxes were read from.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from vision.ocr import WordBox

# Spoken-query filler: verbs and function words that carry no target
# information. Deliberately small — an over-long list risks stripping a
# word the user actually wants pointed at (e.g. "button"). Matching is
# case-insensitive; everything here is lowercase.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "can",
        "click",
        "do",
        "find",
        "for",
        "here",
        "is",
        "it",
        "look",
        "me",
        "my",
        "on",
        "or",
        "please",
        "point",
        "see",
        "show",
        "that",
        "the",
        "there",
        "this",
        "to",
        "where",
        "you",
    }
)

# Minimum SequenceMatcher ratio for a fuzzy (non-substring) match.
# Substring matches short-circuit at 0.9 and exact matches at 1.0, so
# this threshold only admits close misspellings / OCR confusions
# ("Exprt" vs "Export" ratios ~0.91), not unrelated words.
FUZZY_THRESHOLD = 0.55


@dataclass(frozen=True)
class TextMatch:
    """The OCR word box a query resolved to, plus its pixel centre."""

    word: WordBox
    score: float  # 0..1: 1.0 exact, 0.9 substring, else fuzzy ratio
    center_x: int
    center_y: int


def extract_target_words(query: str) -> list[str]:
    """Split a spoken query into candidate target words.

    Lowercases, strips punctuation, drops stopwords and tokens shorter
    than 3 characters (single letters and "OK"/"UI"-style fragments
    match far too promiscuously to point at).
    """
    words: list[str] = []
    for raw in query.lower().split():
        cleaned = "".join(char for char in raw if char.isalnum())
        if len(cleaned) >= 3 and cleaned not in _STOPWORDS:
            words.append(cleaned)
    return words


def _score(target: str, candidate: str) -> float:
    if not target or not candidate:
        return 0.0
    if target == candidate:
        return 1.0
    if target in candidate or candidate in target:
        return 0.9
    return SequenceMatcher(None, target, candidate).ratio()


def locate_text(
    words: list[WordBox], query: str, *, threshold: float = FUZZY_THRESHOLD
) -> TextMatch | None:
    """Find the on-screen word box best matching a spoken query.

    Scores every box against every target word from `query` and
    returns the highest-scoring box at or above `threshold`, breaking
    score ties by OCR confidence. Returns None when there is no
    defensible match — the caller must treat that as "not found", not
    as permission to guess.
    """
    targets = extract_target_words(query)
    if not targets or not words:
        return None
    best: TextMatch | None = None
    for word in words:
        candidate = word.text.lower()
        score = max(_score(target, candidate) for target in targets)
        if score < threshold:
            continue
        if (
            best is None
            or score > best.score
            or (score == best.score and word.confidence > best.word.confidence)
        ):
            best = TextMatch(
                word=word,
                score=score,
                center_x=word.left + word.width // 2,
                center_y=word.top + word.height // 2,
            )
    return best

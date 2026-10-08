"""Decides whether a query should get screen context (capture + caption + OCR).

Pure logic, no models or I/O, so it is unit-tested on its own. `main.py` calls
`decide_screen_context()` once per query.

Order of checks:
  1. No keywords configured: always use the screen (the old "run on every
     query" setting).
  2. A keyword appears in the query: use the screen. This is the fast path and
     costs nothing, so it always runs first.
  3. No keyword matched and a classifier was given: ask it. This is what
     catches questions like "what's wrong with my timeline?" that never say
     "this" or "here".
  4. Otherwise: skip the screen.
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Sequence


class TriggerDecision(NamedTuple):
    use_screen: bool
    reason: str  # short, for logs


def keyword_match(text: str, keywords: Sequence[str]) -> bool:
    """Case-insensitive substring match, same rule the app always used."""
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


def decide_screen_context(
    text: str,
    keywords: Sequence[str],
    classifier: Callable[[str], bool] | None = None,
) -> TriggerDecision:
    if not keywords:
        return TriggerDecision(True, "no trigger keywords configured (always on)")
    if keyword_match(text, keywords):
        return TriggerDecision(True, "keyword match")
    if classifier is not None:
        if classifier(text):
            return TriggerDecision(True, "classifier said the question needs the screen")
        return TriggerDecision(False, "no keyword match; classifier said no")
    return TriggerDecision(False, "no keyword match")

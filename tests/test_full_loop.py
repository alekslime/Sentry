"""Full-loop regression test: one headless turn through the real engines.

Needs real models, Tesseract, and a screen to capture, so the whole
module skips unless every requirement is present (always skipped in CI).
On a machine with models this runs the actual voice-to-answer path via
`run_turn.py`: capture -> vision caption + OCR -> LLM answer -> TTS
synthesis -> STT round-trip -> history persist.
"""

from __future__ import annotations

import re

import pytest

try:
    import run_turn
except ImportError as exc:
    pytest.skip(f"full-loop test needs all extras installed: {exc}", allow_module_level=True)

from config.settings import load_settings
from memory.store import ConversationStore

_settings = load_settings()
_missing = run_turn.check_models(_settings, need_vision=True, need_stt=True)
if not _settings.vision.enabled:
    _missing.append("vision.enabled=false in live config")
if _missing:
    pytest.skip(f"full-loop test needs models: {', '.join(_missing)}", allow_module_level=True)


def _content_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) >= 3}


def test_full_voice_loop_text_in_answer_out(tmp_path) -> None:
    # "this" triggers screen context, "where" triggers the pointing
    # report -- one turn covers the whole pipeline. History goes to a
    # temp database, never the real conversations.db.
    store = ConversationStore(tmp_path / "loop.db")
    result = run_turn.run_turn(
        "Where is the error on this screen?",
        _settings,
        store=store,
        round_trip=True,
    )

    assert result.answer.strip(), "LLM answer is empty"
    assert result.audio_seconds > 0, "no speech was synthesized"
    for stage in ("vision=", "llm=", "tts=", "total="):
        assert stage in result.timing, f"timing line missing {stage}: {result.timing!r}"
    assert result.point_report is not None, "pointing question produced no point report"
    assert result.heard, "STT heard nothing from its own TTS"

    expected = _content_words(result.answer)
    overlap = len(expected & _content_words(result.heard)) / max(1, len(expected))
    assert (
        overlap >= 0.25
    ), f"STT barely heard its own TTS (overlap {overlap:.2f}): {result.heard!r}"

    turns = store.get_recent_turns(limit=5)
    assert len(turns) == 1
    assert turns[0]["query"] == "Where is the error on this screen?"
    assert turns[0]["response"] == result.answer

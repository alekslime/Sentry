"""Tests for vision/text_locate.py — OCR-box text pointing.

Uses synthetic WordBoxes throughout: matching logic must not depend on
the real Tesseract binary (hardware accuracy is measured separately by
/tmp/eval_text_locate.py against rendered scenes with known truth).
"""

import pytest

pytest.importorskip("pytesseract", reason="vision extra (pytesseract) not installed")
pytest.importorskip("PIL", reason="vision extra (Pillow) not installed")

# These imports follow the importorskip guards -- the vision extra is
# optional and Sentry degrades without it.
from vision.ocr import WordBox
from vision.text_locate import TextMatch, extract_target_words, locate_text


def _box(text: str, left: int = 100, top: int = 50, conf: float = 90.0) -> WordBox:
    return WordBox(text=text, confidence=conf, left=left, top=top, width=80, height=20)


def test_exact_single_word_match_reports_word_centre() -> None:
    words = [_box("Cancel", 10, 10), _box("Export", 100, 50), _box("Help", 300, 10)]

    match = locate_text(words, "Export")

    assert isinstance(match, TextMatch)
    assert match.word.text == "Export"
    assert (match.center_x, match.center_y) == (140, 60)


def test_full_spoken_query_matches_its_content_word() -> None:
    words = [_box("Cancel", 10, 10), _box("Export", 100, 50)]

    match = locate_text(words, "where is the export button")

    assert match is not None
    assert match.word.text == "Export"


def test_matching_ignores_case() -> None:
    assert locate_text([_box("Settings")], "SETTINGS") is not None


def test_returns_none_when_nothing_matches() -> None:
    words = [_box("Cancel", 10, 10), _box("Export", 100, 50)]

    assert locate_text(words, "printer") is None


def test_returns_none_for_empty_inputs() -> None:
    assert locate_text([], "Export") is None
    assert locate_text([_box("Export")], "") is None
    assert locate_text([_box("Export")], "where is the") is None


def test_prefers_exact_match_over_fuzzy_match() -> None:
    words = [_box("Exports", 10, 10, conf=99.0), _box("Export", 100, 50, conf=60.0)]

    match = locate_text(words, "Export")

    assert match is not None
    assert match.word.text == "Export"


def test_extract_target_words_drops_stopwords_and_punctuation() -> None:
    assert extract_target_words("Where is the Export button?") == ["export", "button"]
    assert extract_target_words("find it") == []

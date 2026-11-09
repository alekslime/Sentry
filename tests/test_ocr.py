"""Tests for `vision/ocr.py`.

`pytesseract` is only a wrapper around a system-level Tesseract install,
so `OCRReader.__init__` must fail loudly and helpfully when that binary
isn't found -- covered here without mocking, since a missing/wrong path
is a real thing this test can reproduce directly. Sentry's own confidence-
filtering logic (`OCRReader.read`) is covered separately with a mocked
`pytesseract.image_to_data`, so it doesn't depend on Tesseract's actual
recognition accuracy.

NOTE: this file previously imported a free function `extract_text()` that
no longer exists -- `vision/ocr.py` exposes an `OCRReader` class instead
(construct once via `__init__`, call `.read()` per image), which left
this test file silently broken (ImportError on collection) with nothing
exercising it. Rewritten from scratch against the current module.
"""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("pytesseract", reason="vision extra (pytesseract) not installed")
pytest.importorskip("PIL", reason="vision extra (Pillow) not installed")

# These imports follow the importorskip guards -- the vision extra is
# optional and Sentry degrades without it.
import pytesseract
from PIL import Image

from vision.ocr import OCRReader

# The end-to-end test below needs the real Tesseract *binary* (pytesseract
# is only a wrapper around it), which is a system install, not a pip
# package: `apt install tesseract-ocr` on Linux, the installer from
# https://github.com/tesseract-ocr/tesseract on Windows (CI provisions
# both). Skips with an explicit reason where it is missing.
requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract binary not on PATH (install Tesseract OCR to run this test)",
)


@pytest.fixture(autouse=True)
def _restore_tesseract_cmd():
    """`pytesseract.pytesseract.tesseract_cmd` is process-global state --
    without resetting it after each test, a fake path set by one test
    (e.g. the missing-binary test below) would leak into every later
    test in this file, including the real end-to-end one.
    """
    original = pytesseract.pytesseract.tesseract_cmd
    yield
    pytesseract.pytesseract.tesseract_cmd = original


def test_raises_runtime_error_when_tesseract_binary_missing(monkeypatch) -> None:
    # No explicit tesseract_cmd, and "tesseract" isn't found on PATH --
    # exercises OCRReader.__init__'s elif branch (shutil.which check),
    # not the explicit-path branch below.
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)

    with pytest.raises(RuntimeError, match="Tesseract OCR engine not found"):
        OCRReader()


def _load_test_font():
    """First usable TrueType font for rendering OCR-legible test text.

    PIL's default bitmap font is too small/blurry for Tesseract to read
    reliably -- a real TrueType font at a larger size is needed for a
    stable result. Skips the test when no candidate exists rather than
    failing on an illegible rendering.
    """
    from PIL import ImageFont

    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
        r"C:\Windows\Fonts\arial.ttf",  # Windows
    ):
        try:
            return ImageFont.truetype(candidate, 28)
        except OSError:
            continue
    pytest.skip("no TrueType test font found (tried DejaVu Sans-Bold, Arial)")


@requires_tesseract
def test_real_tesseract_extracts_rendered_text() -> None:
    # Exercises the actual OCR engine end-to-end rather than a mock,
    # unlike everything else in this file. A real-engine smoke test, not a
    # test of Sentry's own logic (that's covered below).
    from PIL import ImageDraw

    image = Image.new("RGB", (300, 60), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), "Hello Sentry", fill=(0, 0, 0), font=_load_test_font())

    reader = OCRReader(min_confidence=0)
    result = reader.read(image)

    assert "Sentry" in result


def test_filters_out_words_below_confidence_threshold(monkeypatch) -> None:
    monkeypatch.setattr(
        pytesseract,
        "image_to_data",
        lambda *args, **kwargs: {
            "text": ["Hello", "blurry", "Iris", ""],
            "conf": ["95", "12", "88", "-1"],
        },
    )

    # Explicit tesseract_cmd bypasses OCRReader.__init__'s existence check
    # entirely (see vision/ocr.py) -- no real binary needed for this test,
    # only the mocked image_to_data above.
    reader = OCRReader(tesseract_cmd="/fake/tesseract", min_confidence=60)
    result = reader.read(Image.new("RGB", (8, 8)))

    assert result == "Hello Iris"


def test_returns_empty_string_when_nothing_above_confidence(monkeypatch) -> None:
    monkeypatch.setattr(
        pytesseract,
        "image_to_data",
        lambda *args, **kwargs: {"text": ["blurry", "noise"], "conf": ["10", "5"]},
    )

    reader = OCRReader(tesseract_cmd="/fake/tesseract", min_confidence=60)
    result = reader.read(Image.new("RGB", (8, 8)))

    assert result == ""


def test_sets_tesseract_cmd_when_provided() -> None:
    reader = OCRReader(tesseract_cmd="/custom/path/tesseract")

    assert pytesseract.pytesseract.tesseract_cmd == "/custom/path/tesseract"
    assert reader is not None

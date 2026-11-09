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
            "left": [1, 2, 3, 4],
            "top": [5, 6, 7, 8],
            "width": [10, 20, 30, 40],
            "height": [11, 21, 31, 41],
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
        lambda *args, **kwargs: {
            "text": ["blurry", "noise"],
            "conf": ["10", "5"],
            "left": [1, 2],
            "top": [3, 4],
            "width": [10, 20],
            "height": [11, 21],
        },
    )

    reader = OCRReader(tesseract_cmd="/fake/tesseract", min_confidence=60)
    result = reader.read(Image.new("RGB", (8, 8)))

    assert result == ""


def test_sets_tesseract_cmd_when_provided() -> None:
    reader = OCRReader(tesseract_cmd="/custom/path/tesseract")

    assert pytesseract.pytesseract.tesseract_cmd == "/custom/path/tesseract"
    assert reader is not None


def test_read_with_boxes_returns_confident_words_with_pixel_boxes(monkeypatch) -> None:
    monkeypatch.setattr(
        pytesseract,
        "image_to_data",
        lambda *args, **kwargs: {
            "text": ["Export", "blurry", ""],
            "conf": ["95", "12", "-1"],
            "left": [100, 200, 300],
            "top": [50, 60, 70],
            "width": [80, 90, 95],
            "height": [20, 21, 22],
        },
    )

    reader = OCRReader(tesseract_cmd="/fake/tesseract", min_confidence=60)
    boxes = reader.read_with_boxes(Image.new("RGB", (8, 8)))

    assert len(boxes) == 1
    assert boxes[0].text == "Export"
    assert boxes[0].confidence == 95.0
    assert (boxes[0].left, boxes[0].top, boxes[0].width, boxes[0].height) == (100, 50, 80, 20)


def test_read_and_read_with_boxes_share_one_tesseract_pass(monkeypatch) -> None:
    calls: list[bool] = []

    def fake_image_to_data(*args, **kwargs):
        calls.append(True)
        return {
            "text": ["Export"],
            "conf": ["95"],
            "left": [100],
            "top": [50],
            "width": [80],
            "height": [20],
        }

    monkeypatch.setattr(pytesseract, "image_to_data", fake_image_to_data)

    reader = OCRReader(tesseract_cmd="/fake/tesseract", min_confidence=60)
    image = Image.new("RGB", (1920, 1080), color=(9, 9, 9))
    boxes = reader.read_with_boxes(image)
    text = reader.read(image)
    other = reader.read(Image.new("RGB", (1920, 1080), color=(10, 10, 10)))

    assert len(calls) == 2  # shared pass for the same image, fresh pass for new pixels
    assert [box.text for box in boxes] == ["Export"]
    assert text == "Export"
    assert other == "Export"  # same fake output; the point is the second Tesseract call

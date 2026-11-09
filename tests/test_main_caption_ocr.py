"""Tests for `main._caption_and_ocr` (scene caption + OCR over one screenshot).

Covers the combination semantics: both sides run independently, `None`
or a raised exception on one side yields `""` for that side only.
Wall-clock speedup from running both at once is verified by measurement,
not here.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PIL", reason="vision extra (Pillow) not installed")

# This import follows the importorskip guard -- the vision extra is
# optional and Sentry degrades without it.
from PIL import Image

from main import _caption_and_ocr


def _images() -> tuple[Image.Image, Image.Image]:
    vision_image = Image.new("RGB", (384, 216), color=(10, 20, 30))
    full_image = Image.new("RGB", (1920, 1080), color=(40, 50, 60))
    return vision_image, full_image


def _call(**overrides):
    vision_image, full_image = _images()
    vision_model = MagicMock()
    vision_model.describe.return_value = "a code editor"
    ocr_reader = MagicMock()
    ocr_reader.read.return_value = "File Edit View"
    kwargs = {
        "vision_model": vision_model,
        "ocr_reader": ocr_reader,
        "vision_image": vision_image,
        "image": full_image,
        "caption_prompt": "prompt",
        "max_tokens": 100,
        "repeat_penalty": 1.3,
    }
    kwargs.update(overrides)
    return _caption_and_ocr(**kwargs), vision_model, ocr_reader


def test_both_sides_run_and_combine() -> None:
    (caption, ocr_text), vision_model, ocr_reader = _call()
    assert (caption, ocr_text) == ("a code editor", "File Edit View")
    # Captioning gets the downscaled copy; OCR gets the full-resolution one.
    assert vision_model.describe.call_args.args[0].size == (384, 216)
    assert ocr_reader.read.call_args.args[0].size == (1920, 1080)


def test_missing_subsystem_yields_empty_string_for_that_side() -> None:
    assert _call(vision_model=None)[0] == ("", "File Edit View")
    assert _call(ocr_reader=None)[0] == ("a code editor", "")
    assert _call(vision_model=None, ocr_reader=None)[0] == ("", "")


def test_failing_side_yields_empty_string_for_that_side() -> None:
    vision_model = MagicMock()
    vision_model.describe.side_effect = RuntimeError("model blew up")
    assert _call(vision_model=vision_model)[0] == ("", "File Edit View")

    ocr_reader = MagicMock()
    ocr_reader.read.side_effect = RuntimeError("tesseract blew up")
    assert _call(ocr_reader=ocr_reader)[0] == ("a code editor", "")


def test_sides_run_concurrently() -> None:
    # describe() blocks until OCR has started: against a sequential
    # implementation this times out (OCR runs only after describe
    # returns), failing the test instead of hanging it.
    ocr_started = threading.Event()
    vision_image, full_image = _images()

    def describe(*args, **kwargs) -> str:
        assert ocr_started.wait(timeout=10)
        return "cap"

    def read(image) -> str:
        ocr_started.set()
        return "ocr"

    vision_model = MagicMock()
    vision_model.describe.side_effect = describe
    ocr_reader = MagicMock()
    ocr_reader.read.side_effect = read

    assert _caption_and_ocr(vision_model, ocr_reader, vision_image, full_image, "p", 100, 1.3) == (
        "cap",
        "ocr",
    )


def test_concurrent_side_failure_still_returns_the_other_side() -> None:
    release = threading.Event()
    vision_image, full_image = _images()
    failures: list[str] = []

    def describe(*args, **kwargs) -> str:
        # Must observe OCR running first ("runtime"); a sequential
        # implementation times out here instead ("timeout").
        if release.wait(timeout=10):
            failures.append("runtime")
        else:
            failures.append("timeout")
        raise RuntimeError("model blew up mid-flight")

    def read(image) -> str:
        release.set()
        return "ocr"

    vision_model = MagicMock()
    vision_model.describe.side_effect = describe
    ocr_reader = MagicMock()
    ocr_reader.read.side_effect = read

    assert _caption_and_ocr(vision_model, ocr_reader, vision_image, full_image, "p", 100, 1.3) == (
        "",
        "ocr",
    )
    assert failures == ["runtime"]

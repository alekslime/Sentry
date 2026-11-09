"""Tests for `main.py`'s `_resize_for_vision` (Milestone 11, Part A).

Real `PIL.Image` objects throughout, real `.resize()` calls -- no
mocking. `main.py` isn't a package (it's the entry point), so these
import it directly by relying on the project root being on `sys.path`,
same as `preview_island_manual_check.py` and other root-level scripts
already do.

Importing `main` needs only the core dependencies (PySide6, pydantic,
PyYAML) -- the optional voice/llm/vision/tts extras are try/except
ImportError-guarded in main.py itself for graceful degradation. This
file additionally needs real PIL images, so it skips where Pillow
(the vision extra) isn't installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PIL", reason="vision extra (Pillow) not installed")

# These imports follow the importorskip guard.
from PIL import Image

from main import _resize_for_vision


def _solid_image(width: int, height: int) -> Image.Image:
    return Image.new("RGB", (width, height), color=(120, 60, 200))


def test_none_max_dimension_returns_original_untouched():
    image = _solid_image(1920, 1080)
    result = _resize_for_vision(image, None)
    assert result is image
    assert result.size == (1920, 1080)


def test_image_already_within_bounds_is_untouched():
    image = _solid_image(800, 600)
    result = _resize_for_vision(image, 1280)
    assert result is image
    assert result.size == (800, 600)


def test_image_exactly_at_max_dimension_is_untouched():
    image = _solid_image(1280, 720)
    result = _resize_for_vision(image, 1280)
    assert result is image


def test_landscape_image_downscaled_preserves_aspect_ratio():
    image = _solid_image(1920, 1080)
    result = _resize_for_vision(image, 1280)
    assert result is not image
    assert result.size == (1280, 720)  # 1920x1080 is exactly 16:9


def test_portrait_image_downscaled_preserves_aspect_ratio():
    image = _solid_image(1080, 1920)
    result = _resize_for_vision(image, 1280)
    assert result.size == (720, 1280)


def test_non_16_9_aspect_ratio_rounds_sensibly():
    # 3440x1440 (ultrawide) -> longest side 3440 scaled to 1280
    image = _solid_image(3440, 1440)
    result = _resize_for_vision(image, 1280)
    width, height = result.size
    assert width == 1280
    # height should be close to 1440 * (1280/3440) = 535.8 -> 536
    assert height == 536


def test_result_never_exceeds_max_dimension_on_either_side():
    image = _solid_image(5120, 2880)  # 5K monitor
    result = _resize_for_vision(image, 1024)
    assert max(result.size) <= 1024


def test_tiny_max_dimension_never_produces_zero_pixel_dimension():
    # Guards the max(1, round(...)) floor in the implementation --
    # a pathological max_dimension shouldn't produce a 0-width/height
    # image that PIL/the vision model would choke on.
    image = _solid_image(1920, 1)
    result = _resize_for_vision(image, 1)
    width, height = result.size
    assert width >= 1 and height >= 1

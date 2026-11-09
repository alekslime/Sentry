"""Verbatim on-screen text extraction via Tesseract OCR (Milestone 5 addendum).

Complements `vision/model.py`'s scene description with actual, literal
text recognition -- moondream2 can tell you "a video editor is open with a
timeline", but it can't reliably transcribe the exact text in a menu,
error dialog, or code editor. Tesseract can. `main.py` calls both and
folds each into the prompt separately, since they answer different
questions ("what is this" vs "what does it literally say").

Requires the actual Tesseract OCR engine installed as a system binary --
`pytesseract` is only a thin Python wrapper around the `tesseract` CLI,
not a bundled OCR implementation. See `docs/DECISIONS.md` for install
links. If Tesseract isn't found, `OCRReader.__init__` raises the same way
`VisionModel`/`LLMEngine` do, so `main.py` can catch it and continue
without OCR rather than crashing the whole app.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from typing import NamedTuple

import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_MIN_CONFIDENCE = 60.0


class WordBox(NamedTuple):
    """One confident OCR word with its pixel box in the source image."""

    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int


class OCRReader:
    """Wraps `pytesseract` for extracting literal text from a screenshot.

    Stateless across calls, same shape as `VisionModel.describe()` --
    one image in, one text blob out.
    """

    def __init__(
        self,
        tesseract_cmd: str | None = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        self._min_confidence = min_confidence
        # Last OCR result, keyed by image content hash: `read()` and
        # `read_with_boxes()` share one Tesseract pass when called on the
        # same screenshot (as main.py's locate-then-caption flow does).
        self._cache_key: bytes | None = None
        self._cache_boxes: list[WordBox] = []

        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        elif not shutil.which(pytesseract.pytesseract.tesseract_cmd):
            # No explicit path given and the default "tesseract" isn't on
            # PATH either -- fail loudly and early (during __init__, same
            # as a bad HF repo_id in VisionModel/LLMEngine) rather than
            # letting every describe() call fail silently later.
            raise RuntimeError(
                "Tesseract OCR engine not found. pytesseract is only a "
                "wrapper -- install the actual Tesseract binary (e.g. "
                "https://github.com/UB-Mannheim/tesseract/wiki on "
                "Windows) and either add it to PATH or set "
                "vision.tesseract_cmd to its full exe path in config.yaml."
            )

        logger.info("OCR reader ready (tesseract_cmd=%s).", pytesseract.pytesseract.tesseract_cmd)

    def read_with_boxes(self, image: Image.Image) -> list[WordBox]:
        """Extract confident words with their pixel boxes in `image`.

        Uses `image_to_data` (not the simpler `image_to_string`) so each
        word's confidence score can be checked individually -- screenshots
        often have UI chrome/icons that OCR misreads with low confidence,
        and including those tokens verbatim would be worse than dropping
        them. Text grounding (`vision/text_locate.py`) matches queries
        against these boxes.
        """
        key = hashlib.blake2b(image.tobytes(), digest_size=16).digest()
        if key == self._cache_key:
            return list(self._cache_boxes)

        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        boxes: list[WordBox] = []
        for text, conf, left, top, width, height in zip(
            data["text"],
            data["conf"],
            data["left"],
            data["top"],
            data["width"],
            data["height"],
        ):
            stripped = text.strip()
            if not stripped:
                continue
            try:
                confidence = float(conf)
            except (TypeError, ValueError):
                continue
            if confidence < self._min_confidence:
                continue
            boxes.append(
                WordBox(
                    text=stripped,
                    confidence=confidence,
                    left=int(left),
                    top=int(top),
                    width=int(width),
                    height=int(height),
                )
            )

        self._cache_key = key
        self._cache_boxes = boxes
        logger.debug("OCR extracted %d confident words.", len(boxes))
        return list(boxes)

    def read(self, image: Image.Image) -> str:
        """Extract text from a screenshot, filtering out low-confidence words.

        Same output as before -- now just the joined text of
        `read_with_boxes()`, sharing its result cache.
        """
        boxes = self.read_with_boxes(image)
        result = " ".join(box.text for box in boxes)
        logger.debug("OCR extracted %d chars from %d confident words.", len(result), len(boxes))
        return result

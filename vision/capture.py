"""Screen capture via `mss`.

Knows nothing about vision models, the LLM, or Aura — it takes a screenshot
and hands back an in-memory image. `main.py` (from Milestone 5 onward)
decides when to call it and what to do with the result, same separation of
concerns as `voice/audio_stream.py` and `speech/transcriber.py`.

Privacy by default: `capture()` never writes to disk. The only way a
screenshot touches disk is the explicit `save_debug_screenshots` developer
setting, handled by `capture_and_maybe_save()` — off by default, and never
enabled implicitly. See `docs/DECISIONS.md`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import mss
from PIL import Image

logger = logging.getLogger(__name__)


class ScreenCapture:
    """Captures a single monitor (or the full virtual screen) as a Pillow image.

    Usage:
        capture = ScreenCapture(monitor_index=0)
        image = capture.capture()  # PIL.Image.Image, RGB, in memory only
    """

    def __init__(self, monitor_index: int = 0) -> None:
        self._monitor_index = monitor_index

    def capture(self) -> Image.Image:
        """Grab a screenshot and return it as an in-memory RGB `PIL.Image`.

        Opens and closes its own `mss` context per call rather than holding
        one open across calls — screen capture here is occasional
        (triggered per user query, not continuous, per Sentry's
        no-continuous-monitoring privacy principle), so the small per-call
        setup cost isn't worth the complexity of a long-lived handle.

        Raises `RuntimeError` if capture fails (e.g. no display server
        available, or an invalid monitor_index) — same pattern as
        `LLMEngine`'s constructor: the caller decides how to degrade.
        """
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if self._monitor_index >= len(monitors):
                    raise RuntimeError(
                        f"monitor_index={self._monitor_index} is out of range "
                        f"(mss sees {len(monitors)} monitor entries, including "
                        "index 0 for the combined virtual screen)."
                    )
                raw = sct.grab(monitors[self._monitor_index])
                image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception as exc:
            raise RuntimeError(f"Screen capture failed: {exc}") from exc

        logger.debug(
            "Captured screenshot: monitor_index=%d, size=%s",
            self._monitor_index,
            image.size,
        )
        return image

    def capture_and_maybe_save(
        self,
        save_debug_screenshots: bool,
        debug_screenshot_dir: Path,
    ) -> Image.Image:
        """Same as `capture()`, but also writes the image to disk when
        `save_debug_screenshots` is true.

        This is a developer aid only (see `config.schema.VisionSettings`) —
        production behavior discards the screenshot after use. The caller
        is responsible for resolving the default directory
        (`config/paths.py`'s `DATA_DIR / "debug_screenshots"`) since this
        module has no knowledge of app-wide path conventions.
        """
        image = self.capture()

        if save_debug_screenshots:
            debug_screenshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            out_path = debug_screenshot_dir / f"screenshot_{timestamp}.png"
            image.save(out_path)
            logger.info("Debug screenshot saved to %s", out_path)

        return image

    @staticmethod
    def list_monitors() -> list[dict]:
        """Return `mss`'s monitor descriptors, for a future settings UI to
        pick from. Index 0 is always the combined virtual screen.
        """
        with mss.mss() as sct:
            return list(sct.monitors)

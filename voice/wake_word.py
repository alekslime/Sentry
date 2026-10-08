"""Wake word detection, wrapping OpenWakeWord.

Knows nothing about audio *capture* (see `voice/audio_stream.py`) or what
happens after a wake word fires (that's `main.py`'s job, via a callback).
Its only responsibility: given audio frames, say whether the configured
wake word was just heard.

Model resolution supports two cases:
    - A bundled stock model, referenced by name (e.g. "hey_jarvis") — used
      as a placeholder during development. As of openWakeWord 0.6.0, stock
      models are NOT bundled in the pip package — they're downloaded once
      from GitHub release assets on first use and cached locally
      thereafter. This is a one-time setup download, not runtime cloud
      inference — it doesn't conflict with Sentry's offline-first principle,
      but it does mean the very first run needs an internet connection.
    - A custom-trained model, referenced by a full path to a `.onnx` file
      (e.g. a "Hey Sentry" model trained via https://openwakeword.com/train).
      This is a drop-in swap: point config at the new file, no code changes,
      no download needed.

We always use the "onnx" inference framework explicitly (rather than
OpenWakeWord's default "tflite") since it has no extra native-runtime
dependency and works identically across Windows/Linux/macOS.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import openwakeword
from openwakeword.model import Model
from openwakeword.utils import download_models

logger = logging.getLogger(__name__)

DEFAULT_DETECTION_THRESHOLD = 0.5

# How many consecutive frames above threshold before we consider it a real
# detection rather than a single noisy spike. At ~80ms/frame this is ~160ms.
DEFAULT_CONSECUTIVE_FRAMES = 2

# A single spoken wake word typically stays above threshold for many
# consecutive frames (an utterance is roughly 0.5-1s, i.e. ~6-12 frames at
# 80ms each) — far more than `consecutive_frames_required`. Without a
# cooldown, one utterance fires the callback repeatedly as the streak keeps
# re-crossing the threshold. This cooldown suppresses further detections
# for the same model for a short period after firing, so one "Hey Jarvis"
# reliably produces exactly one detection.
DEFAULT_COOLDOWN_SECONDS = 1.5

INFERENCE_FRAMEWORK = "onnx"

WakeWordCallback = Callable[[str, float], None]


def resolve_model(model_name_or_path: str) -> str:
    """Resolve a wake word model reference to something `Model()` accepts.

    If `model_name_or_path` is an existing file path, it's treated as a
    custom-trained model and returned as-is. Otherwise, it's treated as a
    stock model name (e.g. "hey_jarvis") — bundled models are downloaded
    (once, cached thereafter) via OpenWakeWord's `download_models()`, and
    the name itself is returned for `Model(wakeword_models=[...])` to
    resolve internally.
    """
    custom_path = Path(model_name_or_path)
    if custom_path.exists():
        logger.debug("Using custom wake word model at %s", custom_path)
        return str(custom_path)

    stock_names = list(openwakeword.MODELS.keys())
    if model_name_or_path not in stock_names:
        raise ValueError(
            f"Wake word model '{model_name_or_path}' is not a known bundled model "
            f"(available: {stock_names}) and no file exists at that path."
        )

    logger.info(
        "Ensuring wake word model '%s' is downloaded (one-time setup, cached after first run)...",
        model_name_or_path,
    )
    try:
        download_models([model_name_or_path])
    except Exception as exc:
        raise RuntimeError(
            f"Could not download wake word model '{model_name_or_path}'. "
            "This requires an internet connection the first time it's used. "
            f"Underlying error: {exc}"
        ) from exc

    return model_name_or_path


class WakeWordDetector:
    """Feeds audio frames to OpenWakeWord and reports detections.

    Usage:
        def on_detected(model_name: str, score: float) -> None:
            ...

        detector = WakeWordDetector(model_name_or_path="hey_jarvis", on_detected=on_detected)
        # then, per audio frame from MicrophoneStream:
        detector.process_frame(frame)
    """

    def __init__(
        self,
        model_name_or_path: str,
        on_detected: WakeWordCallback,
        threshold: float = DEFAULT_DETECTION_THRESHOLD,
        consecutive_frames_required: int = DEFAULT_CONSECUTIVE_FRAMES,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._on_detected = on_detected
        self._threshold = threshold
        self._consecutive_required = consecutive_frames_required
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_counts: dict[str, int] = {}
        self._last_detection_time: dict[str, float] = {}

        resolved = resolve_model(model_name_or_path)
        self._model = Model(wakeword_models=[resolved], inference_framework=INFERENCE_FRAMEWORK)
        self._model_names = list(self._model.models.keys())

        logger.info(
            "WakeWordDetector ready (models=%s, threshold=%.2f)",
            self._model_names,
            self._threshold,
        )

    def process_frame(self, frame: np.ndarray) -> None:
        """Feed one audio frame (int16, shape (N,)) through the model.

        Calls `on_detected(model_name, score)` if the wake word is heard for
        `consecutive_frames_required` frames in a row, then enters a
        cooldown period (`cooldown_seconds`) during which further
        detections for that model are suppressed — this is what makes one
        spoken utterance produce exactly one callback instead of several.
        """
        predictions = self._model.predict(frame)
        now = time.monotonic()

        for model_name, score in predictions.items():
            last_detected = self._last_detection_time.get(model_name, 0.0)
            in_cooldown = (now - last_detected) < self._cooldown_seconds

            if score >= self._threshold and not in_cooldown:
                self._consecutive_counts[model_name] = self._consecutive_counts.get(model_name, 0) + 1
                if self._consecutive_counts[model_name] >= self._consecutive_required:
                    logger.info("Wake word detected: %s (score=%.3f)", model_name, score)
                    self._on_detected(model_name, float(score))
                    self._consecutive_counts[model_name] = 0
                    self._last_detection_time[model_name] = now
            else:
                self._consecutive_counts[model_name] = 0

    def reset(self) -> None:
        """Clear internal detection streaks and cooldowns, e.g. after handling a detection."""
        self._consecutive_counts.clear()
        self._last_detection_time.clear()

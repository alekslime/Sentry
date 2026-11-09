"""Microphone audio capture.

Provides a small, focused wrapper around `sounddevice` that streams
16kHz mono int16 audio frames — the format OpenWakeWord (and later,
Faster-Whisper) expects. Deliberately has zero knowledge of wake words or
speech-to-text; it just produces frames. Consumers (e.g. `voice/wake_word.py`)
subscribe via a callback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

# OpenWakeWord expects 16kHz mono audio, delivered in chunks — 80ms (1280
# samples) is OpenWakeWord's documented recommended frame size.
SAMPLE_RATE = 16_000
CHANNELS = 1
FRAME_SIZE = 1280  # samples per callback (~80ms at 16kHz)

AudioFrameCallback = Callable[[np.ndarray], None]


class MicrophoneStream:
    """Streams microphone audio in fixed-size int16 frames via a callback.

    Usage:
        def on_frame(frame: np.ndarray) -> None:
            ...  # frame is shape (FRAME_SIZE,), dtype int16

        mic = MicrophoneStream(on_frame)
        mic.start()
        ...
        mic.stop()
    """

    def __init__(
        self,
        on_frame: AudioFrameCallback,
        device: int | str | None = None,
        sample_rate: int = SAMPLE_RATE,
        frame_size: int = FRAME_SIZE,
    ) -> None:
        self._on_frame = on_frame
        self._device = device
        self._sample_rate = sample_rate
        self._frame_size = frame_size
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        """Open the input stream and begin delivering frames to the callback."""
        if self._stream is not None:
            logger.warning("MicrophoneStream.start() called while already running.")
            return

        logger.info(
            "Starting microphone stream (device=%s, sample_rate=%d, frame_size=%d)",
            self._device if self._device is not None else "default",
            self._sample_rate,
            self._frame_size,
        )

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=CHANNELS,
            dtype="int16",
            blocksize=self._frame_size,
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(
        self, indata: np.ndarray, frames: int, time_info: object, status: sd.CallbackFlags
    ) -> None:
        if status:
            logger.warning("Audio input status flag set: %s", status)
        # indata shape is (frames, channels); flatten to 1D mono.
        self._on_frame(indata[:, 0].copy())

    def stop(self) -> None:
        """Stop and close the input stream. Safe to call even if not started."""
        if self._stream is None:
            return
        self._stream.stop()
        self._stream.close()
        self._stream = None
        logger.info("Microphone stream stopped.")

    @staticmethod
    def list_devices() -> list[dict]:
        """Return available input devices, for a future settings UI to pick from."""
        devices = sd.query_devices()
        return [d for d in devices if d.get("max_input_channels", 0) > 0]

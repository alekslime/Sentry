"""Buffers audio frames for one spoken utterance, using simple RMS-based
silence detection to know when the user is done talking.

Knows nothing about wake words, transcription, or Qt — just accumulates
frames and reports when it thinks the utterance is complete. `main.py`
creates one of these right after a wake word fires, feeds it frames
(reusing `MicrophoneStream`'s callback), and once `finished` it hands
`get_audio()` to `speech/transcriber.py`.

Three ways a session can finish:
    1. The user spoke, then went quiet for `end_silence_seconds` — the
       normal case.
    2. The user never spoke at all within `initial_timeout_seconds` (e.g.
       an accidental wake word trigger) — gives up rather than listening
       forever.
    3. `max_duration_seconds` was reached regardless — a safety cap.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SILENCE_RMS_THRESHOLD = 300.0
DEFAULT_END_SILENCE_SECONDS = 1.0
DEFAULT_INITIAL_TIMEOUT_SECONDS = 3.0
DEFAULT_MAX_DURATION_SECONDS = 10.0

# Must match voice/audio_stream.py's SAMPLE_RATE / FRAME_SIZE — audio
# always flows through MicrophoneStream first, so these are the same
# constants, duplicated here to keep this module independently readable
# and testable without importing voice/.
SAMPLE_RATE = 16_000
FRAME_SIZE = 1280


class ListeningSession:
    """Accumulates audio frames for one utterance until silence, timeout,
    or the max duration is reached.
    """

    def __init__(
        self,
        silence_rms_threshold: float = DEFAULT_SILENCE_RMS_THRESHOLD,
        end_silence_seconds: float = DEFAULT_END_SILENCE_SECONDS,
        initial_timeout_seconds: float = DEFAULT_INITIAL_TIMEOUT_SECONDS,
        max_duration_seconds: float = DEFAULT_MAX_DURATION_SECONDS,
        sample_rate: int = SAMPLE_RATE,
        frame_size: int = FRAME_SIZE,
    ) -> None:
        self._silence_rms_threshold = silence_rms_threshold
        self._end_silence_frames = max(1, round(end_silence_seconds * sample_rate / frame_size))
        self._initial_timeout_frames = max(
            1, round(initial_timeout_seconds * sample_rate / frame_size)
        )
        self._max_frames = max(1, round(max_duration_seconds * sample_rate / frame_size))

        self._buffer: list[np.ndarray] = []
        self._silence_streak = 0
        self._heard_speech = False
        self._finished = False

    @staticmethod
    def _rms(frame: np.ndarray) -> float:
        return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))

    def add_frame(self, frame: np.ndarray) -> bool:
        """Feed one audio frame in.

        Returns True once the session has finished — see module docstring
        for the three ways that can happen. Safe to keep calling after
        finishing; it's a no-op (returns True immediately).
        """
        if self._finished:
            return True

        self._buffer.append(frame)
        is_silent = self._rms(frame) < self._silence_rms_threshold

        if not is_silent:
            self._heard_speech = True
            self._silence_streak = 0
        else:
            self._silence_streak += 1

        if self._heard_speech and self._silence_streak >= self._end_silence_frames:
            logger.debug("Listening session finished: silence after speech.")
            self._finished = True
        elif not self._heard_speech and len(self._buffer) >= self._initial_timeout_frames:
            logger.debug("Listening session finished: no speech detected within timeout.")
            self._finished = True
        elif len(self._buffer) >= self._max_frames:
            logger.debug("Listening session finished: max duration reached.")
            self._finished = True

        return self._finished

    def get_audio(self) -> np.ndarray:
        """Return all buffered audio as one concatenated int16 array."""
        if not self._buffer:
            return np.array([], dtype=np.int16)
        return np.concatenate(self._buffer)

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def heard_speech(self) -> bool:
        """Whether any frame exceeded the silence threshold at all — lets
        callers distinguish "user said something" from "gave up, nobody
        spoke" without transcribing empty/noise-only audio.
        """
        return self._heard_speech

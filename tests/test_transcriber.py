"""Tests for `speech/transcriber.py`, focused on the CUDA-runtime-failure
detection and CPU auto-fallback added 2026-07-11 (see docs/DECISIONS.md).

Doesn't touch a real `WhisperModel` or download anything — `Transcriber`
is constructed with `__new__` and its private attributes set directly, so
these tests exercise the fallback *logic* in isolation from model loading.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("numpy", reason="speech extra (via faster-whisper) not installed")
pytest.importorskip("faster_whisper", reason="speech extra (faster-whisper) not installed")

# These imports follow the importorskip guards -- the speech extra is
# optional and Sentry degrades without it.
import numpy as np

from speech.transcriber import (
    Transcriber,
    _looks_like_cuda_runtime_failure,
    int16_to_float32,
)


def _make_transcriber() -> Transcriber:
    """Build a `Transcriber` without running `__init__` (so no real
    `WhisperModel` load/download), then fill in the attributes it needs.
    """
    t = Transcriber.__new__(Transcriber)
    t._language = "en"
    t._model_size = "small"
    t._compute_type = "default"
    t._fell_back_to_cpu = False
    return t


def _fake_segments(text: str):
    segment = MagicMock()
    segment.text = text
    info = MagicMock(language="en", language_probability=0.99)
    return [segment], info


def test_looks_like_cuda_runtime_failure_matches_known_error() -> None:
    exc = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
    assert _looks_like_cuda_runtime_failure(exc) is True


def test_looks_like_cuda_runtime_failure_ignores_unrelated_errors() -> None:
    exc = ValueError("audio array must be mono")
    assert _looks_like_cuda_runtime_failure(exc) is False


def test_transcribe_falls_back_to_cpu_after_cuda_failure() -> None:
    t = _make_transcriber()

    broken_gpu_model = MagicMock()
    broken_gpu_model.transcribe.side_effect = RuntimeError(
        "Library cublas64_12.dll is not found or cannot be loaded"
    )
    t._model = broken_gpu_model

    cpu_model = MagicMock()
    cpu_model.transcribe.return_value = _fake_segments("hello there")

    audio = np.zeros(16_000, dtype=np.int16)

    with patch("speech.transcriber.WhisperModel", return_value=cpu_model) as ctor:
        result = t.transcribe(audio)

    assert result == "hello there"
    assert t._fell_back_to_cpu is True
    ctor.assert_called_once_with("small", device="cpu", compute_type="int8")
    # Second call should go straight to the (now CPU) model, no more
    # reload attempts or exceptions to catch.
    cpu_model.transcribe.return_value = _fake_segments("again")
    result2 = t.transcribe(audio)
    assert result2 == "again"


def test_transcribe_reraises_non_cuda_errors_without_falling_back() -> None:
    t = _make_transcriber()

    model = MagicMock()
    model.transcribe.side_effect = ValueError("some unrelated failure")
    t._model = model

    audio = np.zeros(16_000, dtype=np.int16)

    with patch("speech.transcriber.WhisperModel") as ctor:
        try:
            t.transcribe(audio)
            raised = False
        except ValueError:
            raised = True

    assert raised is True
    assert t._fell_back_to_cpu is False
    ctor.assert_not_called()


def test_transcribe_empty_audio_returns_empty_string_without_touching_model() -> None:
    t = _make_transcriber()
    t._model = MagicMock()

    result = t.transcribe(np.array([], dtype=np.int16))

    assert result == ""
    t._model.transcribe.assert_not_called()


def test_int16_to_float32_normalizes_range() -> None:
    audio = np.array([-32768, 0, 32767], dtype=np.int16)
    result = int16_to_float32(audio)
    assert result.dtype == np.float32
    assert result.min() >= -1.0
    assert result.max() <= 1.0

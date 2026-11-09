"""Per-turn latency instrumentation (Milestone 11, Part A).

`main.py`'s pipeline for a single voice turn -- wake word -> STT ->
(optional) vision -> LLM -> TTS -- spans several worker threads and two
Qt-signal hops (WakeWordBridge, TranscriptBridge, LLMResponseBridge,
TTSBridge), so no single function owns start-to-finish timing. `TurnTimer`
is a small, dependency-free stopwatch built to be passed through that
whole chain: one instance is created when a wake word fires and threaded
through as an explicit argument (worker-thread stages) or via the
closure-scoped `current_turn` holder in `main.py` (main-thread callback
stages), the same "single turn at a time" assumption `main.py` already
relies on elsewhere (e.g. `TTSSettings.interrupt_on_new_query` stopping
previous playback when a new query starts).

Two ways to mark a stage, matching the two shapes stages come in:

  1. A stage that starts and ends within one function/thread -- use it as
     a context manager: `with timer.stage("llm"): ...`.
  2. A stage that starts in one callback and ends in a later, different
     callback (e.g. "stt" starts in `on_wake_word_detected`, ends in
     `on_transcribed`) -- call `start_stage()`/`end_stage()` explicitly.

Deliberately stdlib-only (`time.perf_counter()`, `threading.Lock`) --
this needs to work in every configuration (LLM/vision/TTS all
optional/uninstalled), not just when the heavier optional extras are
present.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

# Stage order is display order, not enforced order -- `summary()` prints
# whichever stages actually got recorded for this turn, in this sequence,
# since a turn that skipped vision (the common case -- see
# `VisionSettings.trigger_keywords`) or has no TTS configured simply won't
# have those keys, and forcing them all to appear would misleadingly imply
# every turn always runs every stage.
_STAGE_ORDER = ("stt", "vision", "llm", "tts")


def _format_duration(seconds: float) -> str:
    """`820ms` below one second, `1.42s` at or above -- keeps the common
    case (sub-second stages) readable without a decimal point, while
    longer stages (a slow LLM generation) still get useful precision.
    """
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.2f}s"


class TurnTimer:
    """Tracks stage latencies for one wake-word-to-response turn.

    Not reused across turns -- `main.py` creates a fresh instance per
    wake word detection (see module docstring). `summary()` is safe to
    call at any point, including mid-turn (e.g. from an error handler
    that wants to log what got measured before the failure) or more than
    once (e.g. once when TTS finishes, again if that turn is later
    inspected) -- it never raises, just skips stages that never
    completed and reports elapsed-so-far as `total`.
    """

    def __init__(self) -> None:
        self._turn_start = time.perf_counter()
        self._stage_starts: dict[str, float] = {}
        self._durations: dict[str, float] = {}
        self._lock = threading.Lock()

    def start_stage(self, name: str) -> None:
        """Mark `name`'s start. Overwrites any unfinished start under the
        same name -- callers are expected to pair this with exactly one
        `end_stage(name)`, but a stray double-start (e.g. a retry path)
        shouldn't raise; it just resets that stage's clock.
        """
        self._stage_starts[name] = time.perf_counter()

    def end_stage(self, name: str) -> None:
        """Record `name`'s duration since its `start_stage()`. A no-op if
        `name` was never started (e.g. `end_stage("tts")` when no TTS
        engine is configured this session and the caller didn't guard
        for that) -- silently skipped rather than raising, since a
        missing optional stage is normal, not a bug.
        """
        start = self._stage_starts.pop(name, None)
        if start is None:
            return
        elapsed = time.perf_counter() - start
        with self._lock:
            self._durations[name] = elapsed

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Context-manager form for stages that start and end in the same
        function. Ends the stage even if the wrapped block raises, so a
        failed LLM call still reports how long it took before failing.
        """
        self.start_stage(name)
        try:
            yield
        finally:
            self.end_stage(name)

    def summary(self) -> str:
        """A one-line, log-friendly summary, e.g.:

            stt=340ms llm=890ms tts=210ms total=1.44s

        Stages that never ran (skipped vision, no TTS configured, or a
        turn that failed before reaching a later stage) are simply
        omitted rather than shown as 0ms -- an absent `vision=` means
        "not part of this turn," not "instant."
        """
        with self._lock:
            durations = dict(self._durations)
        parts = [
            f"{name}={_format_duration(durations[name])}"
            for name in _STAGE_ORDER
            if name in durations
        ]
        # Any stage recorded under a name outside the known display order
        # (shouldn't happen given main.py's fixed pipeline, but this
        # module has no way to enforce what callers pass to start_stage())
        # still gets reported rather than silently dropped.
        parts.extend(
            f"{name}={_format_duration(durations[name])}"
            for name in durations
            if name not in _STAGE_ORDER
        )
        total = time.perf_counter() - self._turn_start
        parts.append(f"total={_format_duration(total)}")
        return " ".join(parts)

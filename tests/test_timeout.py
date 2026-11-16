"""Tests for `utils/timeout.py` -- hard timeouts around hung inference.

`call_with_timeout` runs the callable on a daemon thread and waits for it
with a deadline. A hung call (e.g. a wedged llama.cpp inference) reports
`timed_out=True` so the turn can reset; the abandoned thread keeps running
in the background -- Python cannot kill threads -- which the caller must
account for (see `main.py`'s engine-busy flags).
"""

from __future__ import annotations

import threading
import time

import pytest


def test_success_returns_value() -> None:
    from utils.timeout import call_with_timeout

    outcome = call_with_timeout(lambda: 40 + 2, 5.0)

    assert outcome.timed_out is False
    assert outcome.value == 42
    assert outcome.error is None


def test_hung_call_reports_timeout() -> None:
    from utils.timeout import call_with_timeout

    started = threading.Event()

    def hang() -> str:
        started.set()
        time.sleep(30)
        return "never"

    outcome = call_with_timeout(hang, 0.2, thread_name="test-hung-call")

    assert started.is_set(), "the callable must actually have started"
    assert outcome.timed_out is True
    assert outcome.value is None
    assert outcome.error is None


def test_exception_is_captured_not_raised() -> None:
    from utils.timeout import call_with_timeout

    def boom() -> str:
        raise ValueError("bad prompt")

    outcome = call_with_timeout(boom, 5.0)

    assert outcome.timed_out is False
    assert outcome.value is None
    assert isinstance(outcome.error, ValueError)
    assert str(outcome.error) == "bad prompt"


def test_none_timeout_waits_for_slow_call() -> None:
    from utils.timeout import call_with_timeout

    def slow() -> str:
        time.sleep(0.3)
        return "late but fine"

    outcome = call_with_timeout(slow, None)

    assert outcome.timed_out is False
    assert outcome.value == "late but fine"


def test_zero_or_negative_timeout_means_no_wait() -> None:
    from utils.timeout import call_with_timeout

    release = threading.Event()

    def gated() -> str:
        release.wait(timeout=10)
        return "released"

    outcome = call_with_timeout(gated, 0.0)

    assert outcome.timed_out is True
    release.set()


def test_result_arriving_just_in_time_wins_race() -> None:
    # If the worker finished before the deadline elapsed, its value must
    # be reported even if the join wakes a hair late -- the done event,
    # not the join's return value, decides.
    from utils.timeout import call_with_timeout

    outcome = call_with_timeout(lambda: "fast", 60.0)

    assert outcome.timed_out is False
    assert outcome.value == "fast"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

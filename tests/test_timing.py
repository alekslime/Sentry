"""Tests for `utils/timing.py`'s `TurnTimer` (Milestone 11, Part A).

Real `time.perf_counter()` calls throughout -- no mocking of time itself,
since these are cheap (millisecond-scale sleeps) and mocking `perf_counter`
would mean testing against a fake clock instead of the real behavior this
module exists to measure. `time.sleep()` durations are kept short but
non-zero so timings are genuinely > 0 and ordering assertions are
meaningful, not just "didn't crash."
"""

from __future__ import annotations

import re
import time

from utils.timing import TurnTimer, _format_duration


def test_format_duration_sub_second_uses_milliseconds():
    assert _format_duration(0.34) == "340ms"


def test_format_duration_at_or_above_one_second_uses_seconds():
    assert _format_duration(1.0) == "1.00s"
    assert _format_duration(1.4234) == "1.42s"


def test_stage_context_manager_records_duration():
    timer = TurnTimer()
    with timer.stage("llm"):
        time.sleep(0.01)
    summary = timer.summary()
    assert "llm=" in summary
    assert "total=" in summary


def test_stage_context_manager_records_duration_even_on_exception():
    timer = TurnTimer()
    try:
        with timer.stage("llm"):
            time.sleep(0.01)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert "llm=" in timer.summary()


def test_start_end_stage_across_separate_calls():
    # Mirrors main.py's cross-callback usage: "stt" starts in
    # on_wake_word_detected and ends in on_transcribed, two separate
    # function calls (possibly on different threads in the real app).
    timer = TurnTimer()
    timer.start_stage("stt")
    time.sleep(0.01)
    timer.end_stage("stt")
    assert "stt=" in timer.summary()


def test_end_stage_without_start_is_a_silent_no_op():
    timer = TurnTimer()
    timer.end_stage("tts")  # never started -- should not raise
    summary = timer.summary()
    assert "tts=" not in summary
    assert "total=" in summary


def test_summary_omits_stages_that_never_ran():
    timer = TurnTimer()
    with timer.stage("stt"):
        pass
    summary = timer.summary()
    assert "stt=" in summary
    assert "llm=" not in summary
    assert "vision=" not in summary
    assert "tts=" not in summary


def test_summary_orders_known_stages_stt_vision_llm_tts():
    timer = TurnTimer()
    # Deliberately recorded out of order to prove summary() re-orders by
    # the fixed display order, not insertion order.
    with timer.stage("tts"):
        pass
    with timer.stage("stt"):
        pass
    with timer.stage("llm"):
        pass
    with timer.stage("vision"):
        pass
    summary = timer.summary()
    order = [m.group(1) for m in re.finditer(r"(\w+)=", summary)]
    assert order == ["stt", "vision", "llm", "tts", "total"]


def test_summary_includes_unknown_stage_names_too():
    timer = TurnTimer()
    with timer.stage("some_future_stage"):
        pass
    assert "some_future_stage=" in timer.summary()


def test_summary_is_callable_multiple_times_without_error():
    timer = TurnTimer()
    with timer.stage("llm"):
        time.sleep(0.01)
    first = timer.summary()
    second = timer.summary()
    # total keeps growing between calls (real elapsed time), but the
    # llm stage's recorded duration shouldn't change once ended.
    assert re.search(r"llm=(\d+)ms", first).group(1) == re.search(r"llm=(\d+)ms", second).group(1)


def test_double_start_stage_resets_the_clock():
    timer = TurnTimer()
    timer.start_stage("stt")
    time.sleep(0.02)
    timer.start_stage("stt")  # stray re-start, e.g. a retry path
    time.sleep(0.01)
    timer.end_stage("stt")
    match = re.search(r"stt=(\d+)ms", timer.summary())
    assert match is not None
    # Should reflect the second start (~10ms), not the full ~30ms window.
    assert int(match.group(1)) < 25


def test_new_turn_timer_starts_a_fresh_clock():
    timer_a = TurnTimer()
    time.sleep(0.02)
    timer_b = TurnTimer()
    total_a = float(re.search(r"total=(\d+)ms", timer_a.summary()).group(1))
    total_b = float(re.search(r"total=(\d+)ms", timer_b.summary()).group(1))
    assert total_a > total_b

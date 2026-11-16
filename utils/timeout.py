"""Hard timeouts around blocking inference calls.

`call_with_timeout` runs `func` on a daemon thread and waits up to
`timeout_s` seconds for it. On expiry it reports `timed_out=True` and
returns, leaving the abandoned thread running: Python has no way to kill
a thread, so a truly hung llama.cpp call keeps burning CPU until the
process exits. Callers MUST therefore treat the underlying engine as
still-busy until the call actually returns (see `main.py`'s engine-busy
flags) -- otherwise a new turn could enter the same non-thread-safe
llama context concurrently and crash (a real GGML assert, seen before).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CallOutcome[T]:
    """What `call_with_timeout` observed. Exactly one of the three
    states holds: `timed_out`, `error is not None`, or a `value`."""

    timed_out: bool
    value: T | None = None
    error: BaseException | None = None


def call_with_timeout[T](
    func: Callable[[], T],
    timeout_s: float | None,
    *,
    thread_name: str = "timeout-worker",
) -> CallOutcome[T]:
    """Run `func` and wait up to `timeout_s` seconds for it.

    `timeout_s=None` waits forever (same as calling `func` directly,
    except exceptions are captured instead of raised); `timeout_s <= 0`
    never waits (reports whatever already finished, which for a fresh
    call is always a timeout). The worker is a daemon thread, so an
    abandoned hung call never blocks process exit -- but also never
    stops burning CPU until it does (see module docstring).
    """
    done = threading.Event()
    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["value"] = func()
        except BaseException as exc:  # noqa: BLE001 -- captured for the caller, not swallowed
            box["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=_worker, name=thread_name, daemon=True)
    worker.start()
    if timeout_s is None:
        done.wait()
        finished = True
    else:
        finished = done.wait(max(0.0, timeout_s))
    if not finished:
        return CallOutcome(timed_out=True)
    if "error" in box:
        error = box["error"]
        assert isinstance(error, BaseException)
        return CallOutcome(timed_out=False, error=error)
    return CallOutcome(timed_out=False, value=box.get("value"))

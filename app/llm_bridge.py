"""Bridges LLM generation results from the background inference thread to
Qt's main/GUI thread. Same pattern as `app/transcript_bridge.py` and
`app/wake_word_bridge.py` — see those files' docstrings for why this
matters.

Generation runs on its own worker thread (not the Qt main thread) because
it can take anywhere from under a second to many seconds depending on
hardware and model size, and the GUI must not block on it.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class LLMResponseBridge(QObject):
    """Emits on the main thread once generation has finished (successfully
    or not), regardless of which thread called `report_response` /
    `report_failure`.
    """

    response_ready = Signal(str)  # generated reply text
    generation_failed = Signal(str)  # human-readable error message
    generation_timed_out = Signal(str)  # timeout detail (stage + seconds)

    def report_response(self, text: str) -> None:
        """Callback intended to be passed to the LLM generation pipeline.

        Safe to call from any thread.
        """
        self.response_ready.emit(text)

    def report_failure(self, message: str) -> None:
        """Safe to call from any thread."""
        self.generation_failed.emit(message)

    def report_timeout(self, detail: str) -> None:
        """Report a hung vision/LLM call (see utils/timeout.py). Unlike
        `report_failure`, the main-thread slot speaks a short apology, so
        the user hears what happened. Safe to call from any thread."""
        self.generation_timed_out.emit(detail)

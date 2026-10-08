"""Minimal placeholder application window.

Per the project's UX philosophy, Sentry should ultimately feel like part of
the OS rather than a traditional app window — this window exists only to
prove the application launches during early milestones, and (when
`debug.enabled` is on) to provide a text-based way to exercise the
wake-word → listen → transcribe pipeline without needing to speak.

That debug input is a developer aid only, not part of Sentry's intended UX.
It's expected to be hidden (via `debug.enabled = false` in config) or
removed once the real Aura + system-tray interaction model exists.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Placeholder window shown during early development milestones."""

    # Emitted when the user submits text via the debug input, carrying the
    # text as if it had been spoken and transcribed. main.py connects this
    # to the same handling path real voice input goes through.
    debug_text_submitted = Signal(str)

    def __init__(self, app_name: str, app_version: str, debug_enabled: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(f"{app_name} v{app_version}")
        self.resize(480, 360 if not debug_enabled else 440)

        central = QWidget(self)
        layout = QVBoxLayout(central)

        status_label = QLabel(
            f"{app_name} v{app_version} is running.\n\n"
            "This placeholder window will be replaced by the Aura overlay\n"
            "and tray-based interaction model in a later milestone.",
            self,
        )
        layout.addWidget(status_label)

        layout.addWidget(QLabel("Last response:", self))
        self._response_view = QTextEdit(self)
        self._response_view.setReadOnly(True)
        self._response_view.setPlaceholderText(
            "(Nothing yet — say the wake word and a command, or use the debug "
            "input below, once an LLM is configured.)"
        )
        self._response_view.setFixedHeight(100)
        layout.addWidget(self._response_view)

        if debug_enabled:
            layout.addWidget(self._build_debug_panel())

        self.setCentralWidget(central)
        logger.debug("MainWindow constructed (debug_enabled=%s).", debug_enabled)

    def show_response(self, text: str) -> None:
        """Display the latest LLM response.

        Text-only for now (Milestone 4) — voice output lands in Milestone 8,
        at which point this becomes a supplementary transcript rather than
        the only way to see a response.
        """
        self._response_view.setPlainText(text)

    def _build_debug_panel(self) -> QWidget:
        """Text input that simulates a full voice command turn (wake word
        + transcription) without needing a microphone. Debug-only — see
        module docstring.
        """
        panel = QWidget(self)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 16, 0, 0)

        panel_layout.addWidget(
            QLabel(
                "🔧 Debug: type a command instead of speaking it "
                "(simulates wake word + transcription):",
                self,
            )
        )

        input_row = QWidget(self)
        input_row_layout = QHBoxLayout(input_row)
        input_row_layout.setContentsMargins(0, 0, 0, 0)

        self._debug_input = QLineEdit(self)
        self._debug_input.setPlaceholderText('e.g. "what does this error mean?"')
        self._debug_input.returnPressed.connect(self._submit_debug_text)
        input_row_layout.addWidget(self._debug_input)

        send_button = QPushButton("Send", self)
        send_button.clicked.connect(self._submit_debug_text)
        input_row_layout.addWidget(send_button)

        panel_layout.addWidget(input_row)
        return panel

    def _submit_debug_text(self) -> None:
        text = self._debug_input.text().strip()
        if not text:
            return
        logger.info('Debug text input submitted: "%s"', text)
        self.debug_text_submitted.emit(text)
        self._debug_input.clear()

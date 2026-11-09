"""Tests for `preview_island_manual_check.wire_preview_shortcuts`.

The wiring lives in a helper that
takes a plain QWidget, so this regression test only needs an offscreen
QApplication -- no island, no display.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication, QWidget

from preview_island_manual_check import wire_preview_shortcuts


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch) -> QApplication:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def test_space_and_escape_shortcuts_fire_their_callbacks(app: QApplication) -> None:
    widget = QWidget()
    calls: list[str] = []

    def on_toggle() -> None:
        calls.append("toggle")

    def on_quit() -> None:
        calls.append("quit")

    wire_preview_shortcuts(widget, on_toggle, on_quit)

    by_key = {shortcut.key().toString(): shortcut for shortcut in widget.findChildren(QShortcut)}
    assert set(by_key) == {"Space", "Esc"}
    # Parented to the widget, so they stay alive as long as it does (an
    # unparented QShortcut with no other reference would be garbage
    # collected and silently stop working).
    assert all(shortcut.parent() is widget for shortcut in by_key.values())

    by_key["Space"].activated.emit()
    assert calls == ["toggle"]
    by_key["Esc"].activated.emit()
    assert calls == ["toggle", "quit"]

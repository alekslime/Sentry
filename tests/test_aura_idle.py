"""Idle-cost regression tests for the Aura glow renderer.

The overlay once repainted a fullscreen image at 30fps forever (~2.5 idle
cores). It now renders each state's glow once into a cached pixmap and only
repaints while the cross-fade between states runs. These tests pin that:
no tint work at idle, repaints only while a transition is in flight.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import aura.renderer.glow_renderer as glow
from aura.renderer.glow_renderer import GlowAuraRenderer
from aura.states import AuraState


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch) -> QApplication:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def _counting_tints(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Count module-level `_tint_gradient` calls from here on."""
    calls: list[bool] = []
    real_tint = glow._tint_gradient

    def counting(*args, **kwargs):
        calls.append(True)
        return real_tint(*args, **kwargs)

    monkeypatch.setattr(glow, "_tint_gradient", counting)
    return calls


def _overlay_widget(renderer: GlowAuraRenderer):
    widget = renderer._widget
    assert widget is not None
    return widget


def test_idle_does_no_tint_work(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = GlowAuraRenderer()
    renderer.initialize()
    renderer.show()
    QTest.qWait(300)  # initial paint settles

    calls = _counting_tints(monkeypatch)
    QTest.qWait(1200)

    assert calls == []
    assert [t for t in _overlay_widget(renderer).findChildren(QTimer)] == []


def test_transition_repaints_then_stops(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = GlowAuraRenderer()
    renderer.initialize()
    renderer.show()
    QTest.qWait(300)

    calls = _counting_tints(monkeypatch)
    renderer.set_state(AuraState.THINKING)
    QTest.qWait(900)  # TRANSITION_MS (500ms) + margin

    assert len(calls) > 0  # the cross-fade actually repainted
    assert _overlay_widget(renderer)._pixmap is not None
    settled = len(calls)
    QTest.qWait(800)
    assert len(calls) == settled  # fade finished: no more tint work

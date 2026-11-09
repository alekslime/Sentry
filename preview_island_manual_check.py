"""Manual, throwaway preview for the Dynamic Island widget (Part A).

This is NOT part of Sentry itself and is not wired into main.py -- it just
shows the widget standalone so you can eyeball the real color/frosted-
glass look on an actual monitor before we decide anything about Part B's
real activation triggers (global hotkey / wake word).

Run it directly:

    python preview_island_manual_check.py

Controls:
    Space   toggle collapsed <-> expanded
    Esc     quit

Safe to delete once you've had a look -- it'll be superseded by real
activation wiring in Part B.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QWidget

from app.dynamic_island import DynamicIslandWidget


def wire_preview_shortcuts(
    widget: QWidget, on_toggle: Callable[[], None], on_quit: Callable[[], None]
) -> None:
    """Space toggles the island, Esc quits. Split out from main() so the
    wiring is covered by tests/test_preview_shortcuts.py. Uses the
    documented `.activated.connect()` form rather than an `activated=`
    constructor kwarg, which PySide6's typed signatures don't declare.
    """
    toggle_shortcut = QShortcut(QKeySequence("Space"), widget)
    toggle_shortcut.activated.connect(on_toggle)
    quit_shortcut = QShortcut(QKeySequence("Escape"), widget)
    quit_shortcut.activated.connect(on_quit)


def main() -> None:
    app = QApplication(sys.argv)

    island = DynamicIslandWidget()
    island.show()
    # WA_ShowWithoutActivating (set inside the widget, matching the Aura
    # overlay's convention) means it won't take keyboard focus on its own --
    # force it here just for this manual preview so Space/Esc work.
    island.raise_()
    island.activateWindow()

    wire_preview_shortcuts(island, island.toggle, app.quit)

    print("Dynamic Island preview running. Space = toggle, Esc = quit.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

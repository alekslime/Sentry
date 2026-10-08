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

from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import QApplication

from app.dynamic_island import DynamicIslandWidget


def main() -> None:
    app = QApplication(sys.argv)

    island = DynamicIslandWidget()
    island.show()
    # WA_ShowWithoutActivating (set inside the widget, matching the Aura
    # overlay's convention) means it won't take keyboard focus on its own --
    # force it here just for this manual preview so Space/Esc work.
    island.raise_()
    island.activateWindow()

    QShortcut(QKeySequence("Space"), island, activated=island.toggle)
    QShortcut(QKeySequence("Escape"), island, activated=app.quit)

    print("Dynamic Island preview running. Space = toggle, Esc = quit.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

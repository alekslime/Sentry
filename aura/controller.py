"""The single point of contact between Sentry's AI/voice/vision logic and
whatever is currently rendering Aura.

Nothing outside `aura/` should ever import a renderer directly — everything
goes through `AuraController`. This is what keeps Aura swappable and
independent from the rest of the app, per the project's design principles.
"""

from __future__ import annotations

import logging

from aura.renderer.base import AuraRenderer
from aura.states import AuraState

logger = logging.getLogger(__name__)


class AuraController:
    """Owns the active renderer and the current Aura state."""

    def __init__(self, renderer: AuraRenderer) -> None:
        self._renderer = renderer
        self._state = AuraState.IDLE

    def start(self) -> None:
        """Initialize and show Aura. Call once during app startup."""
        self._renderer.initialize()
        self._renderer.set_state(self._state)
        self._renderer.show()
        logger.info("Aura started (state=%s).", self._state.value)

    def set_state(self, state: AuraState) -> None:
        """Transition Aura to a new state."""
        self._state = state
        self._renderer.set_state(state)

    def show_target_box(self, x: int, y: int, w: int, h: int) -> None:
        """Flash an outline around a specific screen region (Milestone
        7), in real screen pixel coordinates. See
        `AuraRenderer.show_target_box` for the untrusted-input contract.
        """
        self._renderer.show_target_box(x, y, w, h)

    def clear_target_box(self) -> None:
        """Dismiss the flashed target box immediately, if one is showing."""
        self._renderer.clear_target_box()

    @property
    def state(self) -> AuraState:
        return self._state

    def stop(self) -> None:
        """Hide and shut down Aura. Call once during app shutdown."""
        self._renderer.hide()
        self._renderer.shutdown()
        logger.info("Aura stopped.")

"""Defines the interface every Aura renderer must implement.

Aura is intentionally decoupled from the rest of Sentry: the AI/voice/vision
logic only ever talks to an `AuraRenderer`, never to rendering internals.
This means the actual GPU-rendered ambient glow (a later milestone) can be
swapped in behind this interface without touching any other module, and
community-created themes can eventually provide their own renderers.

This file defines structure only — no rendering happens yet. See
`aura/renderer/null_renderer.py` for the current no-op implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from aura.states import AuraState


class AuraRenderer(ABC):
    """Abstract interface for anything that can visually represent Aura."""

    @abstractmethod
    def initialize(self) -> None:
        """Prepare the renderer (allocate windows/surfaces/GPU resources).

        Called once during application startup, after the Qt event loop
        exists but before Aura is shown.
        """

    @abstractmethod
    def set_state(self, state: AuraState) -> None:
        """Update the visual state Aura should represent (color/animation)."""

    @abstractmethod
    def show_target_box(self, x: int, y: int, w: int, h: int) -> None:
        """Flash a rectangular outline around a specific screen region
        (Milestone 7) for a brief, implementation-defined duration, given
        in real screen pixel coordinates with a top-left origin.

        Coordinates originate from a vision model's output and are not
        trusted as-is: implementations must clamp the rect to the screen
        bounds and enforce a sane minimum size rather than assuming the
        caller already validated it.

        This is orthogonal to `set_state()`, which controls the ambient
        glow's color. Calling this does not change the current
        `AuraState`, and the ambient glow keeps running independently.
        """

    @abstractmethod
    def clear_target_box(self) -> None:
        """Dismiss the flashed target box immediately, if one is showing.

        A no-op if no target box is currently active. Implementations
        that auto-dismiss the box after a timeout (see
        `show_target_box`) may still call this on the next query, so it
        should be safe to call redundantly.
        """

    @abstractmethod
    def show(self) -> None:
        """Make the Aura overlay visible."""

    @abstractmethod
    def hide(self) -> None:
        """Hide the Aura overlay without destroying it."""

    @abstractmethod
    def shutdown(self) -> None:
        """Release any resources held by the renderer. Called on app exit."""

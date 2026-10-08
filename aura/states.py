"""Defines the states Aura can communicate and their associated colors.

This module has zero dependency on rendering or AI logic — it is pure data,
shared by whatever decides Sentry's state (voice/LLM/vision modules, later)
and whatever renders it (aura/renderer).
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class RGB(NamedTuple):
    r: int
    g: int
    b: int


class AuraState(Enum):
    """The high-level state Sentry is currently in, as communicated by Aura."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    ERROR = "error"
    SPEAKING = "speaking"


# Default color mapping per the Aura design language (see docs/ARCHITECTURE.md).
# Themes may override these; this is the fallback "default" theme.
DEFAULT_STATE_COLORS: dict[AuraState, RGB] = {
    AuraState.IDLE: RGB(66, 133, 244),                    # Blue
    AuraState.LISTENING: RGB(52, 168, 83),                # Green
    AuraState.THINKING: RGB(155, 89, 182),                # Purple
    AuraState.WAITING_FOR_CONFIRMATION: RGB(241, 196, 15),  # Yellow
    AuraState.ERROR: RGB(231, 76, 60),                    # Red
    AuraState.SPEAKING: RGB(0, 188, 212),                 # Cyan
}

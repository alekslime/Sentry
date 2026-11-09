"""Global (system-wide) keyboard hotkey registration for the Dynamic
Island's activation trigger (Milestone 10, Part B).

Windows-only for now, matching the project's stated target platform (see
README.md / pyproject.toml's `windows` extra). Qt's own `QShortcut` only
fires when the application has keyboard focus, which doesn't work for a
background copilot the user expects to summon from anywhere -- so this
goes around Qt entirely and uses the real Win32 `RegisterHotKey` API via
`ctypes` (stdlib, no extra dependency -- `pywin32` was considered, see
`docs/DECISIONS.md`, but the whole ask here is exactly one API call plus
one message type, which `ctypes.windll.user32` covers directly).

`RegisterHotKey(None, ...)` (a NULL `hwnd`) associates the hotkey with the
*calling thread's* message queue rather than a specific window. Qt's own
event loop (`QApplication.exec()`) already pumps that thread's Windows
message queue via `GetMessage`/`DispatchMessage` under the hood, so a
`QAbstractNativeEventFilter` installed on the `QApplication` sees the
resulting `WM_HOTKEY` message for free -- no separate thread, no extra
message loop to run ourselves.
"""

from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# Win32 modifier flags (winuser.h). MOD_NOREPEAT (Vista+) stops Windows from
# re-sending WM_HOTKEY on every repeat tick while the key is held down --
# without it, holding the hotkey would spam island.toggle() every ~50ms.
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

_MODIFIER_ALIASES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
    "super": MOD_WIN,
}

# Deliberately small: only what a hotkey binding realistically needs
# (letters, digits, space, function keys). Extend if a real config ever
# needs more -- VK codes for A-Z/0-9 happen to equal their ASCII
# uppercase/digit codepoints, which is why those two ranges are generated
# rather than listed by hand.
_VK_NAMED = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},  # F1=0x70 .. F12=0x7B
}
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _VK_NAMED[_c.lower()] = ord(_c)
for _d in "0123456789":
    _VK_NAMED[_d] = ord(_d)


def parse_hotkey(hotkey: str) -> tuple[int, int]:
    """Parse a `"ctrl+shift+space"`-style string into `(modifiers, vk)`
    for `RegisterHotKey`. Case-insensitive, `+`-separated, exactly one
    non-modifier key required. Raises `ValueError` on anything else
    (unknown token, no main key, more than one main key) -- callers
    should treat that the same as any other bad-config value (log and
    fall back, per this project's usual graceful-degradation shape),
    not let it crash startup.
    """
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"Empty hotkey string: {hotkey!r}")

    modifiers = MOD_NOREPEAT
    main_key: str | None = None
    for part in parts:
        if part in _MODIFIER_ALIASES:
            modifiers |= _MODIFIER_ALIASES[part]
        elif main_key is None:
            main_key = part
        else:
            raise ValueError(
                f"Hotkey {hotkey!r} has more than one non-modifier key "
                f"({main_key!r} and {part!r})"
            )

    if main_key is None:
        raise ValueError(f"Hotkey {hotkey!r} has no non-modifier key")
    if main_key not in _VK_NAMED:
        raise ValueError(f"Unrecognized key {main_key!r} in hotkey {hotkey!r}")

    return modifiers, _VK_NAMED[main_key]


class GlobalHotkeyFilter(QObject, QAbstractNativeEventFilter):
    """Registers one system-wide hotkey and emits `activated` (on the Qt
    main thread -- see module docstring for why no cross-thread bridge is
    needed here, unlike `app/wake_word_bridge.py` etc.) whenever it fires.

    No-ops cleanly (`is_registered` stays `False`, `activated` never
    fires) on any non-Windows platform, or if `RegisterHotKey` itself
    fails (e.g. another application already owns that combination) --
    the Dynamic Island remains fully usable via the wake word either way,
    this is just a convenience trigger on top.
    """

    activated = Signal()

    def __init__(
        self, hotkey_id: int, modifiers: int, vk: int, parent: QObject | None = None
    ) -> None:
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._hotkey_id = hotkey_id
        self._modifiers = modifiers
        self._vk = vk
        self.is_registered = False

        if not _IS_WINDOWS:
            logger.info(
                "Global hotkey registration skipped — only implemented for "
                "Windows (sys.platform=%r). The Dynamic Island can still be "
                "triggered by the wake word.",
                sys.platform,
            )
            return

        # use_last_error=True is required here, not optional: without it,
        # ctypes doesn't capture GetLastError() for calls through this
        # handle at all, so ctypes.get_last_error() below could return a
        # stale/unrelated value (e.g. 0, "success") even when
        # RegisterHotKey genuinely failed -- see
        # https://github.com/python/cpython/issues/132888 and
        # https://github.com/enthought/pywin32-ctypes/issues/122. Kept as
        # an instance attribute (rather than a fresh ctypes.windll.user32
        # lookup) so unregister() below queries the same handle.
        # getattr, not direct attribute access: typeshed only defines
        # WinDLL/get_last_error on Windows, and mypy checks this file on
        # Linux CI too (B009 is wrong here -- this isn't about safety,
        # it's about static-analysis portability across platforms).
        # Unreachable off-Windows anyway (the _IS_WINDOWS guard above
        # already returned), so no default -- fail loud.
        self._user32 = getattr(ctypes, "WinDLL")("user32", use_last_error=True)  # noqa: B009
        if not self._user32.RegisterHotKey(None, self._hotkey_id, self._modifiers, self._vk):
            error_code = getattr(ctypes, "get_last_error")()  # noqa: B009
            logger.warning(
                "RegisterHotKey failed (id=%d, modifiers=0x%x, vk=0x%x, "
                "Win32 error=%d) — likely already bound by another "
                "application. The Dynamic Island can still be triggered by "
                "the wake word.",
                self._hotkey_id,
                self._modifiers,
                self._vk,
                error_code,
            )
            return

        self.is_registered = True
        logger.info(
            "Global hotkey registered (id=%d, modifiers=0x%x, vk=0x%x).",
            self._hotkey_id,
            self._modifiers,
            self._vk,
        )

    def unregister(self) -> None:
        """Idempotent — safe to call even if registration never
        succeeded, and safe to call twice."""
        if not self.is_registered:
            return
        self._user32.UnregisterHotKey(None, self._hotkey_id)
        self.is_registered = False
        logger.debug("Global hotkey (id=%d) unregistered.", self._hotkey_id)

    def nativeEventFilter(self, event_type, message):
        # Only relevant on Windows, where Qt tags native messages
        # "windows_generic_MSG" and hands back a MSG* as an int address.
        if not self.is_registered:
            return False, 0
        try:
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError, OverflowError):
            # `message` is an opaque pointer-sized value handed back by Qt;
            # anything unparseable means "not a hotkey message we care
            # about", never a reason to take down the event filter.
            return False, 0

        if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
            self.activated.emit()
        return False, 0

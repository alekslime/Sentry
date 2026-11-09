"""`main.py` must degrade (not crash) when an optional extra's *native* library fails.

A missing pip package raises ImportError, which main.py already guards. But
a missing *system* library (PortAudio for sounddevice, CUDA DLLs for
faster-whisper) raises OSError from inside the imported package -- which
used to escape the guards and kill the whole app at import. Simulated at
the import-system level by raising OSError for 'sounddevice' exactly the
way sounddevice itself does when PortAudio is absent; breaking the real
OS audio stack to reproduce this was out of scope.
"""

from __future__ import annotations

import importlib
import sys


class _NativeLibBlocker:
    """Meta-path finder that fails one module's import with OSError."""

    def __init__(self, module: str) -> None:
        self.module = module

    def find_spec(self, name, path=None, target=None):
        if name == self.module or name.startswith(self.module + "."):
            raise OSError(f"simulated missing native lib for: {name}")


def test_main_import_degrades_when_native_lib_raises_oserror(monkeypatch) -> None:
    import main as main_module

    # Drop the cached modules that (transitively) import sounddevice so the
    # reload below really re-executes their imports under the blocker.
    for cached in ("sounddevice", "voice.service", "voice.audio_stream", "tts.engine"):
        monkeypatch.delitem(sys.modules, cached, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_NativeLibBlocker("sounddevice")] + sys.meta_path)
    try:
        reloaded = importlib.reload(main_module)
        assert reloaded._VOICE_AVAILABLE is False
        assert reloaded._TTS_AVAILABLE is False
    finally:
        # Restore the real modules/flags so later tests see a normal main.
        monkeypatch.undo()
        importlib.reload(main_module)

    import main as restored

    assert restored._resize_for_vision is not None

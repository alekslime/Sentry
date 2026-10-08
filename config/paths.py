"""Centralized filesystem paths used across Sentry.

All other modules should import paths from here rather than constructing
their own relative paths. This keeps the app portable and makes it easy to
relocate data/log/model directories in the future (e.g. for a Windows
installer that uses %APPDATA%).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME = "Sentry"

# The project was called Iris before it was renamed. Existing installs keep
# their config, history and downloaded models under the old folder name;
# `ensure_app_directories()` moves it to the new name on first launch.
LEGACY_APP_NAME = "Iris"
_LEGACY_DEV_DIRNAME = ".iris_data"
_DEV_DIRNAME = ".sentry_data"

# Root of the repository / installed application (folder containing main.py).
ROOT_DIR: Path = Path(__file__).resolve().parent.parent

# --- User data locations -----------------------------------------------
# On Windows this resolves under %APPDATA%\Sentry. On other platforms it falls
# back to a local .sentry_data folder so development on Linux/macOS still works.
if os.name == "nt":
    _appdata = os.environ.get("APPDATA")
    APP_DATA_DIR: Path = Path(_appdata) / APP_NAME if _appdata else ROOT_DIR / _DEV_DIRNAME
    LEGACY_APP_DATA_DIR: Path = (
        Path(_appdata) / LEGACY_APP_NAME if _appdata else ROOT_DIR / _LEGACY_DEV_DIRNAME
    )
else:
    APP_DATA_DIR = ROOT_DIR / _DEV_DIRNAME
    LEGACY_APP_DATA_DIR = ROOT_DIR / _LEGACY_DEV_DIRNAME

CONFIG_DIR: Path = APP_DATA_DIR / "config"
LOG_DIR: Path = APP_DATA_DIR / "logs"
DATA_DIR: Path = APP_DATA_DIR / "data"
MODELS_DIR: Path = APP_DATA_DIR / "models"

# Bundled defaults shipped with the repo (read-only, version controlled).
DEFAULT_CONFIG_FILE: Path = ROOT_DIR / "config" / "default_config.yaml"

# Active user config file (created on first run, user-editable, gitignored).
USER_CONFIG_FILE: Path = CONFIG_DIR / "config.yaml"


def migrate_legacy_app_data(
    legacy_dir: Path | None = None, new_dir: Path | None = None
) -> bool:
    """Move the pre-rename (Iris) data folder to the new (Sentry) location.

    Only acts when the legacy folder exists and the new one does not, so it
    never overwrites or merges into data the new version has already
    written. Returns True if a move happened. A failed move (for example a
    file locked by another process) is logged and leaves the old folder
    untouched; the app then starts with a fresh data folder.

    The arguments exist so tests can point this at a temp directory.
    """
    legacy_dir = LEGACY_APP_DATA_DIR if legacy_dir is None else legacy_dir
    new_dir = APP_DATA_DIR if new_dir is None else new_dir

    if not legacy_dir.is_dir() or new_dir.exists():
        return False
    try:
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        legacy_dir.rename(new_dir)
    except OSError as exc:
        logger.warning(
            "Could not move old data folder %s to %s (%s). Starting with a fresh "
            "folder; your old config and history are still in the old location.",
            legacy_dir,
            new_dir,
            exc,
        )
        return False
    return True


def ensure_app_directories() -> None:
    """Create all writable application directories if they do not exist yet.

    Safe to call multiple times. Should be called once during application
    startup, before logging or config loading occurs. Also migrates a
    pre-rename data folder first, so existing settings and history carry over.
    """
    migrate_legacy_app_data()
    for directory in (APP_DATA_DIR, CONFIG_DIR, LOG_DIR, DATA_DIR, MODELS_DIR):
        directory.mkdir(parents=True, exist_ok=True)

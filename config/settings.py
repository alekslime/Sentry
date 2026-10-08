"""Loads and persists Sentry's configuration.

Usage:
    from config.settings import get_settings
    settings = get_settings()
    print(settings.aura.theme)

Load order:
    1. Bundled `config/default_config.yaml` (always present, version controlled).
    2. User's `%APPDATA%/Sentry/config/config.yaml` (created on first run, editable).
       Values here override the defaults.

The merged result is validated against `AppSettings` so the rest of the
codebase can trust it is well-formed.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from config.paths import DEFAULT_CONFIG_FILE, USER_CONFIG_FILE, ensure_app_directories
from config.schema import AppSettings

logger = logging.getLogger(__name__)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` into `base`, returning a new dict."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def _backfill_missing(user: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Add keys present in `defaults` but absent from `user`, recursively.

    Existing user values are never touched or reordered, even if the
    corresponding default has since changed -- only genuinely missing keys
    (e.g. a whole new settings section added in a later version of Sentry)
    are added. Returns the possibly-updated dict and whether anything
    changed, so the caller can skip rewriting the file when nothing did.
    """
    result = dict(user)
    changed = False
    for key, default_value in defaults.items():
        if key not in result:
            result[key] = default_value
            changed = True
        elif isinstance(default_value, dict) and isinstance(result[key], dict):
            result[key], sub_changed = _backfill_missing(result[key], default_value)
            changed = changed or sub_changed
    return result, changed


def load_settings() -> AppSettings:
    """Build an `AppSettings` instance from defaults + user overrides.

    Creates the user config file on first run so it exists for the user to
    edit going forward. On later runs, if the schema has grown since the
    user's file was created (new settings sections/fields added in a newer
    version of Sentry), those missing keys are backfilled into the existing
    file so they show up for editing -- without touching any values the
    user has already customized. See docs/DECISIONS.md.
    """
    ensure_app_directories()

    defaults = _read_yaml(DEFAULT_CONFIG_FILE)
    if not defaults:
        logger.warning("Default config file missing or empty at %s", DEFAULT_CONFIG_FILE)

    user_overrides = _read_yaml(USER_CONFIG_FILE)
    merged = _deep_merge(defaults, user_overrides)

    settings = AppSettings.model_validate(merged)

    if not USER_CONFIG_FILE.exists():
        logger.info("First run detected — writing user config to %s", USER_CONFIG_FILE)
        _write_yaml(USER_CONFIG_FILE, settings.model_dump())
    else:
        backfilled, changed = _backfill_missing(user_overrides, defaults)
        if changed:
            logger.info(
                "User config at %s is missing keys introduced by a newer version of "
                "Sentry -- backfilling them without touching your existing settings.",
                USER_CONFIG_FILE,
            )
            _write_yaml(USER_CONFIG_FILE, backfilled)

    return settings


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the process-wide cached `AppSettings` instance.

    Cached so every module gets the same object without re-reading disk.
    Call `get_settings.cache_clear()` if settings need to be reloaded at
    runtime (e.g. after the user edits config through a future settings UI).
    """
    return load_settings()

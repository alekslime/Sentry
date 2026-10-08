"""Tests for `config/settings.py`, focused on the stale-user-config bug
fixed 2026-07-11 (see docs/DECISIONS.md).

Root cause: `load_settings()` only ever wrote the user config file when it
didn't exist yet. A user file created by an early version of Iris (before
newer settings sections like `vision`/`llm`/`speech`/`debug` existed in the
schema) stayed frozen at that old shape forever, since the file's mere
existence skipped the write branch on every later run -- even though
runtime behavior was unaffected (defaults still applied via the merge),
the user had no `vision:` section in their file to actually edit.

These tests exercise `_backfill_missing` and `load_settings` directly
against real files under `tmp_path`, patching `config.paths` so nothing
touches the real `%APPDATA%/Iris` directory.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import config.settings as settings_module
from config.settings import _backfill_missing, _rename_legacy_values, load_settings


def test_backfill_missing_adds_new_top_level_section() -> None:
    user = {"aura": {"enabled": True, "theme": "default"}}
    defaults = {
        "aura": {"enabled": True, "theme": "default"},
        "vision": {"enabled": False, "monitor_index": 0},
    }

    result, changed = _backfill_missing(user, defaults)

    assert changed is True
    assert result["vision"] == {"enabled": False, "monitor_index": 0}


def test_backfill_missing_adds_new_nested_field_without_touching_siblings() -> None:
    user = {"voice": {"wake_word_model": "hey_jarvis", "detection_threshold": 0.9}}
    defaults = {
        "voice": {
            "wake_word_model": "hey_jarvis",
            "detection_threshold": 0.5,
            "cooldown_seconds": 1.5,
        }
    }

    result, changed = _backfill_missing(user, defaults)

    assert changed is True
    # User's customized value is preserved, not clobbered by the default.
    assert result["voice"]["detection_threshold"] == 0.9
    # The genuinely-missing field is backfilled from defaults.
    assert result["voice"]["cooldown_seconds"] == 1.5


def test_backfill_missing_reports_no_change_when_nothing_missing() -> None:
    user = {"aura": {"enabled": True, "theme": "default"}}
    defaults = {"aura": {"enabled": True, "theme": "default"}}

    result, changed = _backfill_missing(user, defaults)

    assert changed is False
    assert result == user


def test_load_settings_backfills_stale_user_file_on_disk(tmp_path, monkeypatch) -> None:
    """Reproduces the exact bug: a user config.yaml written before `vision`
    existed in the schema should gain a `vision:` section on the next
    launch, without losing the user's existing customizations.
    """
    default_file = tmp_path / "default_config.yaml"
    default_file.write_text(
        yaml.safe_dump(
            {
                "app_name": "Iris",
                "aura": {"enabled": True, "theme": "default"},
                "vision": {"enabled": False, "monitor_index": 0},
            }
        )
    )

    user_file = tmp_path / "config.yaml"
    # Simulate an old user file predating the `vision` section, with a
    # customization (theme) that must survive the backfill.
    user_file.write_text(yaml.safe_dump({"aura": {"theme": "midnight"}}))

    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_FILE", default_file)
    monkeypatch.setattr(settings_module, "USER_CONFIG_FILE", user_file)
    monkeypatch.setattr(settings_module, "ensure_app_directories", lambda: None)

    load_settings()

    rewritten = yaml.safe_load(user_file.read_text())
    assert "vision" in rewritten, "missing section should have been backfilled"
    assert rewritten["aura"]["theme"] == "midnight", "existing user value must survive"


def test_load_settings_does_not_rewrite_file_when_up_to_date(tmp_path, monkeypatch) -> None:
    """If the user file already has every key the defaults have, the file
    on disk should be left untouched (no unnecessary rewrite/reformat).
    """
    default_file = tmp_path / "default_config.yaml"
    default_file.write_text(yaml.safe_dump({"aura": {"enabled": True, "theme": "default"}}))

    user_file = tmp_path / "config.yaml"
    user_file.write_text(yaml.safe_dump({"aura": {"enabled": True, "theme": "midnight"}}))

    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_FILE", default_file)
    monkeypatch.setattr(settings_module, "USER_CONFIG_FILE", user_file)
    monkeypatch.setattr(settings_module, "ensure_app_directories", lambda: None)

    before = user_file.read_text()
    load_settings()
    after = user_file.read_text()

    assert before == after


def test_rename_legacy_values_updates_name_and_prompt_phrase_only() -> None:
    user = {
        "app_name": "Iris",
        "llm": {"system_prompt": "You are Iris, a local AI desktop copilot. Be brief."},
        "aura": {"theme": "midnight"},
    }

    result, changed = _rename_legacy_values(user)

    assert changed is True
    assert result["app_name"] == "Sentry"
    assert result["llm"]["system_prompt"] == "You are Sentry, a local AI desktop copilot. Be brief."
    assert result["aura"] == {"theme": "midnight"}
    assert user["app_name"] == "Iris", "input must not be mutated"


def test_rename_legacy_values_leaves_custom_values_alone() -> None:
    user = {"app_name": "My Assistant", "llm": {"system_prompt": "Talk like a pirate."}}

    result, changed = _rename_legacy_values(user)

    assert changed is False
    assert result == user


def test_load_settings_migrates_legacy_name_in_user_file(tmp_path, monkeypatch) -> None:
    default_file = tmp_path / "default_config.yaml"
    default_file.write_text(yaml.safe_dump({"app_name": "Sentry", "aura": {"theme": "default"}}))

    user_file = tmp_path / "config.yaml"
    user_file.write_text(yaml.safe_dump({"app_name": "Iris", "aura": {"theme": "midnight"}}))

    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_FILE", default_file)
    monkeypatch.setattr(settings_module, "USER_CONFIG_FILE", user_file)
    monkeypatch.setattr(settings_module, "ensure_app_directories", lambda: None)

    settings = load_settings()

    assert settings.app_name == "Sentry"
    rewritten = yaml.safe_load(user_file.read_text())
    assert rewritten["app_name"] == "Sentry"
    assert rewritten["aura"]["theme"] == "midnight"

"""Tests for the Iris -> Sentry data-folder migration in `config/paths.py`.

Every test points the migration at a pytest temp directory, so none of them
touch the real `%APPDATA%` folders.
"""

from __future__ import annotations

from pathlib import Path

from config.paths import migrate_legacy_app_data


def _make_legacy(root: Path) -> Path:
    legacy = root / "Iris"
    (legacy / "config").mkdir(parents=True)
    (legacy / "config" / "config.yaml").write_text("app_name: Iris\n", encoding="utf-8")
    (legacy / "data").mkdir()
    (legacy / "data" / "conversations.db").write_bytes(b"history")
    return legacy


def test_moves_legacy_folder_when_new_one_is_absent(tmp_path: Path) -> None:
    legacy = _make_legacy(tmp_path)
    new = tmp_path / "Sentry"

    assert migrate_legacy_app_data(legacy, new) is True

    assert not legacy.exists()
    assert (new / "config" / "config.yaml").read_text(encoding="utf-8") == "app_name: Iris\n"
    assert (new / "data" / "conversations.db").read_bytes() == b"history"


def test_does_nothing_when_there_is_no_legacy_folder(tmp_path: Path) -> None:
    new = tmp_path / "Sentry"

    assert migrate_legacy_app_data(tmp_path / "Iris", new) is False
    assert not new.exists()


def test_never_overwrites_an_existing_new_folder(tmp_path: Path) -> None:
    legacy = _make_legacy(tmp_path)
    new = tmp_path / "Sentry"
    new.mkdir()
    (new / "marker.txt").write_text("keep me", encoding="utf-8")

    assert migrate_legacy_app_data(legacy, new) is False

    assert (legacy / "config" / "config.yaml").exists()
    assert (new / "marker.txt").read_text(encoding="utf-8") == "keep me"
    assert not (new / "config").exists()


def test_failed_move_leaves_legacy_folder_alone(tmp_path: Path, monkeypatch) -> None:
    legacy = _make_legacy(tmp_path)
    new = tmp_path / "Sentry"

    def locked(self: Path, target: Path) -> None:
        raise PermissionError("in use by another process")

    monkeypatch.setattr(Path, "rename", locked)

    assert migrate_legacy_app_data(legacy, new) is False
    assert (legacy / "config" / "config.yaml").exists()
    assert not new.exists()

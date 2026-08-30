"""The runtime notes directory must follow the NOTES_DIR environment override."""

import importlib
from pathlib import Path

from src.tools import notes as notes_module


def _reload_with_env(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("NOTES_DIR", raising=False)
    else:
        monkeypatch.setenv("NOTES_DIR", value)
    return importlib.reload(notes_module)


def test_notes_dir_defaults_to_workspace(monkeypatch):
    reloaded = _reload_with_env(monkeypatch, None)

    assert reloaded.NOTES_DIR == Path("./notes")


def test_empty_notes_dir_env_falls_back_to_workspace(monkeypatch):
    reloaded = _reload_with_env(monkeypatch, "")

    assert reloaded.NOTES_DIR == Path("./notes")


def test_notes_dir_follows_env_override(monkeypatch, tmp_path):
    target = tmp_path / "persisted-notes"
    reloaded = _reload_with_env(monkeypatch, str(target))

    assert reloaded.NOTES_DIR == target

    reloaded._ensure_notes_dir()
    assert target.is_dir()

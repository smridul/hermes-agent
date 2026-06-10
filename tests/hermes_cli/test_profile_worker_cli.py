"""Tests for the hermes profile-worker CLI helpers."""

from __future__ import annotations

import pytest


def test_resolve_profile_path_returns_existing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    profile_root = tmp_path / "profiles" / "family"
    profile_root.mkdir(parents=True)

    from hermes_cli.profile_worker_cli import resolve_profile_path

    resolved = resolve_profile_path("family")
    # get_default_hermes_root may resolve symlinks (/var <-> /private/var
    # on macOS); compare resolved Path objects so we don't fail on that.
    assert resolved.resolve() == profile_root.resolve()


def test_resolve_profile_path_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli.profile_worker_cli import resolve_profile_path

    with pytest.raises(FileNotFoundError):
        resolve_profile_path("nonexistent")

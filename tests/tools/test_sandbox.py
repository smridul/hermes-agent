"""Tests for tools/_sandbox.py — Layer 1 path guard + Layer 2 helpers.

Layer 2 (bwrap wrap) is verified for the *decision* layer: when sandbox
is off, or env_type isn't local, or bwrap isn't available, the command
must come back unchanged.  We don't shell out to a real bwrap here —
that's covered by the operator activation checklist in
``docs/profile_worker_sandboxing.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools import _sandbox


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    """Set up a fake non-primary profile and reset _sandbox caches."""
    root = tmp_path / "hermes"
    profile_dir = root / "profiles" / "test_profile"
    profile_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile_dir))
    monkeypatch.setenv("HERMES_SANDBOX", "strict")
    _sandbox._reset_cache_for_tests()
    yield profile_dir
    _sandbox._reset_cache_for_tests()


def test_enabled_requires_env_var(tmp_path, monkeypatch):
    """Without HERMES_SANDBOX=strict, sandbox stays off even with profile path."""
    profile_dir = tmp_path / "hermes" / "profiles" / "p"
    profile_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile_dir))
    monkeypatch.delenv("HERMES_SANDBOX", raising=False)
    _sandbox._reset_cache_for_tests()
    assert _sandbox.enabled() is False


def test_enabled_requires_profile_path(tmp_path, monkeypatch):
    """Primary profile (HERMES_HOME not under profiles/) stays unsandboxed."""
    primary = tmp_path / "hermes"
    primary.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(primary))
    monkeypatch.setenv("HERMES_SANDBOX", "strict")
    _sandbox._reset_cache_for_tests()
    assert _sandbox.enabled() is False


def test_enabled_when_both_present(profile_env):
    assert _sandbox.enabled() is True
    assert _sandbox.profile_name() == "test_profile"


def test_check_path_no_op_when_disabled(tmp_path, monkeypatch):
    """All sandbox checks must be silent no-ops when sandbox is off."""
    monkeypatch.delenv("HERMES_SANDBOX", raising=False)
    _sandbox._reset_cache_for_tests()
    assert _sandbox.check_path("/etc/passwd") is None
    assert _sandbox.check_path("/data/hermes-agent/profiles/other/config.yaml") is None


def test_check_path_accepts_profile_home(profile_env):
    assert _sandbox.check_path(str(profile_env / "config.yaml")) is None
    assert _sandbox.check_path(str(profile_env / "sub" / "deep.txt")) is None


def test_check_path_accepts_per_profile_scratch(profile_env):
    scratch = Path("/tmp") / "hermes-test_profile" / "x.json"
    assert _sandbox.check_path(str(scratch)) is None


def test_check_path_rejects_other_profile(profile_env, tmp_path):
    other = tmp_path / "hermes" / "profiles" / "another"
    other.mkdir(parents=True)
    err = _sandbox.check_path(str(other / "config.yaml"))
    assert err is not None
    assert "outside this profile's allowlist" in err


def test_check_path_rejects_system_paths(profile_env):
    assert _sandbox.check_path("/etc/passwd") is not None
    assert _sandbox.check_path("/root/.ssh/id_rsa") is not None


def test_check_path_rejects_shared_tmp(profile_env):
    """The general /tmp is NOT in the allowlist — only /tmp/hermes-<profile>."""
    err = _sandbox.check_path("/tmp/foo.json")
    assert err is not None


def test_check_path_rejects_symlink_escape(profile_env, tmp_path):
    """A symlink pointing out of the allowlist must not bypass the check."""
    target = tmp_path / "outside.txt"
    target.write_text("secret")
    link = profile_env / "escape"
    link.symlink_to(target)
    err = _sandbox.check_path(str(link))
    assert err is not None


def test_check_path_rejects_dotdot_traversal(profile_env):
    err = _sandbox.check_path(str(profile_env / ".." / ".." / "etc" / "passwd"))
    assert err is not None


def test_check_paths_short_circuits_on_first_bad(profile_env):
    paths = [
        str(profile_env / "a.txt"),
        "/etc/passwd",
        str(profile_env / "b.txt"),
    ]
    err = _sandbox.check_paths(paths)
    assert err is not None
    assert "/etc/passwd" in err


def test_maybe_wrap_command_no_op_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_SANDBOX", raising=False)
    _sandbox._reset_cache_for_tests()
    assert _sandbox.maybe_wrap_command("ls -la", "local") == "ls -la"


def test_maybe_wrap_command_no_op_for_non_local(profile_env):
    """Cloud/docker backends already isolate; don't double-wrap."""
    assert _sandbox.maybe_wrap_command("ls", "docker") == "ls"
    assert _sandbox.maybe_wrap_command("ls", "modal") == "ls"
    assert _sandbox.maybe_wrap_command("ls", "ssh") == "ls"


def test_maybe_wrap_command_no_op_when_bwrap_unsupported(profile_env, monkeypatch):
    """When the bwrap probe fails (the eureka-hermes default), commands
    must come back unchanged so terminal_tool keeps working.
    """
    monkeypatch.setattr(_sandbox, "bwrap_supported", lambda: False)
    assert _sandbox.maybe_wrap_command("ls -la", "local") == "ls -la"


def test_maybe_wrap_command_wraps_when_supported(profile_env, monkeypatch):
    monkeypatch.setattr(_sandbox, "bwrap_supported", lambda: True)
    wrapped = _sandbox.maybe_wrap_command("echo hi", "local")
    assert wrapped.startswith("bwrap ")
    assert "--unshare-user" in wrapped
    assert "/bin/bash" in wrapped
    # Allowlist must show up as a bind mount.
    assert str(profile_env) in wrapped
    # /etc, /usr, /lib should be read-only bind-mounted (no agent writes
    # to system trees from inside the jail).
    assert "--ro-bind /usr /usr" in wrapped


def test_file_tools_read_blocked_outside_allowlist(profile_env):
    """End-to-end: read_file_tool returns a sandbox error for a bad path."""
    import json
    from tools.file_tools import read_file_tool

    out = json.loads(read_file_tool("/etc/passwd"))
    assert "error" in out
    assert "outside this profile's allowlist" in out["error"]


def test_file_tools_write_blocked_outside_allowlist(profile_env):
    import json
    from tools.file_tools import write_file_tool

    out = json.loads(write_file_tool("/etc/evil.conf", "hi"))
    assert "error" in out
    assert "outside this profile's allowlist" in out["error"]


def test_file_tools_search_blocked_outside_allowlist(profile_env):
    import json
    from tools.file_tools import search_tool

    out = json.loads(search_tool("password", path="/etc"))
    assert "error" in out
    assert "outside this profile's allowlist" in out["error"]

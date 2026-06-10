"""Profile-worker filesystem sandbox helpers.

Activated when ``HERMES_SANDBOX=strict`` is set in the worker's environment
(``hermes_cli/profile_worker_cli.py`` reads ``sandbox: strict`` from the
profile's ``config.yaml`` and exports the env var before any tool import).

Two layers:

- **Layer 1 — path guard.**  ``check_path(path)`` rejects any path that
  doesn't resolve under ``$HERMES_HOME`` or the per-profile ``/tmp``
  scratch.  File tools (read/write/patch/search) call this at entry.

- **Layer 2 — bwrap jail.**  ``maybe_wrap_command(cmd, env_type)`` returns
  the original command verbatim unless sandbox is on AND the terminal
  backend is ``local`` AND ``bwrap`` is available AND the host kernel
  permits user-namespace creation.  Otherwise it returns
  ``bash -c '...'`` wrapped in a bubblewrap invocation that bind-mounts
  only the profile dir + scratch.

The two layers are independent: Layer 1 ships everywhere; Layer 2 is a
no-op until the container is configured with ``seccomp=unconfined`` (see
``docs/profile_worker_sandboxing.md``).  Worker startup logs which layers
are active.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


_SANDBOX_ENV = "HERMES_SANDBOX"
_STRICT = "strict"


# ---------------------------------------------------------------------------
# State (computed once on first call; cheap to recompute, but cached for
# the hot path in every file/terminal tool invocation).
# ---------------------------------------------------------------------------
_cached_enabled: bool | None = None
_cached_profile_name: str | None = None
_cached_allowlist: tuple[Path, ...] | None = None
_cached_bwrap_supported: bool | None = None


def _profile_name_from_hermes_home() -> str | None:
    """Best-effort extraction of the profile name from ``HERMES_HOME``.

    HERMES_HOME for a non-primary profile is ``<root>/profiles/<name>``.
    The primary profile's HERMES_HOME has no ``profiles/`` parent — in
    that case we return ``None`` and ``enabled()`` reports off, since v1
    intentionally leaves the primary unsandboxed.
    """
    home = os.environ.get("HERMES_HOME")
    if not home:
        return None
    parts = Path(home).resolve().parts
    if len(parts) < 2 or parts[-2] != "profiles":
        return None
    return parts[-1]


def _build_allowlist(profile_name: str) -> tuple[Path, ...]:
    home = Path(os.environ["HERMES_HOME"]).resolve()
    scratch = Path("/tmp") / f"hermes-{profile_name}"
    scratch.mkdir(parents=True, exist_ok=True)
    return (home, scratch.resolve())


def _reset_cache_for_tests() -> None:
    """Reset cached state.  Tests use this; production never calls it."""
    global _cached_enabled, _cached_profile_name, _cached_allowlist
    global _cached_bwrap_supported
    _cached_enabled = None
    _cached_profile_name = None
    _cached_allowlist = None
    _cached_bwrap_supported = None


def enabled() -> bool:
    """Return True when the sandbox should apply to this process.

    Requires ``HERMES_SANDBOX=strict`` AND a recognizable per-profile
    ``HERMES_HOME``.  The primary profile (where HERMES_HOME does not
    sit under ``profiles/``) is intentionally exempt in v1 — admin tools
    that cross profile boundaries live there.
    """
    global _cached_enabled, _cached_profile_name, _cached_allowlist
    if _cached_enabled is not None:
        return _cached_enabled
    if os.environ.get(_SANDBOX_ENV, "").strip().lower() != _STRICT:
        _cached_enabled = False
        return False
    profile = _profile_name_from_hermes_home()
    if not profile:
        _cached_enabled = False
        return False
    _cached_profile_name = profile
    _cached_allowlist = _build_allowlist(profile)
    _cached_enabled = True
    logger.info(
        "filesystem sandbox active for profile %r — allowlist: %s",
        profile,
        [str(p) for p in _cached_allowlist],
    )
    return True


def profile_name() -> str | None:
    enabled()  # populate cache
    return _cached_profile_name


def allowlist() -> tuple[Path, ...]:
    enabled()
    return _cached_allowlist or ()


# ---------------------------------------------------------------------------
# Layer 1 — path guard
# ---------------------------------------------------------------------------


def _resolve(path: str | os.PathLike) -> Path:
    """Resolve symlinks/relative parts so allowlist checks can't be evaded
    via ``../`` or symlink chains pointing out of the allowlist.

    ``strict=False`` because we may be checking a path that doesn't exist
    yet (write_file creating a new file); ``resolve(strict=True)`` would
    raise instead of returning the would-be path.
    """
    return Path(os.path.expanduser(str(path))).resolve(strict=False)


def _path_under(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def check_path(path: str | os.PathLike) -> str | None:
    """Return an error message if ``path`` falls outside the allowlist.

    Returns ``None`` when sandbox is off (no-op outside profile workers)
    or when the path is allowed.  Tools should call this at the top of
    every public read/write/edit/list handler and surface the returned
    string via their normal error-response shape.
    """
    if not enabled():
        return None
    resolved = _resolve(path)
    for root in allowlist():
        if _path_under(resolved, root):
            return None
    return (
        f"Sandbox: path {str(path)!r} is outside this profile's allowlist. "
        f"Profile workers may only read/write inside their own HERMES_HOME "
        f"or /tmp/hermes-<profile>. Resolved target: {resolved}."
    )


def check_paths(paths: Iterable[str | os.PathLike]) -> str | None:
    """Convenience for tools that touch multiple paths (V4A patch, etc.)."""
    for p in paths:
        err = check_path(p)
        if err:
            return err
    return None


# ---------------------------------------------------------------------------
# Layer 2 — bwrap wrap on terminal_tool
# ---------------------------------------------------------------------------


def bwrap_supported() -> bool:
    """Return True when bwrap is installed AND can create user namespaces.

    The probe runs ``bwrap --unshare-user --ro-bind /usr /usr /bin/true``
    once at startup; failure cause (missing binary, blocked syscall via
    Docker's default seccomp profile, kernel without unprivileged
    userns) is treated uniformly as "not supported".
    """
    global _cached_bwrap_supported
    if _cached_bwrap_supported is not None:
        return _cached_bwrap_supported
    if not shutil.which("bwrap"):
        _cached_bwrap_supported = False
        return False
    try:
        rc = subprocess.run(
            [
                "bwrap",
                "--unshare-user",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/bin", "/bin",
                "--ro-bind", "/lib", "/lib",
                "/bin/true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).returncode
    except (subprocess.TimeoutExpired, OSError):
        rc = -1
    _cached_bwrap_supported = rc == 0
    if not _cached_bwrap_supported:
        logger.info(
            "filesystem sandbox: bwrap not usable (likely Docker default "
            "seccomp blocking userns clone). Layer 2 inactive; path guard "
            "(Layer 1) still applies."
        )
    return _cached_bwrap_supported


def maybe_wrap_command(command: str, env_type: str) -> str:
    """Wrap ``command`` in a bubblewrap invocation when sandbox is active.

    Returns the original command unchanged when:
    - sandbox is disabled,
    - the terminal backend isn't local (docker/modal/ssh handle their own
      isolation),
    - bwrap is missing or blocked by container seccomp.

    The wrapped form runs ``bash -c '<original cmd>'`` inside a fresh
    mount/PID namespace where only ``$HERMES_HOME`` and the per-profile
    ``/tmp`` scratch are visible under their real paths; system trees
    (/usr, /lib, /bin, /etc) are read-only bind-mounted from the host.
    """
    if not enabled():
        return command
    if env_type != "local":
        return command
    if not bwrap_supported():
        return command

    home = Path(os.environ["HERMES_HOME"]).resolve()
    scratch = Path("/tmp") / f"hermes-{profile_name()}"
    scratch.mkdir(parents=True, exist_ok=True)

    ro_binds = ["/usr", "/bin", "/lib", "/etc"]
    if Path("/lib64").exists():
        ro_binds.append("/lib64")

    argv = [
        "bwrap",
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--share-net",
    ]
    for p in ro_binds:
        argv += ["--ro-bind", p, p]
    argv += [
        "--bind", str(home), str(home),
        "--bind", str(scratch), "/tmp",
        "--proc", "/proc",
        "--dev", "/dev",
        "--chdir", str(home),
        "/bin/bash", "-c", command,
    ]
    return " ".join(shlex.quote(a) for a in argv)

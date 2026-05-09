"""CLI entrypoint for ``hermes profile-worker --name <profile>``.

Boots a Hermes worker subprocess that listens on stdin for ``MessageEvent``
envelopes and writes replies to stdout.  Each worker is a fresh Hermes
process started with ``HERMES_HOME=<profile path>`` in its environment so
all profile-aware code (skills, hooks, oauth, mirror, sessions, memory,
config) resolves to that profile by construction — sidestepping the
in-process module-level capture problem entirely.

Used by the WhatsApp sender-profile-routing feature: ingress spawns one
worker per non-primary profile listed in
``channels.whatsapp.profile_routing.profiles``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
from pathlib import Path

from hermes_constants import get_default_hermes_root


logger = logging.getLogger(__name__)


def resolve_profile_path(profile_name: str) -> Path:
    """Map a profile name to its ``HERMES_HOME`` directory.

    Profiles live at ``<root>/profiles/<name>``.  Raises
    ``FileNotFoundError`` when the directory does not exist (operators
    must create the profile via ``hermes profile create <name>`` first).
    """
    root = get_default_hermes_root()
    candidate = root / "profiles" / profile_name
    if not candidate.is_dir():
        raise FileNotFoundError(
            f"Profile {profile_name!r} not found at {candidate} "
            f"(create it with `hermes profile create {profile_name}` first)"
        )
    return candidate


def _emit_ready(name: str) -> None:
    """Write the readiness envelope to stdout so ingress knows we're up."""
    sys.stdout.write(json.dumps({"kind": "ready", "name": name}) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes profile-worker")
    parser.add_argument("--name", required=True, help="Profile name to load")
    args = parser.parse_args(argv)

    profile_path = resolve_profile_path(args.name)
    os.environ["HERMES_HOME"] = str(profile_path)

    # All worker logging goes to stderr.  Stdout is reserved for the IPC
    # JSON channel; any stray writes to stdout would corrupt it.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _emit_ready(args.name)

    return asyncio.run(_run_worker(args.name))


async def _run_worker(profile_name: str) -> int:
    """Boot a stripped-down GatewayRunner with only the IPC adapter."""
    # Lazy imports — keep cold-start cheap and ensure HERMES_HOME is in
    # ``os.environ`` before any module-level capture fires.
    from gateway.config import (
        GatewayConfig,
        Platform,
        PlatformConfig,
        load_gateway_config,
    )
    from gateway.platforms.ipc import IPCPlatformAdapter
    from gateway.run import GatewayRunner, _start_cron_ticker

    cfg: GatewayConfig
    try:
        cfg = load_gateway_config()
    except Exception:
        logger.exception(
            "profile-worker[%s]: failed to load gateway config; using defaults",
            profile_name,
        )
        cfg = GatewayConfig()

    # Workers attach NO platform adapters of their own — only IPC.  The
    # ingress process owns the WhatsApp bridge and forwards events here.
    cfg.platforms = {Platform.IPC: PlatformConfig(enabled=True)}

    runner = GatewayRunner(cfg)
    started = await runner.start()
    if not started:
        logger.error(
            "profile-worker[%s]: GatewayRunner failed to start", profile_name
        )
        return 1

    adapter = runner.adapters.get(Platform.IPC)
    if not isinstance(adapter, IPCPlatformAdapter):
        logger.error(
            "profile-worker[%s]: IPC adapter not registered after start", profile_name
        )
        await runner.stop()
        return 1

    # Cron jobs created inside this profile live in the profile's own
    # HERMES_HOME/cron/jobs.json.  The main gateway's ticker runs against
    # the *primary* profile and won't see them, so each worker has to tick
    # its own scheduler.  Mirrors the wiring in gateway.run.start_gateway.
    cron_stop = threading.Event()
    cron_thread = threading.Thread(
        target=_start_cron_ticker,
        args=(cron_stop,),
        kwargs={
            "adapters": runner.adapters,
            "loop": asyncio.get_running_loop(),
        },
        daemon=True,
        name=f"cron-ticker[{profile_name}]",
    )
    cron_thread.start()

    # Block until stdin closes (ingress shut us down) or the pump task
    # crashes for some other reason.
    try:
        await adapter.wait_until_disconnected()
    finally:
        cron_stop.set()
        cron_thread.join(timeout=5)
        try:
            await runner.stop()
        except Exception:
            logger.exception(
                "profile-worker[%s]: error during runner.stop()", profile_name
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())

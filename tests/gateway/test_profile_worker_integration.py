"""End-to-end integration tests for profile worker subprocess isolation.

These tests use a stub Python script as the worker entrypoint (rather
than the real Hermes worker) so they don't require LLM credentials.  The
stub asserts ``HERMES_HOME`` propagation and echoes the value back, which
is enough to verify the IPC handshake and environment isolation.

The corresponding test that boots the real ``hermes profile-worker``
subcommand lives at the bottom of this file (skipped when the CLI isn't
installed in the test environment).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import textwrap

import pytest

from gateway.profile_worker import ProfileWorker


def _stub_worker_script(profile_home) -> str:
    """A minimal worker that asserts HERMES_HOME and echoes events."""
    return textwrap.dedent(
        f"""
        import os, sys, json
        assert os.environ.get("HERMES_HOME") == {str(profile_home)!r}, (
            f"HERMES_HOME != expected: {{os.environ.get('HERMES_HOME')}}"
        )
        sys.stdout.write(json.dumps({{"kind":"ready","name":"stub"}}) + "\\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            env = json.loads(line)
            cid = env.get("correlation_id")
            text = env.get("event", {{}}).get("text", "")
            sys.stdout.write(json.dumps({{
                "kind": "reply",
                "correlation_id": cid,
                "reply": {{
                    "text": f"profile_home={{os.environ['HERMES_HOME']}} echoed={{text}}",
                    "error": None,
                    "media": [],
                }}
            }}) + "\\n")
            sys.stdout.flush()
        """
    )


@pytest.mark.asyncio
async def test_two_workers_each_see_own_HERMES_HOME(tmp_path):
    """Each worker subprocess only ever sees its own HERMES_HOME — never the other's."""
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)

    main_script = tmp_path / "main_stub.py"
    family_script = tmp_path / "family_stub.py"
    main_script.write_text(_stub_worker_script(main_home))
    family_script.write_text(_stub_worker_script(family_home))

    main_worker = ProfileWorker(
        name="main",
        argv=[sys.executable, str(main_script)],
        env={**os.environ, "HERMES_HOME": str(main_home)},
    )
    family_worker = ProfileWorker(
        name="family",
        argv=[sys.executable, str(family_script)],
        env={**os.environ, "HERMES_HOME": str(family_home)},
    )

    await asyncio.gather(main_worker.start(), family_worker.start())
    try:
        rm, rf = await asyncio.gather(
            main_worker.dispatch({"text": "ping"}),
            family_worker.dispatch({"text": "ping"}),
        )
        assert str(main_home) in rm["text"]
        assert str(family_home) in rf["text"]
        # Critical invariant: neither worker sees the other's HERMES_HOME.
        assert str(family_home) not in rm["text"]
        assert str(main_home) not in rf["text"]
    finally:
        await asyncio.gather(main_worker.stop(), family_worker.stop())


@pytest.mark.asyncio
async def test_concurrent_dispatch_to_different_workers_does_not_cross_contaminate(tmp_path):
    """10 alternating concurrent dispatches: every reply lands in the right HERMES_HOME."""
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)

    main_script = tmp_path / "main_stub.py"
    family_script = tmp_path / "family_stub.py"
    main_script.write_text(_stub_worker_script(main_home))
    family_script.write_text(_stub_worker_script(family_home))

    workers = {
        "main": ProfileWorker(
            "main",
            [sys.executable, str(main_script)],
            {**os.environ, "HERMES_HOME": str(main_home)},
        ),
        "family": ProfileWorker(
            "family",
            [sys.executable, str(family_script)],
            {**os.environ, "HERMES_HOME": str(family_home)},
        ),
    }
    await asyncio.gather(*[w.start() for w in workers.values()])
    try:
        results = await asyncio.gather(
            *[
                workers["main" if i % 2 == 0 else "family"].dispatch(
                    {"text": f"msg-{i}"}
                )
                for i in range(10)
            ]
        )
        for i, r in enumerate(results):
            expected_home = main_home if i % 2 == 0 else family_home
            other_home = family_home if i % 2 == 0 else main_home
            assert str(expected_home) in r["text"], f"msg-{i}: wrong HERMES_HOME"
            assert str(other_home) not in r["text"], f"msg-{i}: cross-contamination"
    finally:
        await asyncio.gather(*[w.stop() for w in workers.values()])


@pytest.mark.asyncio
async def test_real_hermes_profile_worker_subcommand_emits_readiness(tmp_path):
    """End-to-end smoke: the actual `hermes profile-worker` subcommand boots."""
    profile_home = tmp_path / "profiles" / "smoketest"
    profile_home.mkdir(parents=True)
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "hermes_cli.main",
        "profile-worker",
        "--name",
        "smoketest",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        # Read up to 60s for the readiness line.
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=60.0)
        text = line.decode().strip()
        envelope = json.loads(text)
        assert envelope.get("kind") == "ready"
        assert envelope.get("name") == "smoketest"
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

"""Tests for ProfileWorker (single subprocess + correlation ids)."""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest

from gateway.profile_worker import ProfileWorker


# Minimal echo subprocess: emits a readiness line, then echoes events.
ECHO_SCRIPT = textwrap.dedent(
    """
    import sys, json
    sys.stdout.write(json.dumps({"kind":"ready","name":"echo"}) + "\\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        env = json.loads(line)
        cid = env.get("correlation_id")
        text = env.get("event", {}).get("text", "")
        sys.stdout.write(json.dumps({
            "kind":"reply",
            "correlation_id": cid,
            "reply": {"text": "echo:" + text, "error": None, "media": []}
        }) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.mark.asyncio
async def test_profile_worker_round_trip(tmp_path):
    script = tmp_path / "echo_worker.py"
    script.write_text(ECHO_SCRIPT)

    worker = ProfileWorker(
        name="echo",
        argv=[sys.executable, str(script)],
        env={},
    )
    await worker.start()
    try:
        reply = await asyncio.wait_for(
            worker.dispatch({"text": "hello", "source": None}, timeout=5.0),
            timeout=10.0,
        )
        assert reply["text"] == "echo:hello"
        assert reply["error"] is None
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_profile_worker_handles_concurrent_dispatch(tmp_path):
    script = tmp_path / "echo_worker.py"
    script.write_text(ECHO_SCRIPT)

    worker = ProfileWorker(
        name="echo",
        argv=[sys.executable, str(script)],
        env={},
    )
    await worker.start()
    try:
        replies = await asyncio.gather(
            worker.dispatch({"text": "a", "source": None}, timeout=5.0),
            worker.dispatch({"text": "b", "source": None}, timeout=5.0),
            worker.dispatch({"text": "c", "source": None}, timeout=5.0),
        )
        assert {r["text"] for r in replies} == {"echo:a", "echo:b", "echo:c"}
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_profile_worker_dies_immediately_raises(tmp_path):
    """Worker that exits before emitting readiness should fail start()."""
    script = tmp_path / "dying.py"
    script.write_text("import sys; sys.exit(1)")

    worker = ProfileWorker(
        name="dying", argv=[sys.executable, str(script)], env={}
    )
    with pytest.raises(Exception):  # ProfileWorkerError
        await worker.start()
    await worker.stop()


@pytest.mark.asyncio
async def test_profile_worker_dispatch_after_stop_raises(tmp_path):
    script = tmp_path / "echo.py"
    script.write_text(ECHO_SCRIPT)

    worker = ProfileWorker(
        name="echo", argv=[sys.executable, str(script)], env={}
    )
    await worker.start()
    await worker.stop()

    from gateway.profile_worker import ProfileWorkerError

    with pytest.raises(ProfileWorkerError):
        await worker.dispatch({"text": "x"}, timeout=1.0)

"""Tests for ProfileWorkerManager (lifecycle + dispatch by name)."""

from __future__ import annotations

import sys
import textwrap

import pytest

from gateway.profile_worker_manager import ProfileWorkerManager, WorkerSpec


ECHO_SCRIPT = textwrap.dedent(
    """
    import sys, json
    sys.stdout.write(json.dumps({"kind":"ready","name":"echo"}) + "\\n")
    sys.stdout.flush()
    for line in sys.stdin:
        env = json.loads(line)
        cid = env.get("correlation_id")
        text = env.get("event", {}).get("text", "")
        sys.stdout.write(json.dumps({
            "kind":"reply",
            "correlation_id": cid,
            "reply": {"text": "from-worker:" + text, "error": None, "media": []}
        }) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.mark.asyncio
async def test_manager_starts_workers_and_dispatches_by_name(tmp_path):
    script = tmp_path / "echo.py"
    script.write_text(ECHO_SCRIPT)

    mgr = ProfileWorkerManager()
    await mgr.start(
        [
            WorkerSpec(name="alpha", argv=[sys.executable, str(script)], env={}),
            WorkerSpec(name="beta", argv=[sys.executable, str(script)], env={}),
        ]
    )
    try:
        ra = await mgr.dispatch("alpha", {"text": "x", "source": None})
        rb = await mgr.dispatch("beta", {"text": "y", "source": None})
        assert ra["text"] == "from-worker:x"
        assert rb["text"] == "from-worker:y"
        assert set(mgr.names) == {"alpha", "beta"}
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_dispatch_unknown_profile_raises(tmp_path):
    mgr = ProfileWorkerManager()
    await mgr.start([])
    try:
        with pytest.raises(KeyError):
            await mgr.dispatch("nope", {"text": "x"})
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_failed_worker_start_tears_down_others(tmp_path):
    """If any worker fails to start, the others are stopped before raising."""
    good = tmp_path / "good.py"
    good.write_text(ECHO_SCRIPT)
    bad = tmp_path / "bad.py"
    bad.write_text("import sys; sys.exit(1)")

    mgr = ProfileWorkerManager()
    with pytest.raises(Exception):
        await mgr.start(
            [
                WorkerSpec(name="good", argv=[sys.executable, str(good)], env={}),
                WorkerSpec(name="bad", argv=[sys.executable, str(bad)], env={}),
            ]
        )
    # Manager state must be empty; no leaked workers.
    assert mgr.names == []

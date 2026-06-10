"""Tests for the worker-side IPC platform adapter."""

from __future__ import annotations

import io
import json

import pytest

from gateway.config import PlatformConfig


@pytest.mark.asyncio
async def test_ipc_adapter_round_trip():
    """One inbound event -> handler returns reply -> reply emitted to stdout."""
    from gateway.platforms.ipc import IPCPlatformAdapter

    inbound = (
        json.dumps(
            {
                "kind": "message_event",
                "correlation_id": "corr-1",
                "event": {
                    "text": "hello",
                    "message_type": "text",
                    "source": {
                        "platform": "whatsapp",
                        "chat_id": "60123@s.whatsapp.net",
                        "chat_type": "dm",
                        "user_id": "60123",
                        "user_name": "Alice",
                        "message_id": "m1",
                    },
                    "message_id": "m1",
                    "media_urls": [],
                    "media_types": [],
                    "internal": False,
                    "canonical_sender_id": "60123",
                    "timestamp": "2026-05-07T12:00:00",
                },
            }
        )
        + "\n"
    )
    stdin = io.StringIO(inbound)
    stdout = io.StringIO()

    cfg = PlatformConfig(extra={})
    adapter = IPCPlatformAdapter(cfg, stdin=stdin, stdout=stdout)

    async def fake_handler(event):
        assert event.text == "hello"
        assert event.canonical_sender_id == "60123"
        return "world"

    adapter.set_message_handler(fake_handler)
    await adapter.connect()
    await adapter.wait_until_disconnected()

    out_lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(out_lines) == 1
    parsed = json.loads(out_lines[0])
    assert parsed["kind"] == "reply"
    assert parsed["correlation_id"] == "corr-1"
    assert parsed["reply"]["text"] == "world"
    assert parsed["reply"]["error"] is None


@pytest.mark.asyncio
async def test_ipc_adapter_handler_exception_emits_error_envelope():
    from gateway.platforms.ipc import IPCPlatformAdapter

    inbound = (
        json.dumps(
            {
                "kind": "message_event",
                "correlation_id": "corr-2",
                "event": {
                    "text": "boom",
                    "message_type": "text",
                    "source": None,
                    "media_urls": [],
                    "media_types": [],
                    "internal": False,
                    "timestamp": "2026-05-07T12:00:00",
                },
            }
        )
        + "\n"
    )
    stdin = io.StringIO(inbound)
    stdout = io.StringIO()

    cfg = PlatformConfig(extra={})
    adapter = IPCPlatformAdapter(cfg, stdin=stdin, stdout=stdout)

    async def crashing_handler(event):
        raise RuntimeError("kaboom")

    adapter.set_message_handler(crashing_handler)
    await adapter.connect()
    await adapter.wait_until_disconnected()

    out_lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(out_lines) == 1
    parsed = json.loads(out_lines[0])
    assert parsed["correlation_id"] == "corr-2"
    assert parsed["reply"]["text"] is None
    assert "kaboom" in (parsed["reply"]["error"] or "")


@pytest.mark.asyncio
async def test_ipc_adapter_handler_returning_none_emits_null_text():
    from gateway.platforms.ipc import IPCPlatformAdapter

    inbound = (
        json.dumps(
            {
                "kind": "message_event",
                "correlation_id": "corr-3",
                "event": {
                    "text": "skip",
                    "message_type": "text",
                    "source": None,
                    "media_urls": [],
                    "media_types": [],
                    "internal": False,
                    "timestamp": "2026-05-07T12:00:00",
                },
            }
        )
        + "\n"
    )
    stdin = io.StringIO(inbound)
    stdout = io.StringIO()

    cfg = PlatformConfig(extra={})
    adapter = IPCPlatformAdapter(cfg, stdin=stdin, stdout=stdout)

    async def silent(event):
        return None

    adapter.set_message_handler(silent)
    await adapter.connect()
    await adapter.wait_until_disconnected()

    out_lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(out_lines) == 1
    parsed = json.loads(out_lines[0])
    assert parsed["correlation_id"] == "corr-3"
    assert parsed["reply"]["text"] is None
    assert parsed["reply"]["error"] is None


@pytest.mark.asyncio
async def test_ipc_adapter_drops_malformed_json():
    from gateway.platforms.ipc import IPCPlatformAdapter

    stdin = io.StringIO("{not json}\n")
    stdout = io.StringIO()
    cfg = PlatformConfig(extra={})
    adapter = IPCPlatformAdapter(cfg, stdin=stdin, stdout=stdout)
    async def silent(event):
        return None

    adapter.set_message_handler(silent)

    await adapter.connect()
    await adapter.wait_until_disconnected()
    # No reply emitted for malformed lines.
    out_lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert out_lines == []

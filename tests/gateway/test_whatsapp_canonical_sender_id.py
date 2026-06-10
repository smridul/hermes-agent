"""Test that the WhatsApp adapter populates canonical_sender_id on MessageEvent.

This is the first step of WhatsApp sender-based profile routing: routing
needs a stable, alias-collapsed sender identity attached to the event so
the router can do a single dict lookup at message-receive time.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_build_message_event_attaches_canonical_sender_id(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import PlatformConfig
    from gateway.platforms.whatsapp import WhatsAppAdapter

    cfg = PlatformConfig(extra={})
    adapter = WhatsAppAdapter(cfg)

    raw = {
        "senderId": "60123456789@s.whatsapp.net",
        "chatId": "60123456789@s.whatsapp.net",
        "isGroup": False,
        "messageId": "msg-1",
        "body": "hello",
    }
    event = await adapter._build_message_event(raw)

    assert event is not None
    assert getattr(event, "canonical_sender_id", None) == "60123456789"


@pytest.mark.asyncio
async def test_build_message_event_canonical_sender_id_strips_lid_form(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import PlatformConfig
    from gateway.platforms.whatsapp import WhatsAppAdapter

    cfg = PlatformConfig(extra={})
    adapter = WhatsAppAdapter(cfg)

    raw = {
        "senderId": "999999999999@lid",
        "chatId": "999999999999@lid",
        "isGroup": False,
        "messageId": "msg-2",
        "body": "hi",
    }
    event = await adapter._build_message_event(raw)

    # No lid-mapping files exist under tmp_path, so canonical degrades
    # gracefully to the bare normalized identifier.
    assert event is not None
    assert event.canonical_sender_id == "999999999999"

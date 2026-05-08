"""Tests that _handle_message routes inbound WhatsApp messages to workers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.profile_routing_config import ProfileRoutingConfig
from gateway.session import SessionSource


def _whatsapp_event(canonical_id: str, text: str = "hi") -> MessageEvent:
    src = SessionSource(
        platform=Platform.WHATSAPP,
        chat_id=f"{canonical_id}@s.whatsapp.net",
        chat_type="dm",
        user_id=canonical_id,
        user_name="Anon",
        message_id="m1",
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=src,
        message_id="m1",
        canonical_sender_id=canonical_id,
    )


@pytest.mark.asyncio
async def test_routed_message_dispatches_to_worker(tmp_path, monkeypatch):
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60123": "main", "60987": "family"},
    )
    runner = GatewayRunner(cfg)

    # Stub out the worker manager so we don't need a real subprocess.
    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(
        return_value={"text": "reply-from-family", "error": None}
    )
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_event("60987", "hi family")
    result = await runner._handle_message(event)

    # The routing branch returns None (already delivered via adapter.send).
    assert result is None
    mock_mgr.dispatch.assert_awaited_once()
    call = mock_mgr.dispatch.await_args
    assert call.args[0] == "family"
    mock_wa_adapter.send.assert_awaited_once()
    args, kwargs = mock_wa_adapter.send.await_args
    sent_text = args[1] if len(args) > 1 else kwargs.get("content", "")
    assert "reply-from-family" in sent_text


@pytest.mark.asyncio
async def test_primary_routed_message_does_not_dispatch_to_worker(tmp_path, monkeypatch):
    """An unmapped sender resolves to default (==primary) and falls through."""
    main_home = tmp_path / "profiles" / "main"
    main_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main",),
        default_profile="main",
        sender_profile_map={},
    )
    runner = GatewayRunner(cfg)
    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock()
    runner.profile_worker_manager = mock_mgr

    event = _whatsapp_event("60111", "hi main")
    # The full in-process path may explode without all the deps wired,
    # but we only care that the routing branch did NOT call dispatch.
    try:
        await runner._handle_message(event)
    except Exception:
        pass
    mock_mgr.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_returning_error_envelope_is_logged_and_dropped(tmp_path, monkeypatch):
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60987": "family"},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(
        return_value={"text": None, "error": "kaboom in family"}
    )
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_event("60987", "hi family")
    result = await runner._handle_message(event)
    assert result is None
    mock_mgr.dispatch.assert_awaited_once()
    # Error envelope: no reply sent.
    mock_wa_adapter.send.assert_not_awaited()

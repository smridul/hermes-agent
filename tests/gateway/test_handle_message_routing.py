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


def _whatsapp_group_event(
    chat_id: str,
    canonical_sender_id: str,
    text: str = "@bot hi",
) -> MessageEvent:
    src = SessionSource(
        platform=Platform.WHATSAPP,
        chat_id=chat_id,
        chat_type="group",
        user_id=canonical_sender_id,
        user_name="GroupMember",
        message_id="m1",
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=src,
        message_id="m1",
        canonical_sender_id=canonical_sender_id,
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


@pytest.mark.asyncio
async def test_group_mapped_message_dispatches_to_group_target(tmp_path, monkeypatch):
    """A group with a group_profile_map entry routes to that profile."""
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
        sender_profile_map={},
        group_profile_map={"g1@g.us": "family"},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(return_value={"text": "from-family", "error": None})
    mock_mgr.has_worker = MagicMock(return_value=True)
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_group_event("g1@g.us", "60111")
    result = await runner._handle_message(event)

    assert result is None
    mock_mgr.dispatch.assert_awaited_once()
    assert mock_mgr.dispatch.await_args.args[0] == "family"


@pytest.mark.asyncio
async def test_group_binding_beats_sender_mapping(tmp_path, monkeypatch):
    """When a group is mapped, sender_profile_map is ignored even if it would match."""
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    test_home = tmp_path / "profiles" / "test_profile"
    for d in (main_home, family_home, test_home):
        d.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family", "test_profile"),
        default_profile="main",
        sender_profile_map={"60123": "family"},
        group_profile_map={"g1@g.us": "test_profile"},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(return_value={"text": "from-test", "error": None})
    mock_mgr.has_worker = MagicMock(return_value=True)
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    # sender 60123 is mapped to family, but group g1 is mapped to test_profile.
    event = _whatsapp_group_event("g1@g.us", "60123")
    await runner._handle_message(event)

    mock_mgr.dispatch.assert_awaited_once()
    assert mock_mgr.dispatch.await_args.args[0] == "test_profile"


@pytest.mark.asyncio
async def test_group_mapped_drops_when_worker_unavailable(tmp_path, monkeypatch, caplog):
    """If the bound worker is missing, the message is dropped — no fallback."""
    main_home = tmp_path / "profiles" / "main"
    main_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60123": "main"},  # would have matched if we fell through
        group_profile_map={"g1@g.us": "family"},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock()
    mock_mgr.has_worker = MagicMock(return_value=False)  # family worker missing
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_group_event("g1@g.us", "60123")
    with caplog.at_level("ERROR"):
        result = await runner._handle_message(event)

    assert result is None
    mock_mgr.dispatch.assert_not_awaited()           # no dispatch
    mock_wa_adapter.send.assert_not_awaited()        # no reply
    assert any(
        "group_routing" in rec.message and "worker_unavailable" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_unmapped_group_falls_through_to_sender_routing(tmp_path, monkeypatch):
    """A group not in group_profile_map uses the existing sender path."""
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
        sender_profile_map={"60123": "family"},
        group_profile_map={"g1@g.us": "family"},  # different group than the event
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(return_value={"text": "ok", "error": None})
    mock_mgr.has_worker = MagicMock(return_value=True)
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_group_event("g2@g.us", "60123")  # g2 not mapped, sender is
    await runner._handle_message(event)

    mock_mgr.dispatch.assert_awaited_once()
    assert mock_mgr.dispatch.await_args.args[0] == "family"


@pytest.mark.asyncio
async def test_dm_ignores_group_profile_map(tmp_path, monkeypatch):
    """DMs never consult group_profile_map even if the chat_id string would match."""
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
        sender_profile_map={"60123": "family"},
        # Pathological: group_profile_map keyed on the same string as the DM chat_id.
        # The dispatcher must still treat this as a DM and use sender routing.
        group_profile_map={"60123@s.whatsapp.net": "main"},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(return_value={"text": "ok", "error": None})
    mock_mgr.has_worker = MagicMock(return_value=True)
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_event("60123", "hi")  # DM; chat_type="dm"
    await runner._handle_message(event)

    mock_mgr.dispatch.assert_awaited_once()
    # Sender routing wins — "family", not "main" from the spurious group entry.
    assert mock_mgr.dispatch.await_args.args[0] == "family"

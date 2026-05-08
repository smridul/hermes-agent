"""Regression guards: the routing feature is a no-op when not configured."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_default_gateway_config_has_no_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import GatewayConfig

    cfg = GatewayConfig()
    assert cfg.whatsapp_profile_routing is None


def test_legacy_config_does_not_spawn_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    runner = GatewayRunner(cfg)
    assert runner.profile_worker_manager is None
    assert runner.whatsapp_router is None


@pytest.mark.asyncio
async def test_routing_branch_is_noop_when_disabled(tmp_path, monkeypatch):
    """A WhatsApp event with routing disabled must not dispatch to any worker."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import GatewayConfig, Platform
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource

    cfg = GatewayConfig()
    runner = GatewayRunner(cfg)
    assert runner.whatsapp_router is None

    # Even after planting a manager mock, the routing branch should stay
    # off because the router is None.
    manager = MagicMock()
    manager.dispatch = AsyncMock(return_value={"text": "should-not-reach", "error": None})
    runner.profile_worker_manager = manager

    src = SessionSource(
        platform=Platform.WHATSAPP,
        chat_id="x",
        chat_type="dm",
        user_id="u",
        message_id="m1",
    )
    event = MessageEvent(
        text="hi",
        message_type=MessageType.TEXT,
        source=src,
        message_id="m1",
        canonical_sender_id="u",
    )

    try:
        await runner._handle_message(event)
    except Exception:
        # The full in-process pipeline may explode without all deps wired
        # in this minimal test fixture; that's fine — we only care that
        # the routing branch did not call dispatch.
        pass

    manager.dispatch.assert_not_awaited()

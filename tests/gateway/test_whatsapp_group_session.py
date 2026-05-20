import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SendResult
from gateway.platforms.whatsapp import WhatsAppAdapter


def _make_adapter(*, group_session_window=True, group_session_minutes=15,
                  require_mention=True, extra_overrides=None):
    """Build a WhatsAppAdapter with only the attributes the gate logic needs.

    Mirrors the object.__new__ pattern in test_whatsapp_group_gating.py so we
    do not start a real bridge. `send` is mocked so notice messages are
    captured instead of hitting the network.
    """
    extra = {
        "require_mention": require_mention,
        "group_session_window": group_session_window,
        "group_session_minutes": group_session_minutes,
    }
    if extra_overrides:
        extra.update(extra_overrides)
    adapter = object.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = PlatformConfig(enabled=True, extra=extra)
    adapter._dm_policy = "open"
    adapter._allow_from = set()
    adapter._group_policy = "open"
    adapter._group_allow_from = set()
    adapter._mention_patterns = adapter._compile_mention_patterns()
    adapter._group_session_window = adapter._whatsapp_group_session_enabled()
    adapter._group_session_minutes = adapter._whatsapp_group_session_minutes()
    adapter._group_session_manager = None
    adapter.send = AsyncMock()
    return adapter


def _group_message(body="hello", **overrides):
    data = {
        "isGroup": True,
        "body": body,
        "chatId": "120363001234567890@g.us",
        "mentionedIds": [],
        "botIds": ["15551230000@s.whatsapp.net", "15551230000@lid"],
        "quotedParticipant": "",
    }
    data.update(overrides)
    return data


def _mention_message(body="hey", **overrides):
    overrides.setdefault("mentionedIds", ["15551230000@s.whatsapp.net"])
    return _group_message(body, **overrides)


def _bare_adapter(extra):
    adapter = object.__new__(WhatsAppAdapter)
    adapter.config = PlatformConfig(enabled=True, extra=extra)
    return adapter


# --- Task 3: config readers ---

def test_group_session_enabled_reads_config_true():
    assert _bare_adapter({"group_session_window": True})._whatsapp_group_session_enabled() is True


def test_group_session_enabled_reads_config_string():
    assert _bare_adapter({"group_session_window": "on"})._whatsapp_group_session_enabled() is True


def test_group_session_enabled_reads_config_false():
    assert _bare_adapter({"group_session_window": False})._whatsapp_group_session_enabled() is False


def test_group_session_enabled_reads_config_string_false():
    assert _bare_adapter({"group_session_window": "false"})._whatsapp_group_session_enabled() is False


def test_group_session_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WHATSAPP_GROUP_SESSION_WINDOW", raising=False)
    assert _bare_adapter({})._whatsapp_group_session_enabled() is False


def test_group_session_minutes_defaults_to_15(monkeypatch):
    monkeypatch.delenv("WHATSAPP_GROUP_SESSION_MINUTES", raising=False)
    assert _bare_adapter({})._whatsapp_group_session_minutes() == 15


def test_group_session_minutes_reads_config():
    assert _bare_adapter({"group_session_minutes": 30})._whatsapp_group_session_minutes() == 30


def test_group_session_minutes_rejects_invalid():
    assert _bare_adapter({"group_session_minutes": "abc"})._whatsapp_group_session_minutes() == 15


def test_group_session_minutes_rejects_non_positive():
    assert _bare_adapter({"group_session_minutes": 0})._whatsapp_group_session_minutes() == 15


def test_group_session_minutes_rejects_negative():
    assert _bare_adapter({"group_session_minutes": -5})._whatsapp_group_session_minutes() == 15


# --- Task 4: message classification ---

def test_classify_plain_message_returns_none():
    adapter = _make_adapter()
    assert adapter._classify_group_control(_group_message("hello there")) is None


def test_classify_mention_returns_wake():
    adapter = _make_adapter()
    assert adapter._classify_group_control(_mention_message("hey what's up")) == "wake"


def test_classify_mention_plus_sleep_returns_sleep():
    adapter = _make_adapter()
    msg = _mention_message("@15551230000 sleep")
    assert adapter._classify_group_control(msg) == "sleep"


def test_classify_mention_plus_sleep_word_in_sentence_returns_wake():
    adapter = _make_adapter()
    msg = _mention_message("@15551230000 should i go to sleep early tonight")
    assert adapter._classify_group_control(msg) == "wake"


def test_classify_bare_mention_returns_wake():
    adapter = _make_adapter()
    # Body is only the mention tag; _clean_bot_mention_text returns the
    # original when stripping would empty it — must still be a wake, not sleep.
    msg = _mention_message("@15551230000")
    assert adapter._classify_group_control(msg) == "wake"


def test_wake_notice_mentions_window_length():
    adapter = _make_adapter(group_session_minutes=20)
    notice = adapter._group_session_wake_notice()
    assert "20" in notice


# --- Task 5: _group_session_decision ---

CHAT_ID = "120363001234567890@g.us"


@pytest.mark.asyncio
async def test_decision_wake_opens_window_and_sends_notice():
    adapter = _make_adapter()
    decision = await adapter._group_session_decision(_mention_message("hey"))
    assert decision == "process"
    adapter.send.assert_awaited_once()
    assert adapter._group_session_manager.is_awake(CHAT_ID) is True
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_decision_plain_message_while_open_is_processed():
    adapter = _make_adapter()
    await adapter._group_session_decision(_mention_message("hi"))
    decision = await adapter._group_session_decision(_group_message("just chatting"))
    assert decision == "process"
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_decision_plain_message_while_closed_is_classic():
    adapter = _make_adapter()
    decision = await adapter._group_session_decision(_group_message("just chatting"))
    assert decision == "classic"
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_decision_sleep_closes_window_and_swallows():
    adapter = _make_adapter()
    await adapter._group_session_decision(_mention_message("hi"))
    decision = await adapter._group_session_decision(_mention_message("@15551230000 sleep"))
    assert decision == "swallow"
    assert adapter._group_session_manager.is_awake(CHAT_ID) is False
    assert adapter.send.await_count == 2
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_decision_sleep_while_closed_swallows_without_notice():
    adapter = _make_adapter()
    decision = await adapter._group_session_decision(_mention_message("@15551230000 sleep"))
    assert decision == "swallow"
    adapter.send.assert_not_awaited()
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_decision_re_mention_does_not_resend_wake_notice():
    adapter = _make_adapter()
    await adapter._group_session_decision(_mention_message("hi"))
    await adapter._group_session_decision(_mention_message("still here"))
    assert adapter.send.await_count == 1
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_decision_handles_missing_chat_id():
    # chatId absent → chat_id coerces to "" and the decision still works.
    adapter = _make_adapter()
    decision = await adapter._group_session_decision(_mention_message("hey", chatId=None))
    assert decision == "process"
    adapter._group_session_manager.shutdown()


# --- Task 6: _passes_inbound_gate integration ---

def _dm_message(body="hello"):
    return {
        "isGroup": False,
        "body": body,
        "senderId": "6281234567890@s.whatsapp.net",
        "from": "6281234567890@s.whatsapp.net",
        "botIds": [],
        "mentionedIds": [],
    }


@pytest.mark.asyncio
async def test_gate_dm_unaffected():
    adapter = _make_adapter()
    assert await adapter._passes_inbound_gate(_dm_message("hello")) is True


@pytest.mark.asyncio
async def test_gate_plain_group_message_blocked_when_closed():
    adapter = _make_adapter()
    assert await adapter._passes_inbound_gate(_group_message("hello")) is False
    # the gate lazily created the manager even though the window stays closed
    assert adapter._group_session_manager is not None
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_gate_mention_opens_window_then_plain_passes():
    adapter = _make_adapter()
    assert await adapter._passes_inbound_gate(_mention_message("hey")) is True
    assert await adapter._passes_inbound_gate(_group_message("untagged followup")) is True
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_gate_sleep_command_is_blocked_and_closes_window():
    adapter = _make_adapter()
    await adapter._passes_inbound_gate(_mention_message("hey"))
    assert await adapter._passes_inbound_gate(_mention_message("@15551230000 sleep")) is False
    assert await adapter._passes_inbound_gate(_group_message("untagged after sleep")) is False
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_gate_feature_off_uses_classic_behavior():
    adapter = _make_adapter(group_session_window=False)
    # mention still triggers a response under the classic gate
    assert await adapter._passes_inbound_gate(_mention_message("hey")) is True
    # plain message stays blocked, and no window is created
    assert await adapter._passes_inbound_gate(_group_message("plain")) is False
    assert adapter._group_session_manager is None


@pytest.mark.asyncio
async def test_gate_disallowed_group_blocked_before_session_logic():
    adapter = _make_adapter()
    adapter._group_policy = "allowlist"
    adapter._group_allow_from = {"999999999999@g.us"}
    assert await adapter._passes_inbound_gate(_mention_message("hey")) is False
    assert adapter._group_session_manager is None


# --- Final review: non-trigger rules + notice-failure logging ---

@pytest.mark.asyncio
async def test_gate_reply_to_bot_passes_classic_without_opening_window():
    # Replying to the bot triggers a one-off response via the classic gate
    # but must NOT open an awake window.
    adapter = _make_adapter()
    msg = _group_message("here is my answer", quotedParticipant="15551230000@lid")
    assert await adapter._passes_inbound_gate(msg) is True
    assert adapter._group_session_manager.is_awake(CHAT_ID) is False
    # a following untagged message stays blocked — proof no window opened
    assert await adapter._passes_inbound_gate(_group_message("untagged")) is False
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_gate_mention_pattern_passes_classic_without_opening_window():
    # A custom mention-pattern match triggers a one-off response via the
    # classic gate but must NOT open an awake window.
    adapter = _make_adapter(extra_overrides={"mention_patterns": [r"^\s*chompy\b"]})
    assert await adapter._passes_inbound_gate(_group_message("chompy status")) is True
    assert adapter._group_session_manager.is_awake(CHAT_ID) is False
    assert await adapter._passes_inbound_gate(_group_message("untagged")) is False
    adapter._group_session_manager.shutdown()


@pytest.mark.asyncio
async def test_failed_notice_send_logs_a_warning(caplog):
    adapter = _make_adapter()
    adapter.send = AsyncMock(return_value=SendResult(success=False, error="bridge down"))
    with caplog.at_level(logging.WARNING):
        await adapter._group_session_decision(_mention_message("hey"))
    assert any("notice failed" in r.getMessage() for r in caplog.records)
    if adapter._group_session_manager is not None:
        adapter._group_session_manager.shutdown()

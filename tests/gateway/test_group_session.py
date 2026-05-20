import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway.platforms.group_session import (
    SLEEP_KEYWORDS,
    GroupSessionManager,
    is_sleep_command,
)


def test_exact_sleep_keyword_is_a_command():
    assert is_sleep_command("sleep") is True
    assert is_sleep_command("stop") is True
    assert is_sleep_command("go to sleep") is True


def test_case_and_whitespace_are_normalized():
    assert is_sleep_command("  SLEEP  ") is True
    assert is_sleep_command("Go   To   Sleep") is True
    assert is_sleep_command("Stop Listening") is True


def test_sleep_word_inside_a_sentence_is_not_a_command():
    assert is_sleep_command("i need to sleep now please") is False
    assert is_sleep_command("are you going to sleep") is False
    assert is_sleep_command("stop being annoying") is False


def test_empty_text_is_not_a_command():
    assert is_sleep_command("") is False
    assert is_sleep_command("   ") is False


def test_sleep_keyword_set_contains_expected_keywords():
    assert SLEEP_KEYWORDS == frozenset({
        "sleep", "stop", "go to sleep",
        "sleep now", "stop listening", "quiet",
    })


class FakeClock:
    """A manually-advanced monotonic clock for deterministic tests."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t


@pytest.mark.asyncio
async def test_open_returns_true_then_reset_returns_false():
    mgr = GroupSessionManager(window_seconds=900, on_expire=AsyncMock(), now_fn=FakeClock())
    try:
        assert mgr.open_or_reset("g1@g.us") is True
        assert mgr.open_or_reset("g1@g.us") is False
    finally:
        mgr.shutdown()


@pytest.mark.asyncio
async def test_is_awake_reflects_clock():
    clock = FakeClock(1000.0)
    mgr = GroupSessionManager(window_seconds=900, on_expire=AsyncMock(), now_fn=clock)
    try:
        mgr.open_or_reset("g1@g.us")
        assert mgr.is_awake("g1@g.us") is True
        clock.t = 1000.0 + 900 + 1
        assert mgr.is_awake("g1@g.us") is False
    finally:
        mgr.shutdown()


def test_is_awake_false_for_unknown_group():
    mgr = GroupSessionManager(window_seconds=900, on_expire=AsyncMock(), now_fn=FakeClock())
    assert mgr.is_awake("never@g.us") is False


@pytest.mark.asyncio
async def test_close_stops_awake_window():
    mgr = GroupSessionManager(window_seconds=900, on_expire=AsyncMock(), now_fn=FakeClock())
    mgr.open_or_reset("g1@g.us")
    mgr.close("g1@g.us")
    assert mgr.is_awake("g1@g.us") is False


@pytest.mark.asyncio
async def test_expiry_callback_fires_and_clears_window():
    on_expire = AsyncMock()
    mgr = GroupSessionManager(window_seconds=0.02, on_expire=on_expire)
    mgr.open_or_reset("g1@g.us")
    await asyncio.sleep(0.08)
    on_expire.assert_awaited_once_with("g1@g.us")
    assert mgr.is_awake("g1@g.us") is False


@pytest.mark.asyncio
async def test_close_prevents_expiry_callback():
    on_expire = AsyncMock()
    mgr = GroupSessionManager(window_seconds=0.05, on_expire=on_expire)
    mgr.open_or_reset("g1@g.us")
    mgr.close("g1@g.us")
    await asyncio.sleep(0.12)
    on_expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_reopen_after_natural_expiry_returns_true():
    mgr = GroupSessionManager(window_seconds=0.02, on_expire=AsyncMock())
    assert mgr.open_or_reset("g1@g.us") is True
    await asyncio.sleep(0.08)
    assert mgr.is_awake("g1@g.us") is False
    assert mgr.open_or_reset("g1@g.us") is True
    mgr.shutdown()

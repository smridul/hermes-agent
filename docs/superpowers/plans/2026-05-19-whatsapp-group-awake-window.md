# WhatsApp Group Awake-Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In a WhatsApp group, one @-mention of the agent opens a 15-minute window during which it answers every message untagged; re-mentioning resets the window and `@agent sleep` closes it early.

**Architecture:** A new `GroupSessionManager` (in `gateway/platforms/group_session.py`) holds an in-memory per-group `awake_until` timestamp plus an asyncio expiry task. The WhatsApp adapter classifies each inbound group message as wake / sleep / neither, drives the manager, and posts deterministic wake/sleep/expiry notices. A new `_passes_inbound_gate` wraps the existing classic gate (`_should_process_message`) without modifying it, so behavior is unchanged when the feature is off.

**Tech Stack:** Python 3.13, asyncio, pytest + pytest-asyncio (strict mode — async tests need `@pytest.mark.asyncio`). Tests run via `scripts/run_tests.sh`.

---

## Spec

See `docs/superpowers/specs/2026-05-19-whatsapp-group-awake-window-design.md`.

## File Structure

- **Create** `gateway/platforms/group_session.py` — `SLEEP_KEYWORDS`, `is_sleep_command()`, `GroupSession` dataclass, `GroupSessionManager` class. Pure timer/state module; no WhatsApp-data-shape knowledge. Stdlib-only imports (no circular-import risk).
- **Modify** `gateway/platforms/whatsapp.py` — config readers, message classification, the awake-window gate. The classic `_should_process_message` is left untouched.
- **Create** `tests/gateway/test_group_session.py` — unit tests for the new module.
- **Create** `tests/gateway/test_whatsapp_group_session.py` — adapter wiring + gate integration tests.

---

## Task 1: Sleep-command keyword matcher

**Files:**
- Create: `gateway/platforms/group_session.py`
- Test: `tests/gateway/test_group_session.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_group_session.py`:

```python
from gateway.platforms.group_session import SLEEP_KEYWORDS, is_sleep_command


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


def test_sleep_keywords_set_is_non_empty():
    assert "sleep" in SLEEP_KEYWORDS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run_tests.sh tests/gateway/test_group_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gateway.platforms.group_session'`

- [ ] **Step 3: Write the minimal implementation**

Create `gateway/platforms/group_session.py`:

```python
"""Per-group awake-window state for the WhatsApp adapter.

An @-mention of the agent in a group "opens" a timed awake window during
which every message is processed without requiring a tag. Re-mentioning
resets the window; a sleep command closes it early. State is in-memory and
ephemeral — a gateway restart clears all windows.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

# Exact (whole-message) sleep commands, matched case-insensitively against
# the mention-stripped message body. Exact match avoids accidental shutdowns
# from sentences that merely contain the word "sleep".
SLEEP_KEYWORDS: frozenset[str] = frozenset({
    "sleep",
    "stop",
    "go to sleep",
    "sleep now",
    "stop listening",
    "quiet",
})


def is_sleep_command(text: str) -> bool:
    """Return True if `text` is exactly a sleep command.

    Lower-cases, strips, and collapses internal whitespace before comparing
    against SLEEP_KEYWORDS.
    """
    if not text:
        return False
    normalized = " ".join(text.lower().split())
    return normalized in SLEEP_KEYWORDS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `scripts/run_tests.sh tests/gateway/test_group_session.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add gateway/platforms/group_session.py tests/gateway/test_group_session.py
git commit -m "feat(whatsapp): sleep-command keyword matcher for group sessions"
```

---

## Task 2: GroupSessionManager

**Files:**
- Modify: `gateway/platforms/group_session.py`
- Test: `tests/gateway/test_group_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/gateway/test_group_session.py`:

```python
import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway.platforms.group_session import GroupSessionManager


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


@pytest.mark.asyncio
async def test_is_awake_false_for_unknown_group():
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run_tests.sh tests/gateway/test_group_session.py -v`
Expected: FAIL — `ImportError: cannot import name 'GroupSessionManager'`

- [ ] **Step 3: Write the minimal implementation**

Append to `gateway/platforms/group_session.py`:

```python
@dataclass
class GroupSession:
    """A single group's open awake window."""
    awake_until: float
    expiry_task: Optional[asyncio.Task]


class GroupSessionManager:
    """Tracks open awake windows per group chat and fires expiry callbacks.

    Args:
        window_seconds: how long a window stays open after the last mention.
        on_expire: async callback invoked with the chat_id when a window
            expires naturally (NOT when it is closed early via `close`).
        now_fn: monotonic clock; injectable for tests.
    """

    def __init__(
        self,
        window_seconds: float,
        on_expire: Callable[[str], Awaitable[None]],
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_seconds = window_seconds
        self._on_expire = on_expire
        self._now = now_fn
        self._sessions: Dict[str, GroupSession] = {}

    def is_awake(self, chat_id: str) -> bool:
        session = self._sessions.get(chat_id)
        return session is not None and self._now() < session.awake_until

    def open_or_reset(self, chat_id: str) -> bool:
        """Open a window for `chat_id`, or reset an already-open one.

        Returns True if the window was newly opened, False if an existing
        open window was reset. Always (re)schedules the expiry task.
        """
        existing = self._sessions.get(chat_id)
        newly_opened = existing is None
        if existing is not None and existing.expiry_task is not None:
            existing.expiry_task.cancel()
        awake_until = self._now() + self._window_seconds
        task = asyncio.create_task(self._run_expiry(chat_id))
        self._sessions[chat_id] = GroupSession(
            awake_until=awake_until, expiry_task=task,
        )
        return newly_opened

    def close(self, chat_id: str) -> None:
        """Close a window early. Does NOT fire the on_expire callback."""
        session = self._sessions.pop(chat_id, None)
        if session is not None and session.expiry_task is not None:
            session.expiry_task.cancel()

    def shutdown(self) -> None:
        """Cancel every pending expiry task. For clean teardown / tests."""
        for session in self._sessions.values():
            if session.expiry_task is not None:
                session.expiry_task.cancel()
        self._sessions.clear()

    async def _run_expiry(self, chat_id: str) -> None:
        try:
            await asyncio.sleep(self._window_seconds)
        except asyncio.CancelledError:
            return
        self._sessions.pop(chat_id, None)
        await self._on_expire(chat_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `scripts/run_tests.sh tests/gateway/test_group_session.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add gateway/platforms/group_session.py tests/gateway/test_group_session.py
git commit -m "feat(whatsapp): GroupSessionManager — timed per-group awake windows"
```

---

## Task 3: Adapter config readers + `__init__` attributes

**Files:**
- Modify: `gateway/platforms/whatsapp.py`
- Test: `tests/gateway/test_whatsapp_group_session.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_whatsapp_group_session.py`:

```python
import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_group_session.py -v`
Expected: FAIL — `AttributeError: 'WhatsAppAdapter' object has no attribute '_whatsapp_group_session_enabled'`

- [ ] **Step 3: Write the minimal implementation**

In `gateway/platforms/whatsapp.py`, add the import. Find the existing line:

```python
from gateway.whatsapp_identity import canonical_whatsapp_identifier
```

Add immediately after it:

```python
from gateway.platforms.group_session import GroupSessionManager, is_sleep_command
```

In `WhatsAppAdapter.__init__`, find the end of the constructor — the line:

```python
        self._shutting_down: bool = False
```

Add immediately after it:

```python

        # Group awake-window: an @-mention opens a timed window during which
        # every group message is processed untagged. See group_session.py.
        self._group_session_window: bool = self._whatsapp_group_session_enabled()
        self._group_session_minutes: int = self._whatsapp_group_session_minutes()
        self._group_session_manager: Optional[GroupSessionManager] = None
```

Add the two reader methods. Find the end of `_whatsapp_free_response_chats` — the line:

```python
        return {part.strip() for part in str(raw).split(",") if part.strip()}
```

Add immediately after it (before `@staticmethod` / `_coerce_allow_list`):

```python

    def _whatsapp_group_session_enabled(self) -> bool:
        configured = self.config.extra.get("group_session_window")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in ("true", "1", "yes", "on")
            return bool(configured)
        return os.getenv("WHATSAPP_GROUP_SESSION_WINDOW", "false").lower() in ("true", "1", "yes", "on")

    def _whatsapp_group_session_minutes(self) -> int:
        configured = self.config.extra.get("group_session_minutes")
        if configured is None:
            configured = os.getenv("WHATSAPP_GROUP_SESSION_MINUTES", "15")
        try:
            minutes = int(configured)
        except (TypeError, ValueError):
            return 15
        return minutes if minutes > 0 else 15
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_group_session.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add gateway/platforms/whatsapp.py tests/gateway/test_whatsapp_group_session.py
git commit -m "feat(whatsapp): group-session config readers + adapter state"
```

---

## Task 4: Message classification + notice texts

**Files:**
- Modify: `gateway/platforms/whatsapp.py`
- Test: `tests/gateway/test_whatsapp_group_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/gateway/test_whatsapp_group_session.py`:

```python
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


def test_wake_notice_mentions_window_length():
    adapter = _make_adapter(group_session_minutes=20)
    notice = adapter._group_session_wake_notice()
    assert "20" in notice
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_group_session.py -v`
Expected: FAIL — `AttributeError: 'WhatsAppAdapter' object has no attribute '_classify_group_control'`

- [ ] **Step 3: Write the minimal implementation**

In `gateway/platforms/whatsapp.py`, add the notice constants as class attributes. Find the `__init__` method definition line:

```python
    def __init__(self, config: PlatformConfig):
```

Add immediately *before* it (class-body level, same indentation as `def __init__`):

```python
    _GROUP_SESSION_SLEEP_NOTICE = (
        "😴 Going quiet. Tag me again whenever you want me back."
    )
    _GROUP_SESSION_EXPIRY_NOTICE = (
        "⌛ I've stopped following the chat. Tag me to talk again."
    )

```

Add the classification + wake-notice methods. Find the end of `_should_process_message` — the line:

```python
        return self._message_matches_mention_patterns(data)
```

Add immediately after it:

```python

    def _group_session_wake_notice(self) -> str:
        return (
            f"👂 I'm following this chat for the next "
            f"{self._group_session_minutes} minutes — no need to tag me. "
            f"Tag me with \"sleep\" to stop early."
        )

    def _classify_group_control(self, data: Dict[str, Any]) -> Optional[str]:
        """Classify a group message as an awake-window control message.

        Returns "wake" if the message @-mentions the agent, "sleep" if it
        @-mentions the agent and its mention-stripped body is exactly a
        sleep command, or None if it is neither.
        """
        if not self._message_mentions_bot(data):
            return None
        body = str(data.get("body") or "")
        cleaned = self._clean_bot_mention_text(body, data)
        if is_sleep_command(cleaned):
            return "sleep"
        return "wake"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_group_session.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add gateway/platforms/whatsapp.py tests/gateway/test_whatsapp_group_session.py
git commit -m "feat(whatsapp): classify group wake/sleep control messages"
```

---

## Task 5: Awake-window decision logic

**Files:**
- Modify: `gateway/platforms/whatsapp.py`
- Test: `tests/gateway/test_whatsapp_group_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/gateway/test_whatsapp_group_session.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_group_session.py -v`
Expected: FAIL — `AttributeError: 'WhatsAppAdapter' object has no attribute '_group_session_decision'`

- [ ] **Step 3: Write the minimal implementation**

In `gateway/platforms/whatsapp.py`, add three methods. Find the end of the `_classify_group_control` method added in Task 4 — the line:

```python
        return "wake"
```

Add immediately after it:

```python

    def _ensure_group_session_manager(self) -> GroupSessionManager:
        if self._group_session_manager is None:
            self._group_session_manager = GroupSessionManager(
                window_seconds=self._group_session_minutes * 60,
                on_expire=self._on_group_session_expire,
            )
        return self._group_session_manager

    async def _on_group_session_expire(self, chat_id: str) -> None:
        await self.send(chat_id, self._GROUP_SESSION_EXPIRY_NOTICE)

    async def _group_session_decision(self, data: Dict[str, Any]) -> str:
        """Drive the awake-window for one inbound group message.

        Returns one of:
          "process" — the awake window is open (or just opened); process it.
          "swallow" — this was a sleep command; do not process it.
          "classic" — no window involvement; defer to _should_process_message.
        """
        manager = self._ensure_group_session_manager()
        chat_id = str(data.get("chatId") or "")
        control = self._classify_group_control(data)
        if control == "sleep":
            if manager.is_awake(chat_id):
                manager.close(chat_id)
                await self.send(chat_id, self._GROUP_SESSION_SLEEP_NOTICE)
            return "swallow"
        if control == "wake":
            newly_opened = manager.open_or_reset(chat_id)
            if newly_opened:
                await self.send(chat_id, self._group_session_wake_notice())
            return "process"
        if manager.is_awake(chat_id):
            return "process"
        return "classic"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_group_session.py -v`
Expected: PASS — 18 passed

- [ ] **Step 5: Commit**

```bash
git add gateway/platforms/whatsapp.py tests/gateway/test_whatsapp_group_session.py
git commit -m "feat(whatsapp): group awake-window decision logic"
```

---

## Task 6: Wire the gate into the inbound pipeline

**Files:**
- Modify: `gateway/platforms/whatsapp.py`
- Test: `tests/gateway/test_whatsapp_group_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/gateway/test_whatsapp_group_session.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_group_session.py -v`
Expected: FAIL — `AttributeError: 'WhatsAppAdapter' object has no attribute '_passes_inbound_gate'`

- [ ] **Step 3: Write the minimal implementation**

In `gateway/platforms/whatsapp.py`, add `_passes_inbound_gate`. Find the end of the `_group_session_decision` method added in Task 5 — the line:

```python
        return "classic"
```

Add immediately after it:

```python

    async def _passes_inbound_gate(self, data: Dict[str, Any]) -> bool:
        """Decide whether an inbound message should be processed.

        Wraps the classic _should_process_message gate with the group
        awake-window. DMs and the feature-off path are unchanged.
        """
        if not data.get("isGroup", False):
            return self._should_process_message(data)
        chat_id = str(data.get("chatId") or "")
        if not self._is_group_allowed(chat_id):
            return False
        if self._group_session_window:
            decision = await self._group_session_decision(data)
            if decision == "swallow":
                return False
            if decision == "process":
                return True
            # decision == "classic" — fall through to the classic gate
        return self._should_process_message(data)
```

Now wire it into `_build_message_event`. Find this exact block (near the top of `_build_message_event`):

```python
        try:
            if not self._should_process_message(data):
                return None
```

Replace it with:

```python
        try:
            if not await self._passes_inbound_gate(data):
                return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_group_session.py -v`
Expected: PASS — 24 passed

- [ ] **Step 5: Commit**

```bash
git add gateway/platforms/whatsapp.py tests/gateway/test_whatsapp_group_session.py
git commit -m "feat(whatsapp): route inbound group messages through the awake-window gate"
```

---

## Task 7: Regression verification

**Files:** none changed — verification only.

- [ ] **Step 1: Run the new + adjacent WhatsApp test suites**

Run:
```bash
scripts/run_tests.sh tests/gateway/test_group_session.py tests/gateway/test_whatsapp_group_session.py tests/gateway/test_whatsapp_group_gating.py -v
```
Expected: PASS — all tests in the three files pass (Task 1–6 added 35 new tests; `test_whatsapp_group_gating.py` is unchanged and must stay green, confirming the classic gate is untouched).

- [ ] **Step 2: Run the full gateway test directory to catch regressions**

Run: `scripts/run_tests.sh tests/gateway/`
Expected: PASS, or only failures that also reproduce on the base commit. If any test fails, confirm it is pre-existing by running it on the branch point: `git stash && scripts/run_tests.sh tests/gateway/<failing_file> ; git stash pop`. Do not claim success if a failure was introduced by this change.

- [ ] **Step 3: Update HANDOFF.md**

Replace the top of `HANDOFF.md` with a new "Completed (2026-05-19): WhatsApp group awake-window" section: summarize the feature, the new `gateway/platforms/group_session.py` module, the `group_session_window` / `group_session_minutes` config keys (default off / 15), and the deploy step (source-only commit + push + Coolify rebuild of `eureka-hermes`; the user must set `group_session_window: true` in their group's profile `config.yaml` to activate it).

- [ ] **Step 4: Commit**

```bash
git add HANDOFF.md
git commit -m "docs(handoff): WhatsApp group awake-window feature complete"
```

---

## Self-Review Notes

- **Spec coverage:** Open / Reset / Sleep / Expiry / Closed-window all map to Task 5 (`_group_session_decision`) and Task 2 (`GroupSessionManager`). Wake/sleep/expiry notices → Task 4 constants + Task 5 sends. Config keys (`group_session_window` default off, `group_session_minutes` default 15) → Task 3. Classic-gate-unchanged guarantee → Task 6 (`_should_process_message` untouched) verified by Task 7 Step 1.
- **Non-triggers:** reply-to-bot and mention-patterns are reached only via `_should_process_message` on the "classic" branch and never call `open_or_reset`, so they cannot open a window — matches the spec.
- **In-memory / restart:** no persistence; `_group_session_manager` is recreated lazily per process. Matches spec's "Rejected alternatives".
- **Type consistency:** `_group_session_decision` returns the literal strings `"process"`/`"swallow"`/`"classic"`, consumed identically in `_passes_inbound_gate`. `open_or_reset` returns `bool` (newly-opened), consumed as `newly_opened`. `is_sleep_command`/`is_awake` return `bool`.
- **Out of scope (per spec):** restart persistence, configurable keywords/notice templates, per-user windows, reply-to-bot as a window opener. Adapter `disconnect()` is not wired to `GroupSessionManager.shutdown()` — expiry tasks die with the process; `shutdown()` exists for test teardown.

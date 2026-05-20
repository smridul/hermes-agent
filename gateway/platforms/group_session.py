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
    # str.split() with no arg collapses all whitespace; whitespace-only -> ""
    normalized = " ".join(text.lower().split())
    return normalized in SLEEP_KEYWORDS


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

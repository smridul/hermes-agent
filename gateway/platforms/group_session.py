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

"""JSON codec for :class:`MessageEvent` across the IPC boundary.

Only fields safe to cross a process boundary are encoded.  ``raw_message``
holds platform-specific objects (e.g. PTB Update wrappers) that are not
JSON-serialisable and must not leave the ingress process.

The codec is intentionally explicit (no ``dataclasses.asdict``): we want
forward-compatible encoding where workers running an older version of the
codec gracefully ignore unknown fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


def encode_event(event: MessageEvent) -> dict[str, Any]:
    """Encode a :class:`MessageEvent` to a JSON-safe dict."""
    return {
        "text": event.text,
        "message_type": (
            event.message_type.value if event.message_type else "text"
        ),
        "source": event.source.to_dict() if event.source else None,
        "message_id": event.message_id,
        "platform_update_id": event.platform_update_id,
        "media_urls": list(event.media_urls or []),
        "media_types": list(event.media_types or []),
        "reply_to_message_id": event.reply_to_message_id,
        "reply_to_text": event.reply_to_text,
        "auto_skill": event.auto_skill,
        "channel_prompt": event.channel_prompt,
        "internal": bool(event.internal),
        "canonical_sender_id": event.canonical_sender_id,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
    }


def decode_event(data: dict[str, Any]) -> MessageEvent:
    """Decode a JSON-safe dict back into a :class:`MessageEvent`.

    Unknown fields are ignored.  ``raw_message`` is always ``None`` —
    the original platform object cannot cross the IPC boundary.
    """
    src_data = data.get("source")
    src: SessionSource | None = None
    if src_data:
        src = SessionSource.from_dict(src_data)

    mtype_value = data.get("message_type") or "text"
    timestamp_str = data.get("timestamp")
    timestamp = (
        datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now()
    )

    return MessageEvent(
        text=data.get("text", ""),
        message_type=MessageType(mtype_value),
        source=src,
        raw_message=None,  # intentionally dropped
        message_id=data.get("message_id"),
        platform_update_id=data.get("platform_update_id"),
        media_urls=list(data.get("media_urls") or []),
        media_types=list(data.get("media_types") or []),
        reply_to_message_id=data.get("reply_to_message_id"),
        reply_to_text=data.get("reply_to_text"),
        auto_skill=data.get("auto_skill"),
        channel_prompt=data.get("channel_prompt"),
        internal=bool(data.get("internal", False)),
        canonical_sender_id=data.get("canonical_sender_id"),
        timestamp=timestamp,
    )

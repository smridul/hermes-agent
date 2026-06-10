"""Round-trip tests for MessageEvent JSON codec used across IPC."""

from __future__ import annotations

from datetime import datetime

import pytest

from gateway.config import Platform
from gateway.message_event_codec import decode_event, encode_event
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


def _sample_event() -> MessageEvent:
    src = SessionSource(
        platform=Platform.WHATSAPP,
        chat_id="60123456789@s.whatsapp.net",
        chat_type="dm",
        user_id="60123456789",
        user_name="Alice",
        message_id="msg-1",
    )
    return MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=src,
        message_id="msg-1",
        media_urls=["/tmp/foo.jpg"],
        media_types=["image/jpeg"],
        canonical_sender_id="60123456789",
        timestamp=datetime(2026, 5, 7, 12, 0, 0),
    )


def test_encode_decode_round_trip():
    original = _sample_event()
    encoded = encode_event(original)
    assert isinstance(encoded, dict)
    decoded = decode_event(encoded)

    assert decoded.text == original.text
    assert decoded.message_type == original.message_type
    assert decoded.source is not None
    assert decoded.source.platform == original.source.platform
    assert decoded.source.chat_id == original.source.chat_id
    assert decoded.source.user_id == original.source.user_id
    assert decoded.source.user_name == original.source.user_name
    assert decoded.canonical_sender_id == original.canonical_sender_id
    assert decoded.media_urls == original.media_urls
    assert decoded.media_types == original.media_types
    assert decoded.timestamp == original.timestamp
    assert decoded.message_id == original.message_id


def test_decode_strips_raw_message_field():
    """raw_message holds platform-specific objects that must NOT cross IPC."""
    e = _sample_event()
    e.raw_message = {"unparseable": object()}
    decoded = decode_event(encode_event(e))
    assert decoded.raw_message is None


def test_encode_with_no_source():
    e = MessageEvent(text="hi", source=None)
    encoded = encode_event(e)
    decoded = decode_event(encoded)
    assert decoded.text == "hi"
    assert decoded.source is None


def test_encoded_is_pure_json():
    """The encoded dict must be JSON-serialisable end-to-end."""
    import json

    e = _sample_event()
    encoded = encode_event(e)
    s = json.dumps(encoded)
    parsed = json.loads(s)
    decoded = decode_event(parsed)
    assert decoded.text == e.text
    assert decoded.canonical_sender_id == e.canonical_sender_id

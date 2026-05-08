"""Tests for the WhatsApp sender->profile router."""

from __future__ import annotations

from gateway.profile_routing_config import ProfileRoutingConfig
from gateway.whatsapp_router import WhatsAppRouter


def _cfg() -> ProfileRoutingConfig:
    return ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={
            "60123456789": "main",
            "60987654321": "family",
        },
    )


def test_mapped_sender_resolves_to_profile():
    r = WhatsAppRouter(_cfg())
    assert r.resolve_profile("60123456789") == "main"
    assert r.resolve_profile("60987654321") == "family"


def test_unmapped_sender_resolves_to_default():
    r = WhatsAppRouter(_cfg())
    assert r.resolve_profile("60111111111") == "main"


def test_router_does_not_renormalize_input():
    """The router does NOT canonicalise input.

    Callers (the adapter) must pass the canonical id stored on the event
    so we keep canonicalisation in one well-known place.
    """
    r = WhatsAppRouter(_cfg())
    # Raw JID would not match the canonical map keys.
    assert r.resolve_profile("60123456789@s.whatsapp.net") == "main"  # falls back to default


def test_empty_sender_resolves_to_default():
    r = WhatsAppRouter(_cfg())
    assert r.resolve_profile("") == "main"

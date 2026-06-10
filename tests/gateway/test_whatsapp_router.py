"""Unit tests for WhatsAppRouter."""

from __future__ import annotations

from gateway.profile_routing_config import ProfileRoutingConfig
from gateway.whatsapp_router import WhatsAppRouter


def _cfg(**overrides) -> ProfileRoutingConfig:
    base = dict(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60123": "family"},
        group_profile_map={"g1@g.us": "family"},
    )
    base.update(overrides)
    return ProfileRoutingConfig(**base)


def test_resolve_group_returns_mapped_profile():
    router = WhatsAppRouter(_cfg())
    assert router.resolve_group("g1@g.us") == "family"


def test_resolve_group_returns_none_for_unmapped_chat():
    router = WhatsAppRouter(_cfg())
    assert router.resolve_group("g2@g.us") is None


def test_resolve_group_returns_none_when_map_empty():
    router = WhatsAppRouter(_cfg(group_profile_map={}))
    assert router.resolve_group("g1@g.us") is None


def test_resolve_profile_unaffected_by_group_map():
    """Existing sender lookup stays exactly as before."""
    router = WhatsAppRouter(_cfg())
    assert router.resolve_profile("60123") == "family"
    assert router.resolve_profile("99999") == "main"  # default fallback

"""Tests for the profile_routing config schema parser."""

from __future__ import annotations

import pytest

from gateway.profile_routing_config import (
    ProfileRoutingConfig,
    ProfileRoutingConfigError,
    parse_profile_routing,
)


def test_absent_block_returns_none():
    assert parse_profile_routing(None) is None
    assert parse_profile_routing({}) is None


def test_minimal_valid_config():
    cfg = parse_profile_routing(
        {
            "profiles": ["main", "family"],
            "default_profile": "main",
            "sender_profile_map": {
                "+60123456789": "main",
                "60987654321@lid": "family",
            },
        }
    )
    assert isinstance(cfg, ProfileRoutingConfig)
    assert cfg.default_profile == "main"
    assert cfg.profiles == ("main", "family")
    # Keys MUST be canonicalized to the bare numeric identifier.
    assert cfg.sender_profile_map["60123456789"] == "main"
    assert cfg.sender_profile_map["60987654321"] == "family"


def test_default_profile_must_be_in_profiles():
    with pytest.raises(ProfileRoutingConfigError, match="default_profile"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "family",
                "sender_profile_map": {},
            }
        )


def test_unknown_profile_in_map():
    with pytest.raises(ProfileRoutingConfigError, match="unknown profile"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {"+60123456789": "stranger"},
            }
        )


def test_duplicate_canonicalized_sender():
    with pytest.raises(ProfileRoutingConfigError, match="duplicate"):
        parse_profile_routing(
            {
                "profiles": ["main", "family"],
                "default_profile": "main",
                "sender_profile_map": {
                    "+60123456789": "main",
                    "60123456789@s.whatsapp.net": "family",
                },
            }
        )


def test_unmapped_sender_behavior_only_default_supported():
    with pytest.raises(ProfileRoutingConfigError, match="not yet supported"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "unmapped_sender_behavior": "deny",
            }
        )


def test_profiles_must_be_non_empty_list_of_strings():
    with pytest.raises(ProfileRoutingConfigError, match="profiles"):
        parse_profile_routing(
            {
                "profiles": [],
                "default_profile": "main",
                "sender_profile_map": {},
            }
        )


def test_sender_map_must_be_mapping():
    with pytest.raises(ProfileRoutingConfigError, match="must be a mapping"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": ["not", "a", "dict"],
            }
        )

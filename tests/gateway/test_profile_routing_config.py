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


def test_dataclass_has_group_profile_map_default_empty():
    """ProfileRoutingConfig accepts group_profile_map and defaults to empty dict."""
    cfg = ProfileRoutingConfig(
        profiles=("main",),
        default_profile="main",
        sender_profile_map={},
    )
    assert cfg.group_profile_map == {}


def test_dataclass_accepts_group_profile_map():
    cfg = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={},
        group_profile_map={"120363409860032836@g.us": "family"},
    )
    assert cfg.group_profile_map == {"120363409860032836@g.us": "family"}


def test_parser_populates_group_profile_map():
    cfg = parse_profile_routing(
        {
            "profiles": ["main", "family"],
            "default_profile": "main",
            "sender_profile_map": {},
            "group_profile_map": {
                "120363409860032836@g.us": "family",
                "120363409999999999@g.us": "main",
            },
        }
    )
    assert cfg is not None
    assert cfg.group_profile_map == {
        "120363409860032836@g.us": "family",
        "120363409999999999@g.us": "main",
    }


def test_parser_group_profile_map_optional():
    """A config without group_profile_map parses with an empty group_profile_map."""
    cfg = parse_profile_routing(
        {
            "profiles": ["main"],
            "default_profile": "main",
            "sender_profile_map": {},
        }
    )
    assert cfg is not None
    assert cfg.group_profile_map == {}


def test_parser_allows_multiple_groups_to_same_profile():
    cfg = parse_profile_routing(
        {
            "profiles": ["main", "family"],
            "default_profile": "main",
            "sender_profile_map": {},
            "group_profile_map": {
                "g1@g.us": "family",
                "g2@g.us": "family",
            },
        }
    )
    assert cfg is not None
    assert cfg.group_profile_map == {"g1@g.us": "family", "g2@g.us": "family"}


def test_group_profile_map_unknown_target_raises():
    with pytest.raises(ProfileRoutingConfigError, match="unknown profile"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": {"g1@g.us": "ghost"},
            }
        )


def test_group_profile_map_non_string_key_raises():
    with pytest.raises(ProfileRoutingConfigError, match="must be strings"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": {123: "main"},
            }
        )


def test_group_profile_map_non_string_value_raises():
    with pytest.raises(ProfileRoutingConfigError, match="must be strings"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": {"g1@g.us": 42},
            }
        )


def test_group_profile_map_empty_key_raises():
    with pytest.raises(ProfileRoutingConfigError, match="empty"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": {"   ": "main"},
            }
        )


def test_group_profile_map_not_a_dict_raises():
    with pytest.raises(ProfileRoutingConfigError, match="must be a mapping"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": ["g1@g.us", "main"],
            }
        )

"""Tests that GatewayRunner boots routing infrastructure correctly."""

from __future__ import annotations

import pytest

from gateway.profile_routing_config import ProfileRoutingConfig, ProfileRoutingConfigError


def test_no_routing_config_means_no_worker_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    assert cfg.whatsapp_profile_routing is None

    runner = GatewayRunner(cfg)
    assert runner.profile_worker_manager is None
    assert runner.whatsapp_router is None


def test_routing_config_creates_manager_and_router(tmp_path, monkeypatch):
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60123": "main", "60987": "family"},
    )
    runner = GatewayRunner(cfg)
    assert runner.profile_worker_manager is not None
    assert runner.whatsapp_router is not None
    assert runner.primary_profile_name == "main"


def test_load_gateway_config_parses_profile_routing_from_yaml(tmp_path, monkeypatch):
    """Regression: load_gateway_config must NOT crash with NameError on
    'config' when profile_routing is present in the yaml file. Bug fixed
    by parsing routing into a function-scoped local before the
    GatewayConfig object is constructed.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        """
whatsapp:
  profile_routing:
    profiles: ["default", "family"]
    default_profile: "default"
    sender_profile_map:
      "+15551234567": "default"
      "+15557654321": "family"
"""
    )
    from gateway.config import load_gateway_config

    cfg = load_gateway_config()
    assert cfg.whatsapp_profile_routing is not None
    assert cfg.whatsapp_profile_routing.default_profile == "default"
    assert "family" in cfg.whatsapp_profile_routing.profiles
    assert cfg.whatsapp_profile_routing.sender_profile_map["15557654321"] == "family"


def test_load_gateway_config_no_routing_block_works(tmp_path, monkeypatch):
    """A config.yaml without profile_routing must load cleanly."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("whatsapp: {}\n")
    from gateway.config import load_gateway_config

    cfg = load_gateway_config()
    assert cfg.whatsapp_profile_routing is None


def test_invalid_profile_routing_fails_closed(tmp_path, monkeypatch):
    """A malformed ``whatsapp.profile_routing`` block must abort gateway boot.

    Regression: a sender_profile_map entry pointing at a profile not in
    the ``profiles`` list used to be swallowed by the broad except in
    load_gateway_config, silently disabling routing — every restricted
    sender then fell through to the in-process default profile, leaking
    its tools/MCP servers. Routing is a security boundary; fail closed.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        """
whatsapp:
  profile_routing:
    profiles: ["default", "main"]
    default_profile: "default"
    sender_profile_map:
      "+15551234567": "ghost-profile"
"""
    )
    from gateway.config import load_gateway_config

    with pytest.raises(ProfileRoutingConfigError):
        load_gateway_config()


def test_primary_profile_name_falls_back_when_home_is_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    runner = GatewayRunner(cfg)
    # HERMES_HOME points at root, not a profile dir, so primary defaults to "default".
    assert runner.primary_profile_name == "default"


def test_invalid_group_profile_map_fails_closed(tmp_path, monkeypatch):
    """A malformed ``group_profile_map`` block must abort gateway boot.

    Mirrors test_invalid_profile_routing_fails_closed: the existing
    fail-closed pass-through in gateway/config.py:979 catches
    ProfileRoutingConfigError raised by either map.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        """
whatsapp:
  profile_routing:
    profiles: ["default", "main"]
    default_profile: "default"
    group_profile_map:
      "g1@g.us": "ghost-profile"
"""
    )
    from gateway.config import load_gateway_config

    with pytest.raises(ProfileRoutingConfigError):
        load_gateway_config()


def test_valid_group_profile_map_loads_from_yaml(tmp_path, monkeypatch):
    """A valid group_profile_map block survives load_gateway_config."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        """
whatsapp:
  profile_routing:
    profiles: ["default", "family"]
    default_profile: "default"
    group_profile_map:
      "120363409860032836@g.us": "family"
"""
    )
    from gateway.config import load_gateway_config

    cfg = load_gateway_config()
    assert cfg.whatsapp_profile_routing is not None
    assert cfg.whatsapp_profile_routing.group_profile_map == {
        "120363409860032836@g.us": "family"
    }

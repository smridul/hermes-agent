"""Tests that GatewayRunner boots routing infrastructure correctly."""

from __future__ import annotations

import pytest

from gateway.profile_routing_config import ProfileRoutingConfig


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


def test_primary_profile_name_falls_back_when_home_is_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    runner = GatewayRunner(cfg)
    # HERMES_HOME points at root, not a profile dir, so primary defaults to "default".
    assert runner.primary_profile_name == "default"

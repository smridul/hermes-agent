"""Pure routing function: canonical WhatsApp sender id -> target profile name."""

from __future__ import annotations

from gateway.profile_routing_config import ProfileRoutingConfig


class WhatsAppRouter:
    """Resolves a target profile name from a canonicalised sender identifier.

    The router does NOT canonicalise input — callers must pass the same
    canonical form that was applied to the map keys at config-load time
    (see :func:`gateway.profile_routing_config.parse_profile_routing`).
    Keeping canonicalisation in one well-known place (the WhatsApp adapter)
    avoids drift between load-time and message-time normalisation.
    """

    def __init__(self, config: ProfileRoutingConfig) -> None:
        self._map = config.sender_profile_map
        self._default = config.default_profile

    def resolve_profile(self, canonical_sender_id: str) -> str:
        return self._map.get(canonical_sender_id, self._default)

"""Pure routing function: WhatsApp identity -> target profile name."""

from __future__ import annotations

from typing import Optional

from gateway.profile_routing_config import ProfileRoutingConfig


class WhatsAppRouter:
    """Resolves a target profile name from canonical WhatsApp identifiers.

    The router does NOT canonicalise input — callers must pass the same
    canonical form that was applied to the map keys at config-load time
    (see :func:`gateway.profile_routing_config.parse_profile_routing`).
    Keeping canonicalisation in one well-known place (the WhatsApp adapter)
    avoids drift between load-time and message-time normalisation.

    Two lookups are exposed:

    * :meth:`resolve_profile` — sender-based, falls back to ``default_profile``.
    * :meth:`resolve_group` — group-chat-based, returns ``None`` when the chat
      is not bound to any profile. Group routing is exclusive: callers MUST
      treat a non-``None`` result as the only legitimate target and drop the
      message rather than fall back to sender routing or the default profile.
    """

    def __init__(self, config: ProfileRoutingConfig) -> None:
        self._map = config.sender_profile_map
        self._default = config.default_profile
        self._group_map = config.group_profile_map

    def resolve_profile(self, canonical_sender_id: str) -> str:
        return self._map.get(canonical_sender_id, self._default)

    def resolve_group(self, chat_id: str) -> Optional[str]:
        return self._group_map.get(chat_id)

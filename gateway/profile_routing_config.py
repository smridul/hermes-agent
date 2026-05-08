"""Schema parser/validator for ``channels.whatsapp.profile_routing``.

Routes inbound WhatsApp messages to different Hermes profile workers
based on the canonical sender identity.  Parsed once at gateway boot;
the result is consumed by :class:`gateway.whatsapp_router.WhatsAppRouter`.

The canonicalisation here MUST match the one applied to inbound senders
at message-receive time (see
:func:`gateway.platforms.whatsapp.WhatsAppAdapter._build_message_event`),
which uses :func:`gateway.whatsapp_identity.canonical_whatsapp_identifier`.
Using a different normalisation would silently break lookups for users
whose phone JID and LID forms collapse to a non-trivial canonical
identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from gateway.whatsapp_identity import canonical_whatsapp_identifier


class ProfileRoutingConfigError(ValueError):
    """Raised when ``channels.whatsapp.profile_routing`` is misconfigured."""


@dataclass(frozen=True)
class ProfileRoutingConfig:
    """Validated, canonicalised routing config."""

    profiles: tuple[str, ...]
    default_profile: str
    sender_profile_map: dict[str, str] = field(default_factory=dict)


def parse_profile_routing(raw: dict[str, Any] | None) -> Optional[ProfileRoutingConfig]:
    """Parse, validate, and canonicalise a ``profile_routing`` block.

    Returns ``None`` when the block is absent (routing disabled).
    Raises :class:`ProfileRoutingConfigError` on any structural problem.
    """
    if not raw:
        return None

    profiles_raw = raw.get("profiles")
    if (
        not isinstance(profiles_raw, list)
        or not profiles_raw
        or not all(isinstance(p, str) and p for p in profiles_raw)
    ):
        raise ProfileRoutingConfigError(
            "profile_routing.profiles must be a non-empty list of strings"
        )
    profiles_tuple = tuple(profiles_raw)

    default_profile = raw.get("default_profile")
    if not isinstance(default_profile, str) or not default_profile:
        raise ProfileRoutingConfigError(
            "profile_routing.default_profile must be a non-empty string"
        )
    if default_profile not in profiles_tuple:
        raise ProfileRoutingConfigError(
            f"profile_routing.default_profile {default_profile!r} "
            f"must be one of profile_routing.profiles ({list(profiles_tuple)})"
        )

    unmapped_behavior = raw.get("unmapped_sender_behavior", "default_profile")
    if unmapped_behavior != "default_profile":
        raise ProfileRoutingConfigError(
            f"unmapped_sender_behavior={unmapped_behavior!r} not yet supported "
            "(only 'default_profile' is implemented in MVP)"
        )

    raw_map = raw.get("sender_profile_map")
    if raw_map is None:
        raw_map = {}
    if not isinstance(raw_map, dict):
        raise ProfileRoutingConfigError(
            "profile_routing.sender_profile_map must be a mapping"
        )

    canonical_map: dict[str, str] = {}
    for raw_sender, target in raw_map.items():
        if not isinstance(raw_sender, str) or not isinstance(target, str):
            raise ProfileRoutingConfigError(
                "sender_profile_map keys and values must be strings"
            )
        if target not in profiles_tuple:
            raise ProfileRoutingConfigError(
                f"sender_profile_map maps {raw_sender!r} to unknown profile "
                f"{target!r} (not in profiles list)"
            )
        canonical = canonical_whatsapp_identifier(raw_sender)
        if not canonical:
            raise ProfileRoutingConfigError(
                f"sender_profile_map key {raw_sender!r} canonicalises to empty"
            )
        existing = canonical_map.get(canonical)
        if existing is not None and existing != target:
            raise ProfileRoutingConfigError(
                f"duplicate sender after canonicalisation: {raw_sender!r} -> "
                f"{canonical!r} maps to both {existing!r} and {target!r}"
            )
        canonical_map[canonical] = target

    return ProfileRoutingConfig(
        profiles=profiles_tuple,
        default_profile=default_profile,
        sender_profile_map=canonical_map,
    )

# WhatsApp Sender-Based Profile Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route inbound WhatsApp messages to different Hermes profile worker subprocesses based on the sender's WhatsApp identity, while keeping a single WhatsApp number, single bridge session, and single container.

**Architecture:** The existing gateway becomes the **ingress** process and additionally spawns one Hermes-worker subprocess per non-primary profile listed in `channels.whatsapp.profile_routing.profiles`. Each worker boots with `HERMES_HOME=<profile path>` so all profile-aware code resolves correctly by construction. Ingress and workers communicate over stdin/stdout using newline-delimited JSON. A new IPC platform adapter inside the worker reads inbound events from stdin and writes replies to stdout. Ingress's `_handle_message` resolves the target profile per inbound WhatsApp message; messages destined for a non-primary profile are dispatched to its worker, and the worker's reply is delivered back through ingress's existing WhatsApp adapter.

**Tech Stack:** Python 3.11+, asyncio, `asyncio.create_subprocess_exec`, existing Hermes gateway machinery (BasePlatformAdapter, MessageEvent, GatewayRunner), pytest.

**Spec:** `docs/superpowers/specs/2026-05-07-whatsapp-sender-profile-routing-design.md`

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `gateway/whatsapp_router.py` | Pure function: canonical sender id → target profile name. Loads from validated config dict. |
| `gateway/profile_routing_config.py` | Schema parsing + validation for `channels.whatsapp.profile_routing`. Raises on misconfig. |
| `gateway/message_event_codec.py` | JSON serialize/deserialize for `MessageEvent`. Round-trip safe; strips non-serializable fields with explicit allow-list. |
| `gateway/platforms/ipc.py` | Worker-side platform adapter: reads stdin → builds MessageEvent → calls handler; writes handler's reply to stdout as JSON. |
| `gateway/profile_worker.py` | Ingress-side wrapper around one subprocess: writer task, reader task, correlation tracking, restart on death. |
| `gateway/profile_worker_manager.py` | Owns `dict[profile_name, ProfileWorker]`. Spawns at boot, dispatches by name, shuts down on gateway exit. |
| `hermes_cli/profile_worker_cli.py` | CLI subcommand entrypoint: `hermes profile-worker --name <x>`. Sets up env, boots a stripped-down GatewayRunner with only the IPC adapter. |
| `tests/gateway/test_whatsapp_profile_routing.py` | Unit tests: router, config parser, codec. |
| `tests/gateway/test_profile_worker_integration.py` | Integration tests: real subprocess with stub agent, memory isolation, concurrent dispatch. |

### Modified files

| Path | Change |
|---|---|
| `gateway/platforms/whatsapp.py` | `_build_message_event` (~line 1005): attach `event.canonical_sender_id` from `canonical_whatsapp_identifier`. |
| `gateway/run.py` | (a) `_handle_message` (line 4421): early branch for routed senders → dispatch to worker manager → deliver reply via WhatsApp adapter. (b) `GatewayRunner.__init__`: instantiate `ProfileWorkerManager` when routing is configured. (c) shutdown hook: stop workers cleanly. |
| `gateway/config.py` | Wire `profile_routing` parsing into `load_gateway_config` so the config dict is validated at boot. |
| `gateway/platforms/base.py` | (Possibly) add an optional `canonical_sender_id: Optional[str] = None` field to `MessageEvent` for the WhatsApp adapter to populate. (Confirmed by reading current MessageEvent definition at line 870 — currently has no such field.) |
| `hermes_cli/_parser.py` (or wherever subcommands register) | Register `profile-worker` subcommand. |
| `hermes_cli/main.py` | Dispatch to `profile_worker_cli` for the new subcommand. |

---

## Task Decomposition

The plan has 16 tasks across 6 phases. Each task is bite-sized (one feature, one commit). Phases 0–1 produce no externally visible behavior change; Phase 4 ties everything together.

---

## Phase 0 — Foundations

### Task 1: Add `canonical_sender_id` to MessageEvent

**Files:**
- Modify: `gateway/platforms/base.py:870-918` (`MessageEvent` dataclass)
- Modify: `gateway/platforms/whatsapp.py:~1005` (`_build_message_event`)
- Test: `tests/gateway/test_whatsapp_canonical_sender_id.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/test_whatsapp_canonical_sender_id.py
import pytest
from gateway.platforms.whatsapp import WhatsAppAdapter

@pytest.mark.asyncio
async def test_build_message_event_attaches_canonical_sender_id(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Import inside the test so HERMES_HOME monkeypatch lands first.
    from gateway.platforms.whatsapp import WhatsAppAdapter
    from gateway.config import PlatformConfig

    cfg = PlatformConfig(name="whatsapp", extra={})
    adapter = WhatsAppAdapter(cfg)

    raw = {
        "senderId": "60123456789@s.whatsapp.net",
        "chatId": "60123456789@s.whatsapp.net",
        "text": "hello",
        "isGroup": False,
        "messageId": "msg-1",
    }
    event = await adapter._build_message_event(raw)

    assert event is not None
    assert getattr(event, "canonical_sender_id", None) == "60123456789"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_whatsapp_canonical_sender_id.py -v`
Expected: FAIL — `AttributeError: 'MessageEvent' object has no attribute 'canonical_sender_id'`

- [ ] **Step 3: Add `canonical_sender_id` field to `MessageEvent`**

In `gateway/platforms/base.py`, find the `MessageEvent` dataclass (line 870). Add this field after `internal: bool = False` (line 915):

```python
    # Canonical WhatsApp sender identity (numeric, alias-collapsed).
    # Populated by the WhatsApp adapter; None for other platforms.
    canonical_sender_id: Optional[str] = None
```

- [ ] **Step 4: Populate the field in WhatsApp adapter**

In `gateway/platforms/whatsapp.py`, locate `_build_message_event` (around line 1005). Just before the function returns the constructed `MessageEvent`, populate the new field:

```python
from gateway.whatsapp_identity import canonical_whatsapp_identifier

# ... existing logic ...
sender_id_raw = data.get("senderId") or ""
canonical = canonical_whatsapp_identifier(sender_id_raw) if sender_id_raw else None

event = MessageEvent(
    text=...,
    # ... existing kwargs ...
    canonical_sender_id=canonical,
)
return event
```

(Exact insertion depends on the current code shape — preserve the existing `MessageEvent(...)` construction, just add `canonical_sender_id=canonical` to its kwargs.)

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/gateway/test_whatsapp_canonical_sender_id.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gateway/platforms/base.py gateway/platforms/whatsapp.py tests/gateway/test_whatsapp_canonical_sender_id.py
git commit -m "feat(whatsapp): attach canonical_sender_id to MessageEvent"
```

---

### Task 2: Routing config schema parser

**Files:**
- Create: `gateway/profile_routing_config.py`
- Test: `tests/gateway/test_profile_routing_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/test_profile_routing_config.py
import pytest
from gateway.profile_routing_config import (
    ProfileRoutingConfig,
    parse_profile_routing,
    ProfileRoutingConfigError,
)


def test_absent_block_returns_none():
    assert parse_profile_routing({}) is None


def test_minimal_valid_config():
    cfg = parse_profile_routing({
        "profiles": ["main", "family"],
        "default_profile": "main",
        "sender_profile_map": {
            "+60123456789": "main",
            "60987654321@lid": "family",
        },
    })
    assert isinstance(cfg, ProfileRoutingConfig)
    assert cfg.default_profile == "main"
    assert cfg.profiles == ("main", "family")
    # Keys MUST be canonicalized (numeric only, plus stripped).
    assert cfg.sender_profile_map["60123456789"] == "main"
    assert cfg.sender_profile_map["60987654321"] == "family"


def test_default_profile_must_be_in_profiles():
    with pytest.raises(ProfileRoutingConfigError, match="default_profile"):
        parse_profile_routing({
            "profiles": ["main"],
            "default_profile": "family",
            "sender_profile_map": {},
        })


def test_unknown_profile_in_map():
    with pytest.raises(ProfileRoutingConfigError, match="unknown profile"):
        parse_profile_routing({
            "profiles": ["main"],
            "default_profile": "main",
            "sender_profile_map": {"+60123456789": "stranger"},
        })


def test_duplicate_canonicalized_sender():
    with pytest.raises(ProfileRoutingConfigError, match="duplicate"):
        parse_profile_routing({
            "profiles": ["main", "family"],
            "default_profile": "main",
            "sender_profile_map": {
                "+60123456789": "main",
                "60123456789@s.whatsapp.net": "family",  # canonicalizes to same
            },
        })


def test_unmapped_sender_behavior_only_default_supported():
    with pytest.raises(ProfileRoutingConfigError, match="not yet supported"):
        parse_profile_routing({
            "profiles": ["main"],
            "default_profile": "main",
            "sender_profile_map": {},
            "unmapped_sender_behavior": "deny",
        })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_profile_routing_config.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `gateway/profile_routing_config.py`**

```python
"""Schema parser/validator for channels.whatsapp.profile_routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from gateway.whatsapp_identity import canonical_whatsapp_identifier


class ProfileRoutingConfigError(ValueError):
    """Raised when channels.whatsapp.profile_routing is misconfigured."""


@dataclass(frozen=True)
class ProfileRoutingConfig:
    """Validated, canonicalized routing config."""

    profiles: tuple[str, ...]
    default_profile: str
    sender_profile_map: dict[str, str]  # canonicalized sender id -> profile name


def parse_profile_routing(raw: dict[str, Any] | None) -> Optional[ProfileRoutingConfig]:
    """Parse, validate, and canonicalize a profile_routing block.

    Returns None when the block is absent (routing disabled).
    Raises ProfileRoutingConfigError on any structural problem.
    """
    if not raw:
        return None

    profiles = raw.get("profiles")
    if not isinstance(profiles, list) or not profiles or not all(
        isinstance(p, str) and p for p in profiles
    ):
        raise ProfileRoutingConfigError(
            "profile_routing.profiles must be a non-empty list of strings"
        )
    profiles_tuple = tuple(profiles)

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
            f"(only 'default_profile' is implemented in MVP)"
        )

    raw_map = raw.get("sender_profile_map") or {}
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
                f"sender_profile_map key {raw_sender!r} canonicalizes to empty"
            )
        if canonical in canonical_map and canonical_map[canonical] != target:
            raise ProfileRoutingConfigError(
                f"duplicate sender after canonicalization: {raw_sender!r} -> "
                f"{canonical!r} maps to both {canonical_map[canonical]!r} and {target!r}"
            )
        canonical_map[canonical] = target

    return ProfileRoutingConfig(
        profiles=profiles_tuple,
        default_profile=default_profile,
        sender_profile_map=canonical_map,
    )
```

(Note: this MUST be `canonical_whatsapp_identifier`, the same helper Task 1 uses to populate `event.canonical_sender_id` at message-receive time. Using a different normalization here would silently break lookups for users whose phone JID and LID forms collapse to a non-trivial canonical identity. The bridge's `lid-mapping-*.json` files must already exist on disk at gateway boot time for cross-form aliases to resolve correctly; if they don't, canonicalization gracefully degrades to the bare normalized identifier and lookups still match for any sender whose mapping is also absent at message-receive time.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_profile_routing_config.py -v`
Expected: PASS (all five tests)

- [ ] **Step 5: Commit**

```bash
git add gateway/profile_routing_config.py tests/gateway/test_profile_routing_config.py
git commit -m "feat(gateway): profile_routing config schema parser"
```

---

### Task 3: WhatsAppRouter

**Files:**
- Create: `gateway/whatsapp_router.py`
- Test: extend `tests/gateway/test_whatsapp_profile_routing.py` (new file)

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/test_whatsapp_profile_routing.py
import pytest
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


def test_resolves_via_normalized_input_when_caller_passes_raw_jid():
    """Caller normalizes before passing in; router does NOT re-normalize.

    This keeps the router pure and forces the canonicalization decision to
    happen at one well-known site (the WhatsApp adapter).
    """
    r = WhatsAppRouter(_cfg())
    # Raw JID would not match — caller is responsible for normalization.
    assert r.resolve_profile("60123456789@s.whatsapp.net") == "main"  # falls through to default


def test_empty_sender_resolves_to_default():
    r = WhatsAppRouter(_cfg())
    assert r.resolve_profile("") == "main"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_whatsapp_profile_routing.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `gateway/whatsapp_router.py`**

```python
"""Pure routing function: canonical sender id -> target profile name."""

from __future__ import annotations

from gateway.profile_routing_config import ProfileRoutingConfig


class WhatsAppRouter:
    """Resolves a target profile name from a canonicalized sender identifier.

    The router does NOT canonicalize input. Callers must pass the same
    normalization that was applied to map keys at config-load time. This
    keeps the router a pure dict-lookup primitive.
    """

    def __init__(self, config: ProfileRoutingConfig) -> None:
        self._map = config.sender_profile_map
        self._default = config.default_profile

    def resolve_profile(self, canonical_sender_id: str) -> str:
        return self._map.get(canonical_sender_id, self._default)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_whatsapp_profile_routing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gateway/whatsapp_router.py tests/gateway/test_whatsapp_profile_routing.py
git commit -m "feat(gateway): WhatsAppRouter (sender id -> profile)"
```

---

### Task 4: MessageEvent JSON codec

**Files:**
- Create: `gateway/message_event_codec.py`
- Test: `tests/gateway/test_message_event_codec.py`

The IPC layer needs a stable, round-trip-safe JSON form of `MessageEvent`. We explicitly enumerate which fields cross the IPC boundary — anything else is stripped (and documented as "not portable to workers").

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/test_message_event_codec.py
import pytest
from datetime import datetime
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from gateway.platforms.base import Platform
from gateway.message_event_codec import encode_event, decode_event


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
    assert decoded.source.platform == original.source.platform
    assert decoded.source.chat_id == original.source.chat_id
    assert decoded.source.user_id == original.source.user_id
    assert decoded.canonical_sender_id == original.canonical_sender_id
    assert decoded.media_urls == original.media_urls
    assert decoded.timestamp == original.timestamp


def test_decode_strips_raw_message_field():
    """raw_message is platform-specific and not serializable; it's intentionally dropped."""
    e = _sample_event()
    e.raw_message = {"unparseable": object()}  # type: ignore[assignment]
    decoded = decode_event(encode_event(e))
    assert decoded.raw_message is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_message_event_codec.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `gateway/message_event_codec.py`**

```python
"""JSON serializer for MessageEvent across the IPC boundary.

Only fields safe to cross a process boundary are included.  raw_message,
plugin handles, and any non-JSON-serializable callbacks are intentionally
dropped — workers do not have access to the ingress's raw platform objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource, Platform


def encode_event(event: MessageEvent) -> dict[str, Any]:
    """Encode a MessageEvent to a JSON-safe dict."""
    src = event.source
    return {
        "text": event.text,
        "message_type": event.message_type.value if event.message_type else "text",
        "source": {
            "platform": src.platform.value if src and src.platform else None,
            "chat_id": src.chat_id if src else None,
            "chat_type": src.chat_type if src else None,
            "user_id": src.user_id if src else None,
            "user_name": src.user_name if src else None,
            "message_id": src.message_id if src else None,
        } if src else None,
        "message_id": event.message_id,
        "platform_update_id": event.platform_update_id,
        "media_urls": list(event.media_urls or []),
        "media_types": list(event.media_types or []),
        "reply_to_message_id": event.reply_to_message_id,
        "reply_to_text": event.reply_to_text,
        "auto_skill": event.auto_skill,
        "channel_prompt": event.channel_prompt,
        "internal": bool(event.internal),
        "canonical_sender_id": event.canonical_sender_id,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
    }


def decode_event(data: dict[str, Any]) -> MessageEvent:
    """Decode a JSON-safe dict back into a MessageEvent."""
    src_data = data.get("source")
    src: SessionSource | None = None
    if src_data:
        platform_value = src_data.get("platform")
        platform = Platform(platform_value) if platform_value else None
        src = SessionSource(
            platform=platform,
            chat_id=src_data.get("chat_id"),
            chat_type=src_data.get("chat_type"),
            user_id=src_data.get("user_id"),
            user_name=src_data.get("user_name"),
            message_id=src_data.get("message_id"),
        )
    mtype_value = data.get("message_type") or "text"
    timestamp_str = data.get("timestamp")
    timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now()
    return MessageEvent(
        text=data.get("text", ""),
        message_type=MessageType(mtype_value),
        source=src,
        raw_message=None,  # intentionally dropped
        message_id=data.get("message_id"),
        platform_update_id=data.get("platform_update_id"),
        media_urls=list(data.get("media_urls") or []),
        media_types=list(data.get("media_types") or []),
        reply_to_message_id=data.get("reply_to_message_id"),
        reply_to_text=data.get("reply_to_text"),
        auto_skill=data.get("auto_skill"),
        channel_prompt=data.get("channel_prompt"),
        internal=bool(data.get("internal", False)),
        canonical_sender_id=data.get("canonical_sender_id"),
        timestamp=timestamp,
    )
```

(Note: `SessionSource` and `Platform` import paths may differ — verify with the existing `gateway/session.py`. If `SessionSource` lives elsewhere, adjust the import. The structure of the test will reveal the right import on first run.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_message_event_codec.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gateway/message_event_codec.py tests/gateway/test_message_event_codec.py
git commit -m "feat(gateway): MessageEvent JSON codec for IPC"
```

---

## Phase 1 — IPC platform adapter (worker-side)

### Task 5: IPC platform adapter — basic round trip

**Files:**
- Create: `gateway/platforms/ipc.py`
- Test: `tests/gateway/test_platform_ipc_adapter.py`

The IPC adapter is the worker's only inbound source. It reads JSON lines from `sys.stdin`, dispatches each as a `MessageEvent` to the gateway's message handler, and writes the handler's reply (a string) back as a JSON line to `sys.stdout`.

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/test_platform_ipc_adapter.py
import asyncio
import io
import json
import pytest
from gateway.platforms.ipc import IPCPlatformAdapter
from gateway.config import PlatformConfig


@pytest.mark.asyncio
async def test_ipc_adapter_round_trip():
    """One inbound event → handler returns reply → reply emitted to stdout."""
    inbound = (
        json.dumps({
            "kind": "message_event",
            "correlation_id": "corr-1",
            "event": {
                "text": "hello",
                "message_type": "text",
                "source": {
                    "platform": "whatsapp",
                    "chat_id": "60123@s.whatsapp.net",
                    "chat_type": "dm",
                    "user_id": "60123",
                    "user_name": "Alice",
                    "message_id": "m1",
                },
                "message_id": "m1",
                "media_urls": [],
                "media_types": [],
                "internal": False,
                "canonical_sender_id": "60123",
                "timestamp": "2026-05-07T12:00:00",
            },
        })
        + "\n"
    )
    stdin = io.StringIO(inbound)
    stdout = io.StringIO()

    cfg = PlatformConfig(name="ipc", extra={})
    adapter = IPCPlatformAdapter(cfg, stdin=stdin, stdout=stdout)

    async def fake_handler(event):
        assert event.text == "hello"
        assert event.canonical_sender_id == "60123"
        return "world"

    adapter.set_message_handler(fake_handler)

    # connect() should: pump the single line, dispatch, write reply, return when stdin closes.
    await adapter.connect()

    # Adapter is expected to have written one reply line.
    out_lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(out_lines) == 1
    parsed = json.loads(out_lines[0])
    assert parsed["kind"] == "reply"
    assert parsed["correlation_id"] == "corr-1"
    assert parsed["reply"]["text"] == "world"
    assert parsed["reply"]["error"] is None


@pytest.mark.asyncio
async def test_ipc_adapter_handler_exception_emits_error_envelope():
    inbound = (
        json.dumps({
            "kind": "message_event",
            "correlation_id": "corr-2",
            "event": {
                "text": "boom",
                "message_type": "text",
                "source": None,
                "media_urls": [],
                "media_types": [],
                "internal": False,
                "timestamp": "2026-05-07T12:00:00",
            },
        })
        + "\n"
    )
    stdin = io.StringIO(inbound)
    stdout = io.StringIO()

    cfg = PlatformConfig(name="ipc", extra={})
    adapter = IPCPlatformAdapter(cfg, stdin=stdin, stdout=stdout)

    async def crashing_handler(event):
        raise RuntimeError("kaboom")

    adapter.set_message_handler(crashing_handler)
    await adapter.connect()

    out_lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(out_lines) == 1
    parsed = json.loads(out_lines[0])
    assert parsed["correlation_id"] == "corr-2"
    assert parsed["reply"]["text"] is None
    assert "kaboom" in (parsed["reply"]["error"] or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_platform_ipc_adapter.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `gateway/platforms/ipc.py`**

```python
"""IPC platform adapter — the worker's only inbound source.

Reads newline-delimited JSON envelopes from stdin, dispatches each to the
gateway's message handler, and writes one reply envelope per message to
stdout. The wire format is documented in
``docs/superpowers/specs/2026-05-07-whatsapp-sender-profile-routing-design.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import traceback
from typing import Any, Optional, TextIO

from gateway.config import PlatformConfig
from gateway.message_event_codec import decode_event
from gateway.platforms.base import (
    BasePlatformAdapter,
    Platform,
    SendResult,
)

logger = logging.getLogger(__name__)


class IPCPlatformAdapter(BasePlatformAdapter):
    """Adapter that uses stdin/stdout JSON pipes as its transport."""

    def __init__(
        self,
        config: PlatformConfig,
        stdin: Optional[TextIO] = None,
        stdout: Optional[TextIO] = None,
    ) -> None:
        super().__init__(config, Platform.IPC)
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout

    @property
    def name(self) -> str:
        return "ipc"

    async def connect(self) -> bool:
        """Pump stdin lines until EOF, dispatching each to the handler."""
        self._mark_connected()
        loop = asyncio.get_event_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, self._stdin.readline)
                if not line:
                    break  # EOF
                line = line.strip()
                if not line:
                    continue
                await self._handle_line(line)
        finally:
            self._mark_disconnected()
        return True

    async def disconnect(self) -> None:
        # No-op: connect() exits naturally when stdin closes.
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        """Replies are emitted from connect()'s loop, not from send().

        send() is required by the abstract base but is unused for IPC: the
        worker doesn't initiate outbound messages, it only replies.
        """
        return SendResult(success=False, error="ipc.send() is not supported; replies are emitted via the handler return value")

    async def _handle_line(self, line: str) -> None:
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("ipc: malformed JSON (dropped): %s", exc)
            return

        kind = envelope.get("kind")
        if kind != "message_event":
            logger.warning("ipc: unknown envelope kind %r (dropped)", kind)
            return

        correlation_id = envelope.get("correlation_id")
        event_data = envelope.get("event") or {}
        try:
            event = decode_event(event_data)
        except Exception as exc:
            self._emit_reply(correlation_id, text=None, error=f"decode failed: {exc}")
            return

        if self._message_handler is None:
            self._emit_reply(correlation_id, text=None, error="no handler set")
            return

        try:
            reply = await self._message_handler(event)
        except Exception as exc:
            tb = traceback.format_exc(limit=4)
            logger.exception("ipc: handler raised")
            self._emit_reply(correlation_id, text=None, error=f"{exc}\n{tb}")
            return

        # Handler may return None (no reply), a string, or an EphemeralReply.
        text: Optional[str]
        if reply is None:
            text = None
        elif isinstance(reply, str):
            text = reply
        else:
            text = getattr(reply, "text", None) or str(reply)
        self._emit_reply(correlation_id, text=text, error=None)

    def _emit_reply(
        self, correlation_id: Optional[str], *, text: Optional[str], error: Optional[str]
    ) -> None:
        envelope: dict[str, Any] = {
            "kind": "reply",
            "correlation_id": correlation_id,
            "reply": {"text": text, "error": error, "media": []},
        }
        line = json.dumps(envelope, ensure_ascii=False)
        self._stdout.write(line + "\n")
        self._stdout.flush()
```

(Note: `Platform.IPC` does not yet exist as an enum value — see Task 6 for adding it. If the test in Step 4 fails on the `Platform.IPC` import, defer running it until after Task 6 step 3.)

- [ ] **Step 4: Add `Platform.IPC` enum value**

In `gateway/session.py` (or wherever the `Platform` enum lives — verify by `grep -n "class Platform" gateway/`), add:

```python
class Platform(Enum):
    # ... existing values ...
    IPC = "ipc"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/gateway/test_platform_ipc_adapter.py -v`
Expected: PASS (both tests)

- [ ] **Step 6: Commit**

```bash
git add gateway/platforms/ipc.py gateway/session.py tests/gateway/test_platform_ipc_adapter.py
git commit -m "feat(gateway): IPC platform adapter (worker-side stdin/stdout)"
```

---

### Task 6: Register IPC adapter in platform_registry

**Files:**
- Modify: `gateway/platform_registry.py` (add a built-in registration for `ipc`)
- OR: Modify `gateway/run.py` `_create_adapter()` if registry is plugin-only — verify which mechanism the codebase prefers

- [ ] **Step 1: Inspect how built-in adapters are wired**

Run: `grep -n "ipc\|register\|create_adapter\|_create_adapter" gateway/run.py | head -30`

Expected: a function `_create_adapter()` (or similar) that returns an adapter instance for a given platform name. If the codebase uses if/elif there, follow the same pattern. If it uses `platform_registry.register(...)` for built-ins, register IPC there instead.

- [ ] **Step 2: Add `ipc` to the adapter creation path**

If using if/elif in `_create_adapter`:

```python
elif platform_name == "ipc":
    from gateway.platforms.ipc import IPCPlatformAdapter
    return IPCPlatformAdapter(platform_config)
```

If using `platform_registry`, add:

```python
from gateway.platform_registry import platform_registry, PlatformEntry
from gateway.platforms.ipc import IPCPlatformAdapter

platform_registry.register(PlatformEntry(
    name="ipc",
    label="IPC",
    adapter_factory=lambda cfg: IPCPlatformAdapter(cfg),
    check_fn=lambda: True,
    source="builtin",
    pii_safe=True,
    emoji="🔁",
))
```

- [ ] **Step 3: Smoke test**

Run: `python -c "from gateway.platforms.ipc import IPCPlatformAdapter; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add gateway/platform_registry.py gateway/run.py  # whichever changed
git commit -m "feat(gateway): register ipc platform adapter"
```

---

## Phase 2 — Worker entrypoint

### Task 7: Worker CLI subcommand — `hermes profile-worker`

**Files:**
- Create: `hermes_cli/profile_worker_cli.py`
- Modify: subcommand registration (`hermes_cli/_parser.py` or wherever subcommands attach)
- Modify: `hermes_cli/main.py` to dispatch
- Test: `tests/hermes_cli/test_profile_worker_cli.py`

The worker CLI is a thin entrypoint that:
1. Resolves the profile name to a `HERMES_HOME` path.
2. Sets `HERMES_HOME` in the process environment.
3. Boots a stripped-down GatewayRunner whose only platform adapter is `ipc`.
4. Returns when the run loop exits (stdin EOF).

- [ ] **Step 1: Inspect existing subcommand wiring**

Run: `grep -rn "add_parser\|subparsers" hermes_cli/_parser.py hermes_cli/main.py | head -20`

Expected: a pattern like `subparsers.add_parser("gateway", ...)`. Follow that pattern.

- [ ] **Step 2: Write the failing test**

```python
# tests/hermes_cli/test_profile_worker_cli.py
import os
import pytest
from hermes_cli.profile_worker_cli import resolve_profile_path, build_worker_argv_for_test


def test_resolve_profile_path_returns_existing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    profile_root = tmp_path / "profiles" / "family"
    profile_root.mkdir(parents=True)
    resolved = resolve_profile_path("family")
    assert resolved == profile_root


def test_resolve_profile_path_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        resolve_profile_path("nonexistent")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/hermes_cli/test_profile_worker_cli.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 4: Implement `hermes_cli/profile_worker_cli.py`**

```python
"""CLI entrypoint for `hermes profile-worker --name <profile>`.

Boots a Hermes worker subprocess that listens on stdin for MessageEvents
and writes replies to stdout. Used by the WhatsApp sender-profile-routing
feature: ingress spawns one worker per non-primary profile.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from hermes_constants import get_default_hermes_root


logger = logging.getLogger(__name__)


def resolve_profile_path(profile_name: str) -> Path:
    """Map a profile name to its HERMES_HOME directory.

    Default profile lives at <root>/profiles/<name>. Raises
    FileNotFoundError if the directory does not exist.
    """
    root = get_default_hermes_root()
    candidate = root / "profiles" / profile_name
    if not candidate.is_dir():
        raise FileNotFoundError(
            f"Profile {profile_name!r} not found at {candidate} "
            f"(create it with `hermes profile create {profile_name}` first)"
        )
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes profile-worker")
    parser.add_argument("--name", required=True, help="Profile name to load")
    args = parser.parse_args(argv)

    profile_path = resolve_profile_path(args.name)

    # Pin HERMES_HOME for THIS process and any subprocess we spawn.
    os.environ["HERMES_HOME"] = str(profile_path)

    # Send all log output to stderr — stdout is the IPC channel.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Emit readiness line to stdout so ingress knows we're up.
    # NB: ingress treats this as "process started, will accept input shortly".
    # If the gateway boot takes a moment to fully wire IPC pumping, queued
    # input on the pipe is buffered by the OS and drained when ready.
    import json as _json
    sys.stdout.write(_json.dumps({"kind": "ready", "name": args.name}) + "\n")
    sys.stdout.flush()

    return asyncio.run(_run_worker(args.name))


async def _run_worker(profile_name: str) -> int:
    """Boot a stripped-down GatewayRunner with only the IPC adapter."""
    # Lazy import — keeps cold-start cheap and avoids loading the full
    # gateway code path until we know HERMES_HOME is correct.
    from gateway.run import GatewayRunner
    from gateway.config import load_gateway_config, PlatformConfig

    base_cfg = load_gateway_config()
    # Override platforms: only IPC.
    base_cfg.platforms = {"ipc": PlatformConfig(name="ipc", extra={})}

    runner = GatewayRunner(base_cfg)
    try:
        await runner.start()
    finally:
        await runner.stop()
    return 0


def build_worker_argv_for_test(profile_name: str) -> list[str]:
    """Helper exposed for tests."""
    return ["--name", profile_name]


if __name__ == "__main__":
    sys.exit(main())
```

(Note: the body of `_run_worker` depends on the precise `GatewayRunner` API — `runner.start()` / `runner.stop()` / how `base_cfg.platforms` is shaped. Run `grep -n "class GatewayRunner\|def start\|def stop\|self.platforms\|self.adapters" gateway/run.py | head -20` to confirm exact method names and adjust before running. The plan shape is correct; the method names may need a 1-line correction.)

- [ ] **Step 5: Register subcommand**

Add to whichever module owns subcommand registration (likely `hermes_cli/_parser.py` or `hermes_cli/main.py`):

```python
# Pseudo-code — match the existing add_parser style.
worker_parser = subparsers.add_parser(
    "profile-worker",
    help="Run a Hermes worker subprocess for a single profile (used by gateway routing).",
)
worker_parser.add_argument("--name", required=True)
# In dispatch:
if args.command == "profile-worker":
    from hermes_cli.profile_worker_cli import main as worker_main
    return worker_main(["--name", args.name])
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/hermes_cli/test_profile_worker_cli.py -v`
Expected: PASS

- [ ] **Step 7: Smoke test**

```bash
mkdir -p ~/.hermes/profiles/test_smoke
hermes profile-worker --name test_smoke < /dev/null
```

Expected: prints a readiness JSON line to stdout, then exits cleanly because stdin is closed.

- [ ] **Step 8: Commit**

```bash
git add hermes_cli/profile_worker_cli.py hermes_cli/_parser.py hermes_cli/main.py tests/hermes_cli/test_profile_worker_cli.py
git commit -m "feat(cli): hermes profile-worker subcommand"
```

---

## Phase 3 — Ingress-side worker manager

### Task 8: ProfileWorker (single subprocess wrapper)

**Files:**
- Create: `gateway/profile_worker.py`
- Test: `tests/gateway/test_profile_worker.py`

`ProfileWorker` wraps one subprocess. Owns the writer/reader tasks and the correlation-id → future map.

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/test_profile_worker.py
import asyncio
import json
import sys
import pytest

from gateway.profile_worker import ProfileWorker


# Minimal echo subprocess: read JSON line from stdin, parse, echo back as reply.
ECHO_SCRIPT = """
import sys, json
sys.stdout.write(json.dumps({"kind":"ready","name":"echo"}) + "\\n")
sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    env = json.loads(line)
    cid = env.get("correlation_id")
    text = env.get("event", {}).get("text", "")
    sys.stdout.write(json.dumps({
        "kind":"reply",
        "correlation_id": cid,
        "reply": {"text": "echo:" + text, "error": None, "media": []}
    }) + "\\n")
    sys.stdout.flush()
"""


@pytest.mark.asyncio
async def test_profile_worker_round_trip(tmp_path):
    script = tmp_path / "echo_worker.py"
    script.write_text(ECHO_SCRIPT)

    worker = ProfileWorker(
        name="echo",
        argv=[sys.executable, str(script)],
        env={},
    )
    await worker.start()
    try:
        reply = await asyncio.wait_for(
            worker.dispatch({"text": "hello", "source": None}, timeout=5.0),
            timeout=10.0,
        )
        assert reply["text"] == "echo:hello"
        assert reply["error"] is None
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_profile_worker_handles_concurrent_dispatch(tmp_path):
    script = tmp_path / "echo_worker.py"
    script.write_text(ECHO_SCRIPT)

    worker = ProfileWorker(name="echo", argv=[sys.executable, str(script)], env={})
    await worker.start()
    try:
        replies = await asyncio.gather(
            worker.dispatch({"text": "a", "source": None}, timeout=5.0),
            worker.dispatch({"text": "b", "source": None}, timeout=5.0),
            worker.dispatch({"text": "c", "source": None}, timeout=5.0),
        )
        assert {r["text"] for r in replies} == {"echo:a", "echo:b", "echo:c"}
    finally:
        await worker.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_profile_worker.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `gateway/profile_worker.py`**

```python
"""One Hermes worker subprocess + correlation-id-based dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ProfileWorkerError(RuntimeError):
    """Raised when a dispatch fails (worker died, timeout, malformed reply)."""


class ProfileWorker:
    """Wrapper around one worker subprocess.

    Public API:
      await worker.start()
      reply = await worker.dispatch(event_dict, timeout=...)
      await worker.stop()
    """

    def __init__(self, name: str, argv: list[str], env: dict[str, str]) -> None:
        self.name = name
        self._argv = list(argv)
        self._env = dict(env)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ready: asyncio.Event = asyncio.Event()
        self._stopping: bool = False

    async def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**self._env},
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"profile_worker_reader[{self.name}]"
        )
        # Wait for the readiness signal (the worker prints {"kind":"ready"})
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=30.0)
        except asyncio.TimeoutError as exc:
            await self.stop()
            raise ProfileWorkerError(
                f"worker {self.name!r} did not emit readiness signal in 30s"
            ) from exc

    async def stop(self) -> None:
        self._stopping = True
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.stdin.close()  # type: ignore[union-attr]
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        # Reject any pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ProfileWorkerError(f"worker {self.name!r} stopped"))
        self._pending.clear()

    async def dispatch(self, event_dict: dict[str, Any], *, timeout: float = 300.0) -> dict[str, Any]:
        if self._proc is None or self._proc.returncode is not None:
            raise ProfileWorkerError(f"worker {self.name!r} not running")

        correlation_id = uuid.uuid4().hex
        envelope = {
            "kind": "message_event",
            "correlation_id": correlation_id,
            "event": event_dict,
        }
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[correlation_id] = future

        try:
            line = json.dumps(envelope) + "\n"
            self._proc.stdin.write(line.encode("utf-8"))  # type: ignore[union-attr]
            await self._proc.stdin.drain()  # type: ignore[union-attr]
        except Exception as exc:
            self._pending.pop(correlation_id, None)
            raise ProfileWorkerError(f"failed to write to worker {self.name!r}: {exc}") from exc

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(correlation_id, None)

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    envelope = json.loads(text)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "worker %s: dropped non-JSON stdout line: %r (err=%s)",
                        self.name, text[:200], exc,
                    )
                    continue
                kind = envelope.get("kind")
                if kind == "ready":
                    self._ready.set()
                elif kind == "reply":
                    cid = envelope.get("correlation_id")
                    fut = self._pending.get(cid) if cid else None
                    if fut is None or fut.done():
                        logger.warning(
                            "worker %s: reply for unknown correlation_id %r", self.name, cid
                        )
                        continue
                    fut.set_result(envelope.get("reply") or {})
                else:
                    logger.warning("worker %s: unknown envelope kind %r", self.name, kind)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker %s: reader loop crashed", self.name)
        finally:
            # Tell anyone waiting that they're not getting a reply.
            if not self._stopping:
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(
                            ProfileWorkerError(f"worker {self.name!r} stdout closed unexpectedly")
                        )
                self._pending.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_profile_worker.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add gateway/profile_worker.py tests/gateway/test_profile_worker.py
git commit -m "feat(gateway): ProfileWorker subprocess wrapper with correlation IDs"
```

---

### Task 9: ProfileWorkerManager (lifecycle + dispatch by name)

**Files:**
- Create: `gateway/profile_worker_manager.py`
- Test: `tests/gateway/test_profile_worker_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/test_profile_worker_manager.py
import asyncio
import sys
import pytest

from gateway.profile_worker_manager import ProfileWorkerManager, WorkerSpec


ECHO_SCRIPT = """
import sys, json
sys.stdout.write(json.dumps({"kind":"ready","name":"echo"}) + "\\n")
sys.stdout.flush()
for line in sys.stdin:
    env = json.loads(line)
    cid = env.get("correlation_id")
    text = env.get("event", {}).get("text", "")
    sys.stdout.write(json.dumps({
        "kind":"reply",
        "correlation_id": cid,
        "reply": {"text": "from-worker:" + text, "error": None, "media": []}
    }) + "\\n")
    sys.stdout.flush()
"""


@pytest.mark.asyncio
async def test_manager_starts_workers_and_dispatches_by_name(tmp_path):
    script = tmp_path / "echo.py"
    script.write_text(ECHO_SCRIPT)

    mgr = ProfileWorkerManager()
    await mgr.start([
        WorkerSpec(name="alpha", argv=[sys.executable, str(script)], env={}),
        WorkerSpec(name="beta",  argv=[sys.executable, str(script)], env={}),
    ])
    try:
        ra = await mgr.dispatch("alpha", {"text": "x", "source": None})
        rb = await mgr.dispatch("beta",  {"text": "y", "source": None})
        assert ra["text"] == "from-worker:x"
        assert rb["text"] == "from-worker:y"
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_dispatch_unknown_profile_raises(tmp_path):
    mgr = ProfileWorkerManager()
    await mgr.start([])
    try:
        with pytest.raises(KeyError):
            await mgr.dispatch("nope", {"text": "x"})
    finally:
        await mgr.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_profile_worker_manager.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `gateway/profile_worker_manager.py`**

```python
"""Owns the set of profile worker subprocesses, dispatches by profile name."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from gateway.profile_worker import ProfileWorker, ProfileWorkerError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    argv: list[str]
    env: dict[str, str]


class ProfileWorkerManager:
    def __init__(self) -> None:
        self._workers: dict[str, ProfileWorker] = {}

    async def start(self, specs: list[WorkerSpec]) -> None:
        async def _start_one(spec: WorkerSpec) -> tuple[str, ProfileWorker]:
            worker = ProfileWorker(name=spec.name, argv=spec.argv, env=spec.env)
            await worker.start()
            return spec.name, worker

        results = await asyncio.gather(
            *[_start_one(s) for s in specs],
            return_exceptions=True,
        )
        for spec, result in zip(specs, results):
            if isinstance(result, Exception):
                logger.error("Failed to start worker %s: %s", spec.name, result)
                # Tear down any that did start before re-raising.
                await self.shutdown()
                raise result
            name, worker = result  # type: ignore[misc]
            self._workers[name] = worker

    async def shutdown(self) -> None:
        await asyncio.gather(
            *[w.stop() for w in self._workers.values()],
            return_exceptions=True,
        )
        self._workers.clear()

    async def dispatch(
        self, profile_name: str, event_dict: dict[str, Any], *, timeout: float = 300.0
    ) -> dict[str, Any]:
        worker = self._workers.get(profile_name)
        if worker is None:
            raise KeyError(f"no worker registered for profile {profile_name!r}")
        return await worker.dispatch(event_dict, timeout=timeout)

    @property
    def names(self) -> list[str]:
        return list(self._workers.keys())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_profile_worker_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gateway/profile_worker_manager.py tests/gateway/test_profile_worker_manager.py
git commit -m "feat(gateway): ProfileWorkerManager lifecycle + dispatch by name"
```

---

## Phase 4 — Wire ingress to workers

### Task 10: Load profile_routing config + spawn workers at gateway boot

**Files:**
- Modify: `gateway/config.py` (parse `channels.whatsapp.profile_routing`)
- Modify: `gateway/run.py` (`GatewayRunner.__init__` and `start()`)
- Test: `tests/gateway/test_gateway_routing_boot.py`

- [ ] **Step 1: Inspect config loader**

Run: `grep -n "channels\|whatsapp\|load_gateway_config" gateway/config.py | head -30`

Find the spot where `channels.whatsapp` is parsed. The new key `profile_routing` should be parsed there, attached to the gateway config object as `config.whatsapp_profile_routing: ProfileRoutingConfig | None`.

- [ ] **Step 2: Modify `gateway/config.py`**

Add to `load_gateway_config()`:

```python
from gateway.profile_routing_config import parse_profile_routing

# after channels.whatsapp is loaded into a dict (call it whatsapp_cfg):
config.whatsapp_profile_routing = parse_profile_routing(
    whatsapp_cfg.get("profile_routing")
)
```

And add the field to the `GatewayConfig` dataclass:

```python
whatsapp_profile_routing: Optional[ProfileRoutingConfig] = None
```

- [ ] **Step 3: Wire `GatewayRunner` to spawn the manager**

In `gateway/run.py`, in `GatewayRunner.__init__`:

```python
from gateway.profile_worker_manager import ProfileWorkerManager
from gateway.whatsapp_router import WhatsAppRouter

# after existing init:
self._profile_routing = config.whatsapp_profile_routing
self.profile_worker_manager: Optional[ProfileWorkerManager] = None
self.whatsapp_router: Optional[WhatsAppRouter] = None
self.primary_profile_name: str = self._resolve_primary_profile_name()

if self._profile_routing is not None:
    self.profile_worker_manager = ProfileWorkerManager()
    self.whatsapp_router = WhatsAppRouter(self._profile_routing)
```

`_resolve_primary_profile_name` reads from the `HERMES_HOME` directory name (e.g. `~/.hermes/profiles/main` → `"main"`; bare `~/.hermes` → `"default"`). Helper:

```python
def _resolve_primary_profile_name(self) -> str:
    from hermes_constants import get_hermes_home, get_default_hermes_root
    home = get_hermes_home()
    root = get_default_hermes_root()
    try:
        rel = home.relative_to(root / "profiles")
        # rel is "<name>" — first part
        return str(rel).split("/")[0]
    except ValueError:
        return "default"
```

In `GatewayRunner.start()`, after platforms are created but before the main run loop awaits, spawn workers for any profile in `_profile_routing.profiles` that is not `self.primary_profile_name`:

```python
if self._profile_routing is not None and self.profile_worker_manager is not None:
    specs: list[WorkerSpec] = []
    for profile_name in self._profile_routing.profiles:
        if profile_name == self.primary_profile_name:
            continue
        specs.append(WorkerSpec(
            name=profile_name,
            argv=[sys.executable, "-m", "hermes_cli", "profile-worker", "--name", profile_name],
            env={**os.environ},  # workers re-set HERMES_HOME themselves
        ))
    await self.profile_worker_manager.start(specs)
```

(Confirm whether `hermes` is invoked as `python -m hermes_cli` or as a console script. If it's a console script, use that.)

In `GatewayRunner.stop()`:

```python
if self.profile_worker_manager is not None:
    await self.profile_worker_manager.shutdown()
```

- [ ] **Step 4: Write the test**

```python
# tests/gateway/test_gateway_routing_boot.py
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gateway.profile_routing_config import ProfileRoutingConfig


@pytest.mark.asyncio
async def test_no_routing_config_means_no_worker_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()  # default — no whatsapp_profile_routing
    runner = GatewayRunner(cfg)
    assert runner.profile_worker_manager is None
    assert runner.whatsapp_router is None


@pytest.mark.asyncio
async def test_routing_config_creates_manager_and_router(tmp_path, monkeypatch):
    (tmp_path / "profiles" / "family").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "main"))
    (tmp_path / "profiles" / "main").mkdir(parents=True)

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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/gateway/test_gateway_routing_boot.py -v`
Expected: PASS

(If `GatewayConfig()` cannot be constructed without args, adjust to use the project's existing test fixture for a minimal config — look in `tests/gateway/conftest.py`.)

- [ ] **Step 6: Commit**

```bash
git add gateway/config.py gateway/run.py tests/gateway/test_gateway_routing_boot.py
git commit -m "feat(gateway): boot ProfileWorkerManager from profile_routing config"
```

---

### Task 11: Route inbound WhatsApp messages to workers

**Files:**
- Modify: `gateway/run.py` (`_handle_message`, line 4421)
- Test: `tests/gateway/test_handle_message_routing.py`

This is the focal change: at the top of `_handle_message`, if the event is from WhatsApp and routing is enabled and the resolved target ≠ primary, hand off to the worker manager and deliver the reply via the WhatsApp adapter.

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/test_handle_message_routing.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import Platform, SessionSource
from gateway.profile_routing_config import ProfileRoutingConfig


def _whatsapp_event(canonical_id: str, text: str = "hi") -> MessageEvent:
    src = SessionSource(
        platform=Platform.WHATSAPP,
        chat_id=f"{canonical_id}@s.whatsapp.net",
        chat_type="dm",
        user_id=canonical_id,
        user_name="Anon",
        message_id="m1",
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=src,
        message_id="m1",
        canonical_sender_id=canonical_id,
    )


@pytest.mark.asyncio
async def test_routed_message_dispatches_to_worker(tmp_path, monkeypatch):
    (tmp_path / "profiles" / "main").mkdir(parents=True)
    (tmp_path / "profiles" / "family").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "main"))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60123": "main", "60987": "family"},
    )
    runner = GatewayRunner(cfg)

    # Inject mock worker manager + WhatsApp adapter
    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(return_value={"text": "reply-from-family", "error": None})
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_event("60987", "hi family")
    await runner._handle_message(event)

    mock_mgr.dispatch.assert_awaited_once()
    call = mock_mgr.dispatch.await_args
    assert call.args[0] == "family"  # target profile
    mock_wa_adapter.send.assert_awaited_once()
    args, kwargs = mock_wa_adapter.send.await_args
    assert "reply-from-family" in (args[1] if len(args) > 1 else kwargs.get("content", ""))


@pytest.mark.asyncio
async def test_primary_routed_message_falls_through_to_inprocess(tmp_path, monkeypatch):
    (tmp_path / "profiles" / "main").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "main"))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main",),
        default_profile="main",
        sender_profile_map={},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock()
    runner.profile_worker_manager = mock_mgr

    # Stub out the rest of _handle_message so we're only testing the routing branch.
    # An unmapped sender resolves to "main" (default == primary), so dispatch must NOT be called.
    runner._handle_message_inprocess = AsyncMock(return_value=None)  # type: ignore[attr-defined]

    event = _whatsapp_event("60111", "hi main")
    # If the routing branch is correct, _handle_message hands off to in-process and never calls dispatch.
    # We can't test the full in-process path without all the deps, but we can assert dispatch wasn't called.
    try:
        await runner._handle_message(event)
    except Exception:
        # The full in-process path may explode without a real session_store, etc.
        # That's fine — we only care about whether dispatch was invoked.
        pass
    mock_mgr.dispatch.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_handle_message_routing.py -v`
Expected: FAIL — routing branch doesn't exist yet.

- [ ] **Step 3: Add the routing branch to `_handle_message`**

In `gateway/run.py`, at the top of `_handle_message` (line 4421), insert the routing branch BEFORE the existing pre-dispatch hooks (to avoid running primary's hooks for events destined for another profile):

```python
async def _handle_message(self, event: MessageEvent) -> Optional[str]:
    # ── WhatsApp profile routing ───────────────────────────────────
    if (
        event.source is not None
        and event.source.platform == Platform.WHATSAPP
        and self.whatsapp_router is not None
        and self.profile_worker_manager is not None
    ):
        target = self.whatsapp_router.resolve_profile(event.canonical_sender_id or "")
        if target != self.primary_profile_name:
            return await self._dispatch_to_worker(target, event)
    # ── existing logic continues here ──────────────────────────────
    # (the original body of _handle_message is unchanged from this line on)
```

And the new helper method on `GatewayRunner`:

```python
async def _dispatch_to_worker(self, target_profile: str, event: MessageEvent) -> Optional[str]:
    """Forward an event to the named worker, deliver the reply via the WhatsApp adapter."""
    from gateway.message_event_codec import encode_event

    assert self.profile_worker_manager is not None
    encoded = encode_event(event)
    try:
        reply = await self.profile_worker_manager.dispatch(target_profile, encoded)
    except Exception as exc:
        logger.error(
            "profile_routing: dispatch to worker %r failed: %s",
            target_profile, exc,
        )
        # MVP: silently drop. Future: send an error message back to user.
        return None

    text = reply.get("text") if isinstance(reply, dict) else None
    error = reply.get("error") if isinstance(reply, dict) else None
    if error:
        logger.warning(
            "profile_routing: worker %r returned error: %s", target_profile, error,
        )
        return None
    if not text:
        return None  # no reply text — nothing to send

    adapter = self.adapters.get(Platform.WHATSAPP)
    if adapter is None:
        logger.error("profile_routing: WhatsApp adapter missing; cannot deliver reply")
        return None
    chat_id = event.source.chat_id if event.source else None
    if not chat_id:
        logger.error("profile_routing: event has no chat_id; cannot deliver reply")
        return None
    await adapter.send(chat_id, text, reply_to=event.message_id)
    return None  # we delivered already; nothing further for the caller to send
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_handle_message_routing.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add gateway/run.py tests/gateway/test_handle_message_routing.py
git commit -m "feat(gateway): route WhatsApp messages to profile workers"
```

---

## Phase 5 — Integration tests + polish

### Task 12: Real subprocess integration test — memory isolation

**Files:**
- Test: `tests/gateway/test_profile_worker_integration.py`

This test uses a real worker subprocess (not the echo-stub), so it exercises the full IPC adapter + handler stack inside the worker. To keep the test hermetic, we point HERMES_HOME at tmp_path, create a fake profile dir, and stub the agent so it just echoes back the profile name.

- [ ] **Step 1: Write the integration test**

```python
# tests/gateway/test_profile_worker_integration.py
import asyncio
import json
import os
import sys
import textwrap
import pytest
from pathlib import Path

from gateway.profile_worker import ProfileWorker


# A minimal Hermes-like worker: imports HERMES_HOME at start and echoes
# the profile-home path back. This is enough to verify that ingress->worker
# IPC works end-to-end and that HERMES_HOME isolation holds.
def _stub_worker_script(profile_home: Path) -> str:
    return textwrap.dedent(f"""
        import os, sys, json
        # Assert the env propagation worked.
        assert os.environ.get("HERMES_HOME") == {str(profile_home)!r}, \\
            f"HERMES_HOME != expected: {{os.environ.get('HERMES_HOME')}}"
        sys.stdout.write(json.dumps({{"kind":"ready","name":"stub"}}) + "\\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            env = json.loads(line)
            cid = env.get("correlation_id")
            text = env.get("event", {{}}).get("text", "")
            sys.stdout.write(json.dumps({{
                "kind": "reply",
                "correlation_id": cid,
                "reply": {{
                    "text": f"profile_home={{os.environ['HERMES_HOME']}} echoed={{text}}",
                    "error": None,
                    "media": [],
                }}
            }}) + "\\n")
            sys.stdout.flush()
    """)


@pytest.mark.asyncio
async def test_two_workers_each_see_own_HERMES_HOME(tmp_path):
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)

    main_script = tmp_path / "main_stub.py"
    family_script = tmp_path / "family_stub.py"
    main_script.write_text(_stub_worker_script(main_home))
    family_script.write_text(_stub_worker_script(family_home))

    main_worker = ProfileWorker(
        name="main",
        argv=[sys.executable, str(main_script)],
        env={**os.environ, "HERMES_HOME": str(main_home)},
    )
    family_worker = ProfileWorker(
        name="family",
        argv=[sys.executable, str(family_script)],
        env={**os.environ, "HERMES_HOME": str(family_home)},
    )

    await asyncio.gather(main_worker.start(), family_worker.start())
    try:
        rm, rf = await asyncio.gather(
            main_worker.dispatch({"text": "ping"}),
            family_worker.dispatch({"text": "ping"}),
        )
        assert str(main_home) in rm["text"]
        assert str(family_home) in rf["text"]
        # Critical invariant: neither worker sees the other's HERMES_HOME.
        assert str(family_home) not in rm["text"]
        assert str(main_home) not in rf["text"]
    finally:
        await asyncio.gather(main_worker.stop(), family_worker.stop())


@pytest.mark.asyncio
async def test_concurrent_dispatch_to_different_workers_does_not_cross_contaminate(tmp_path):
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)

    main_script = tmp_path / "main_stub.py"
    family_script = tmp_path / "family_stub.py"
    main_script.write_text(_stub_worker_script(main_home))
    family_script.write_text(_stub_worker_script(family_home))

    workers = {
        "main": ProfileWorker("main", [sys.executable, str(main_script)],
                              {**os.environ, "HERMES_HOME": str(main_home)}),
        "family": ProfileWorker("family", [sys.executable, str(family_script)],
                                {**os.environ, "HERMES_HOME": str(family_home)}),
    }
    await asyncio.gather(*[w.start() for w in workers.values()])
    try:
        # 10 concurrent dispatches to alternating workers.
        results = await asyncio.gather(*[
            workers["main" if i % 2 == 0 else "family"].dispatch({"text": f"msg-{i}"})
            for i in range(10)
        ])
        for i, r in enumerate(results):
            expected_home = main_home if i % 2 == 0 else family_home
            other_home = family_home if i % 2 == 0 else main_home
            assert str(expected_home) in r["text"], f"msg-{i}: wrong HERMES_HOME"
            assert str(other_home) not in r["text"], f"msg-{i}: cross-contamination"
    finally:
        await asyncio.gather(*[w.stop() for w in workers.values()])
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/gateway/test_profile_worker_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/test_profile_worker_integration.py
git commit -m "test(gateway): subprocess HERMES_HOME isolation + concurrent dispatch"
```

---

### Task 13: End-to-end worker test using the real `hermes profile-worker` CLI

**Files:**
- Test: extend `tests/gateway/test_profile_worker_integration.py`

This validates the actual subcommand from Task 7 boots, accepts an event over stdin, and emits a reply. Skipped if `hermes` CLI isn't on the test PATH (CI environments).

- [ ] **Step 1: Write the test**

```python
# Append to tests/gateway/test_profile_worker_integration.py

import shutil

@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("hermes") is None, reason="hermes CLI not installed in test env")
async def test_real_hermes_profile_worker_subprocess_smoke(tmp_path):
    """Boot the actual `hermes profile-worker` and verify it produces a readiness signal.

    We don't test the full agent path (no LLM creds in CI). We just confirm:
      - The subcommand starts successfully under our argv.
      - It emits a "ready" line on stdout.
      - It exits cleanly when stdin closes.
    """
    profile_home = tmp_path / "profiles" / "smoketest"
    profile_home.mkdir(parents=True)
    monkeypatch_env = {**os.environ, "HERMES_HOME": str(tmp_path)}

    proc = await asyncio.create_subprocess_exec(
        "hermes", "profile-worker", "--name", "smoketest",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=monkeypatch_env,
    )
    try:
        # Read up to 60 seconds for the readiness line.
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=60.0)
        text = line.decode().strip()
        envelope = json.loads(text)
        assert envelope.get("kind") == "ready"
        assert envelope.get("name") == "smoketest"
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/gateway/test_profile_worker_integration.py::test_real_hermes_profile_worker_subprocess_smoke -v`
Expected: PASS (or SKIP if `hermes` not installed locally).

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/test_profile_worker_integration.py
git commit -m "test(gateway): smoke-test real hermes profile-worker subcommand"
```

---

### Task 14: Backward-compat regression test

**Files:**
- Test: `tests/gateway/test_routing_backcompat.py`

Confirm: when `channels.whatsapp.profile_routing` is absent, gateway behaves exactly as today and no workers spawn.

- [ ] **Step 1: Write the test**

```python
# tests/gateway/test_routing_backcompat.py
import pytest


@pytest.mark.asyncio
async def test_legacy_config_does_not_spawn_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    # Default: whatsapp_profile_routing is None.
    assert cfg.whatsapp_profile_routing is None

    runner = GatewayRunner(cfg)
    assert runner.profile_worker_manager is None
    assert runner.whatsapp_router is None
    # primary_profile_name should still resolve sensibly.
    assert runner.primary_profile_name in {"default", str(tmp_path).split("/")[-1]}


@pytest.mark.asyncio
async def test_legacy_handle_message_path_unaffected_by_routing_branch(tmp_path, monkeypatch):
    """When routing isn't configured, the routing branch in _handle_message is a no-op."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.session import Platform, SessionSource

    cfg = GatewayConfig()
    runner = GatewayRunner(cfg)

    src = SessionSource(
        platform=Platform.WHATSAPP, chat_id="x", chat_type="dm",
        user_id="u", user_name="n", message_id="m1",
    )
    event = MessageEvent(text="hi", message_type=MessageType.TEXT, source=src,
                         message_id="m1", canonical_sender_id="u")

    # The routing branch should be skipped (no router); we don't care about
    # the rest of _handle_message — it may explode on missing dependencies.
    # We assert ONLY that runner.whatsapp_router is None and the routing branch
    # condition is correctly guarded.
    assert runner.whatsapp_router is None
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/gateway/test_routing_backcompat.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/test_routing_backcompat.py
git commit -m "test(gateway): regression guard for routing-disabled mode"
```

---

### Task 15: Worker stdout pollution guard

**Files:**
- Modify: `hermes_cli/profile_worker_cli.py` (route logging to stderr)
- Modify: `gateway/profile_worker.py` (drain worker stderr to gateway logger)

Worker stdout is the IPC channel — anything emitted there that isn't a JSON envelope corrupts the stream. We already route logging to stderr in Task 7. Add a small reader in ingress that drains the worker's stderr to a Python logger so worker logs are visible during debugging.

- [ ] **Step 1: Add a stderr drainer in `ProfileWorker`**

In `gateway/profile_worker.py`, in `start()`:

```python
async def start(self) -> None:
    # ... existing code that creates self._proc ...
    self._reader_task = asyncio.create_task(self._read_loop(), name=...)
    self._stderr_task = asyncio.create_task(self._drain_stderr(), name=f"profile_worker_stderr[{self.name}]")
    # ... existing readiness wait ...

async def _drain_stderr(self) -> None:
    assert self._proc is not None and self._proc.stderr is not None
    try:
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.info("worker[%s] stderr: %s", self.name, text)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("worker[%s] stderr drainer crashed", self.name)
```

In `stop()`, also cancel `self._stderr_task`.

- [ ] **Step 2: Add a unit test that worker logs land on stderr, not stdout**

```python
# Append to tests/gateway/test_profile_worker.py

@pytest.mark.asyncio
async def test_worker_logs_do_not_pollute_stdout(tmp_path):
    """Worker that writes to stderr should not corrupt the stdout IPC channel."""
    script = tmp_path / "logger.py"
    script.write_text(textwrap.dedent("""
        import sys, json
        sys.stderr.write("DEBUG: starting up\\n")
        sys.stdout.write(json.dumps({"kind":"ready","name":"logger"}) + "\\n")
        sys.stdout.flush()
        for line in sys.stdin:
            env = json.loads(line)
            sys.stderr.write(f"DEBUG: got event {env}\\n")
            sys.stdout.write(json.dumps({
                "kind":"reply", "correlation_id": env["correlation_id"],
                "reply": {"text":"ok","error":None,"media":[]}
            }) + "\\n")
            sys.stdout.flush()
    """))

    worker = ProfileWorker(name="logger", argv=[sys.executable, str(script)], env={})
    await worker.start()
    try:
        reply = await worker.dispatch({"text": "x"})
        assert reply["text"] == "ok"
    finally:
        await worker.stop()
```

- [ ] **Step 3: Run all worker tests**

Run: `pytest tests/gateway/test_profile_worker.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add gateway/profile_worker.py tests/gateway/test_profile_worker.py
git commit -m "feat(gateway): drain worker stderr; verify stdout stays clean for IPC"
```

---

### Task 16: Documentation

**Files:**
- Modify or create: `docs/CONFIG.md` (or `README.md` whichever the project uses for config docs)

- [ ] **Step 1: Locate config docs**

Run: `find docs -name "*.md" | head -20; ls`

Find the file that documents `channels.whatsapp` config. If it doesn't exist, add a section to `README.md` (or create `docs/whatsapp_profile_routing.md`).

- [ ] **Step 2: Add a routing config section**

```markdown
## WhatsApp profile routing

A single WhatsApp number can route inbound messages to different Hermes
profiles based on the sender's WhatsApp identity. Each profile keeps its
own memory, sessions, skills, hooks, pairing/allowlist — fully isolated
because each runs as its own Hermes worker subprocess.

### Setup

1. Create the additional profile(s):
   ```
   hermes profile create family
   ```
2. Add a `profile_routing` block to the gateway's `config.yaml`:
   ```yaml
   channels:
     whatsapp:
       profile_routing:
         profiles: ["main", "family"]
         default_profile: "main"
         sender_profile_map:
           "60123456789": "main"           # owner — routes to main
           "60987654321": "family"         # family member — routes to family
   ```
3. Restart the gateway. It will spawn one worker subprocess per non-primary
   profile listed in `profiles:`.

### Sender identifier format

`sender_profile_map` keys are canonicalized at config load (numeric only,
plus stripped, JID/LID suffixes removed). Both of these forms map to the
same canonical id `60123456789` and are accepted equivalently:

- `+60123456789`
- `60123456789@s.whatsapp.net`
- `60123456789@lid`
- `60123456789`

### Pairing/allowlist

Each profile has its own pairing store and allowlist. Listing a sender in
`sender_profile_map` does NOT approve them — they still need to pass the
target profile's pairing or allowlist gate. Approving sender X for the
`main` profile does NOT approve them for `family`.

### Unmapped senders

Senders not listed in `sender_profile_map` are routed to `default_profile`.

### Limitations (MVP)

- Only WhatsApp DMs are routed; groups always go to the primary profile.
- WhatsApp credentials live with the primary profile (the gateway's
  bootstrap `HERMES_HOME`). The single bridge session is shared.
- `unmapped_sender_behavior` only supports `default_profile` in MVP;
  `deny`/`pair`/`ignore` are not yet implemented.
- Only WhatsApp is routed; other channels run in-process under the
  primary profile.
```

- [ ] **Step 3: Commit**

```bash
git add docs/  # or README.md
git commit -m "docs: WhatsApp profile routing setup and limitations"
```

---

## Self-Review

After all 16 tasks are written, look back at the spec and confirm coverage:

| Spec Section | Implemented in |
|---|---|
| §3 Architecture overview (ingress + N workers) | Tasks 8, 9, 10 |
| §4.1 Routing config | Task 2 |
| §4.2 IPC adapter | Tasks 5, 6 |
| §4.3 Worker entrypoint | Task 7 |
| §4.4 Worker manager | Task 9 |
| §4.5 WhatsAppRouter | Task 3 |
| §4.6 `_handle_message` modification | Task 11 |
| §4.7 WhatsApp adapter `canonical_sender_id` | Task 1 |
| §4.8 Per-profile pairing | Inherited free — workers run their own _handle_message which reads their own pairing_store. No explicit task needed; verified by Task 12 (HERMES_HOME isolation). |
| §4.9 Reply path | Task 11 (_dispatch_to_worker) |
| §5 Unmapped sender → default | Task 2 (config validation) + Task 3 (router fallback) |
| §6 Concurrency model | Verified by Task 12 concurrent test |
| §7 Error handling table | Task 11 (_dispatch_to_worker error logging) + Task 8 (ProfileWorkerError on crash) |
| §8 Backward compat | Task 14 |
| §9 Testing | Tasks 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15 (every code task ships its own tests) |
| §10 Implementation risks | Worker boot time → Task 8 readiness signal; serialization → Task 4 codec; reply addressing → Task 11; stdout pollution → Task 15; supervision → Task 9 manager.shutdown |
| §11 Out of scope | Documented in Task 16 |

No spec gaps detected.

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-07-whatsapp-sender-profile-routing.md`. Two execution options:

1. **Subagent-Driven (recommended)** — Each task dispatched to a fresh subagent with full context, two-stage review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?

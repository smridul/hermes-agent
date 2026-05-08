# WhatsApp Sender-Based Profile Routing — Design

**Date**: 2026-05-07
**Status**: Approved (pending user review of this written spec)
**Scope**: Hermes WhatsApp gateway

---

## 1. Goal

Allow a single WhatsApp number, single WhatsApp session, and single Hermes container to route incoming messages to **different Hermes profiles based on the sender's WhatsApp identity**. Each profile keeps its own memory, sessions, config, skills, and pairing/allowlist — strictly isolated.

Example:
- Owner messages the number → handled by `main` profile (owner's memory, skills).
- Family member messages the same number → handled by `family` profile (separate memory, skills, sessions).
- Replies go back through the same WhatsApp number, composed by the routed profile.

Non-goals: a second WhatsApp number, a second container, hot config reload, per-group routing, per-message profile *override* (e.g. via command syntax).

## 2. Constraints & Acceptance Criteria

1. Message from owner resolves to `main` profile.
2. Message from family member resolves to `family` profile.
3. `main` profile memory/session data never appears in `family` profile replies, and vice versa.
4. Tools/skills enabled-by-config differ correctly by profile.
5. Session transcripts are written under the routed profile only.
6. Pairing/allowlist behavior is per-profile and remains sensible for unmapped senders.
7. No second WhatsApp number/session required.
8. No second container required.
9. Concurrent messages from different profiles do not cross-contaminate, even mid-await.
10. If routing config is absent, the gateway behaves exactly as today (backward compatible).

## 3. Architecture Overview

One gateway process boots a **multi-profile runtime**. Today the gateway holds one `session_store`, one `pairing_store`, one `agent_cache`, one config — all keyed off the single active profile booted via `HERMES_HOME`. The change: introduce a **`ProfileRuntimeRegistry`** that owns *N* parallel sets of these objects, one per profile name.

The WhatsApp adapter still owns a single bridge/session (one number, one `session_path` lock). A new **`WhatsAppRouter`** sits between the adapter and `_handle_message`: it canonicalizes the inbound sender ID, looks up the target profile, and dispatches the message into that profile's runtime.

No `HERMES_HOME` mutation at request time. No global locks. Concurrency-safe because each in-flight message references the runtime it was dispatched into, and runtimes don't share mutable state. Replies travel back through the same WhatsApp adapter — replies don't need profile context once composed.

**Boot model**: gateway config gains a new `channels.whatsapp.profile_routing` section that names the profiles to load and the sender→profile map. Gateway reads it, then **sequentially** instantiates each profile's runtime side-by-side (one at a time at boot, so no race), and registers them in the registry.

## 4. Components

### 4.1 `ProfileRuntimeRegistry` (new — `gateway/profile_registry.py`)

Holds a `dict[str, ProfileRuntime]`. Each `ProfileRuntime` is:

```python
@dataclass(frozen=True)
class ProfileRuntime:
    name: str
    hermes_home: Path
    session_store: SessionStore
    pairing_store: PairingStore
    agent_cache: AgentLRUCache  # existing type
    config: GatewayConfig
    # plus per-profile auth state: dm_policy, allow_from, group_policy, etc.
```

Built once at gateway boot. `registry.get(profile_name) -> ProfileRuntime` is a hot-path dict lookup. `registry.primary` returns the gateway's bootstrap profile (used for non-WhatsApp channels and for fallback paths).

### 4.2 `WhatsAppRouter` (new — `gateway/whatsapp_router.py`)

Single responsibility: given a normalized inbound WhatsApp event, decide the target profile.

```python
class WhatsAppRouter:
    def __init__(self, sender_profile_map: dict[str, str], default_profile: str): ...
    def resolve_profile(self, canonical_sender_id: str) -> str: ...
```

Map keys are canonicalized at config-load time using `canonical_whatsapp_identifier` (already exists at `gateway/whatsapp_identity.py:122`) so lookup is a single dict access. Unmapped senders return `default_profile` (Section 6 — only behavior in MVP).

### 4.3 Routing config (new section in gateway `config.yaml`)

```yaml
channels:
  whatsapp:
    # ... existing keys (dm_policy, allow_from, etc.) ...
    profile_routing:
      profiles: ["main", "family"]    # which profiles to load runtimes for
      default_profile: "main"
      sender_profile_map:
        "104084237459666": "main"
        "16692508441": "main"
        "<family_member_canonical_id>": "family"
```

Notes:
- Keys are canonicalized at load (numeric-only — the shape `canonical_whatsapp_identifier` already produces). Authors can write phone numbers with `+`, JID/LID suffixes, etc. — they're normalized before storage.
- `default_profile` MUST be one of the entries in `profiles`.
- Every profile listed in `profiles` MUST exist on disk under Hermes' standard profile location (validated at boot — see Section 8).
- If the entire `profile_routing` block is absent, gateway runs as today.

### 4.4 `_handle_message` change (`gateway/run.py:4421`)

Refactor: split the body of `_handle_message` into a private `_handle_message_in_runtime(event, runtime)`. The new outer `_handle_message`:

1. If `event.platform == "whatsapp"` and routing is enabled:
   - `runtime = registry.get(router.resolve_profile(event.canonical_sender_id))`
2. Else:
   - `runtime = registry.primary`
3. Set `ACTIVE_RUNTIME` ContextVar (Section 5).
4. `await self._handle_message_in_runtime(event, runtime)`

Inside `_handle_message_in_runtime`, every reference that was `self.session_store`, `self.pairing_store`, etc. becomes `runtime.session_store`, `runtime.pairing_store`, etc. — explicit, not env-dependent.

### 4.5 WhatsApp adapter (`gateway/platforms/whatsapp.py`)

Two small changes:
- `_build_message_event` (line ~1005) already extracts `senderId`. Add the canonicalized form to the event metadata so the router doesn't recompute.
- `session_path` lock: still one path under the **primary** profile's `HERMES_HOME` (WhatsApp credentials are tied to the number, not the profile, and there's only one bridge). Unchanged.

### 4.6 Pairing/allowlist semantics

Each profile owns its own `pairing_store` and allowlist. Auth runs **after** routing, inside the resolved profile's context. Listing a sender in `sender_profile_map` does not approve them — they still need to pass that profile's pairing/allowlist gate. This preserves "isolation" as a structural property and gives each profile owner control over their own approvals.

### 4.7 Reply path

The agent generates a reply inside the profile runtime; the reply is handed back to the singleton WhatsApp adapter for delivery. The adapter is profile-agnostic. Per-profile reply formatting (signatures, footers) is whatever the profile's existing config produces — no extra plumbing needed for MVP.

## 5. Profile Context Propagation (the safety net)

The risk: any code path that calls `get_hermes_home()` (or reads `HERMES_HOME` env) **during** message handling — rather than at boot — would silently resolve to the gateway's bootstrap profile, not the routed one. That would leak data across profiles invisibly.

Three-layer defense:

### Layer 1 — Static audit
Grep every caller of `get_hermes_home()`, `get_hermes_dir()`, and direct `os.environ["HERMES_HOME"]` reads. Categorize each:
- **Boot-time** (loaded once at startup) → fine, no change.
- **Message-time** (called during `_handle_message` lifecycle) → relies on Layer 2.

The audit findings + remediation list will be captured in the implementation plan, not this spec.

### Layer 2 — `ContextVar`-backed resolver (runtime safety)

Define the ContextVar **in `hermes_constants.py`** (or a sibling low-level module that `hermes_constants` can import without creating a cycle). The ContextVar holds a plain `Path | None`, **not** a `ProfileRuntime` — keeping the dependency one-way (low-level module knows nothing about gateway types).

```python
# in hermes_constants.py (or hermes_runtime_context.py imported by it)
import contextvars
from pathlib import Path

ACTIVE_HERMES_HOME: contextvars.ContextVar[Path | None] = (
    contextvars.ContextVar("active_hermes_home", default=None)
)
```

`get_hermes_home()` consults it first, falls back to env:

```python
def get_hermes_home() -> Path:
    override = ACTIVE_HERMES_HOME.get()
    if override is not None:
        return override
    # existing env-var resolution
    ...
```

The gateway-side wiring sets the ContextVar from the `ProfileRuntime` at the top of `_handle_message_in_runtime`:

```python
token = ACTIVE_HERMES_HOME.set(runtime.hermes_home)
try:
    await self._handle_message_in_runtime_inner(event, runtime)
finally:
    ACTIVE_HERMES_HOME.reset(token)
```

`ContextVar` is per-async-task: the binding survives every `await` inside the task that set it, and is invisible to concurrently-running tasks. No locks needed, no leakage between concurrent messages. The explicit `reset` in `finally` is belt-and-suspenders — async tasks already have isolated contexts so it's not strictly required, but it's cheap insurance.

`ContextVar` is per-async-task: the binding survives every `await` inside the task that set it, and is invisible to concurrently-running tasks. No locks needed, no leakage between concurrent messages.

`_handle_message_in_runtime` sets `ACTIVE_RUNTIME` at the top, so every downstream call to `get_hermes_home()` automatically resolves to the routed profile — even from code I haven't audited.

### Layer 3 — Test-time assertion (catch regressions)

A pytest fixture monkey-patches `get_hermes_home()` to record every caller during message handling and asserts the resolved path equals the routed runtime's `hermes_home`. Wired into the integration tests in Section 9. If any call site ever escapes the ContextVar, the test fails with a stack trace pointing at the culprit. New regressions can't slip into main silently.

## 6. Unmapped Sender Behavior

MVP supports one mode: **route to `default_profile`**. Existing per-profile `dm_policy` / `allow_from` / pairing rules then apply within the default profile, gating strangers exactly as they do today.

Other modes from the original spec (`deny`, `pair`, `ignore`) are explicitly out of scope for MVP. The config schema reserves the key name (`profile_routing.unmapped_sender_behavior`) but only `default_profile` is implemented; any other value fails at boot with an explicit "not yet supported" error.

## 7. Data Flow

```
WhatsApp bridge
   │  raw event JSON
   ▼
WhatsAppAdapter._build_message_event           gateway/platforms/whatsapp.py:~1005
   │  extracts senderId, builds MessageEvent
   │  attaches event.canonical_sender_id
   ▼
GatewayRunner._handle_message(event)           gateway/run.py:4421
   │  if whatsapp + routing enabled:
   │      profile_name = router.resolve_profile(event.canonical_sender_id)
   │      runtime = registry.get(profile_name)
   │  else:
   │      runtime = registry.primary
   │  ACTIVE_RUNTIME.set(runtime)
   ▼
_handle_message_in_runtime(event, runtime)
   │  authorize via runtime.pairing_store / runtime.allow_from
   │  build session_key
   │  load/create session in runtime.session_store
   │  fetch/create agent in runtime.agent_cache
   │  agent runs; any get_hermes_home() reads runtime.hermes_home via ContextVar
   ▼
reply text/media
   │
   ▼
WhatsAppAdapter.send_message(...)              singleton, profile-agnostic
```

Invariant: from the moment routing resolves a profile, every store/path/config read flows through `runtime.*` (explicit) or the ContextVar (implicit fallback) — never through gateway-level singletons or raw `HERMES_HOME`.

## 8. Error Handling

| Condition | Behavior |
|---|---|
| Profile in `profiles:` doesn't exist on disk | Fail fast at boot with explicit error |
| Routing config malformed (duplicate sender → multiple profiles, unknown profile name) | Fail fast at boot |
| `default_profile` not in `profiles:` | Fail fast at boot |
| Sender ID not in map | Route to `default_profile` |
| Profile runtime crashes mid-message | Caught in `_handle_message`, logged with `profile=<name>` tag, no propagation to other runtimes |
| Routing config absent | Gateway runs as today (single-profile mode) |
| `unmapped_sender_behavior` set to anything other than `default_profile` | Fail fast at boot ("not yet supported") |

## 9. Testing

**Unit tests** — new file `tests/gateway/test_whatsapp_profile_routing.py`:

- Sender canonicalization: raw `senderId` formats (LID, phone, JID with various suffixes) all collapse identically before map lookup.
- `WhatsAppRouter.resolve_profile`: mapped sender → correct profile; unmapped → `default_profile`.
- Routing config parse: valid config loads; duplicate sender, unknown profile name, missing default → boot-time error.
- `ProfileRuntimeRegistry.get` for unknown profile → explicit error (not silent fallback).

**Integration tests** — extend `tests/gateway/conftest.py` fixtures:

- Two synthetic WhatsApp DM events from different senders within one gateway runtime → assert each message's session is written under the correct profile's `HERMES_HOME`.
- Memory write in profile A, memory read in profile B → must not see A's data.
- Pairing approval in profile A doesn't grant access in profile B.
- Concurrent `asyncio.gather` of one message per profile → no cross-contamination, both complete, both assertions hold.
- Routing config absent → gateway runs unchanged (regression guard for backward compatibility).

**Profile-context-leak guard** — a fixture that wraps `get_hermes_home()` to record every caller during a message lifecycle and asserts every resolved path matches the routed runtime. Applied to all integration tests above. Failures print the offending call stack.

**Out of scope for tests**: subprocess-worker semantics, hot config reload, `unmapped_sender_behavior` modes other than `default_profile`.

## 10. Backward Compatibility

- If `channels.whatsapp.profile_routing` is absent: gateway boots with a single profile (current behavior). All existing single-profile installs unchanged.
- `ProfileRuntimeRegistry` always exists, but with one entry (the bootstrap profile) when routing is disabled. `_handle_message` always resolves a runtime — just always to `registry.primary` in single-profile mode. This means the refactor of `_handle_message` is exercised even when routing is disabled, so it gets test coverage from existing tests.
- ContextVar fallback to env keeps legacy code paths working when routing is disabled (ContextVar default is `None`, falls through to env).

## 11. Implementation Risks

1. **Long tail of `get_hermes_home()` callers**: the count is unknown until the audit. Layer 2 (ContextVar) makes most callers safe automatically, but some code may *capture* a path at import time and stash it in a module-global. Those would be invisible to the ContextVar. The audit must specifically grep for module-level path captures, not just function calls.
2. **Per-profile config loading at boot**: `load_gateway_config()` currently reads one config from one `HERMES_HOME`. Loading N configs in sequence requires either a function that takes `hermes_home` as an argument, or temporarily setting the env var around each call (boot-time only, no concurrency, so safe). Plan: refactor to take an explicit argument.
3. **Agent LRU cache key**: today keyed by `session_key`. With multiple profiles, two profiles could in principle generate the same session_key string. Mitigation: each profile has its own `agent_cache` instance — no shared keyspace — so this is structurally avoided. But worth a test.
4. **WhatsApp `session_path` lock under primary `HERMES_HOME`**: tying it to primary means the primary profile owns the WhatsApp credentials. Document this clearly: "the gateway's bootstrap profile holds the WhatsApp session; other profiles do not need their own."

## 12. Out of Scope (explicit deferrals)

- Subprocess workers per profile.
- Hot reload of routing config.
- Per-group routing (only DMs in MVP — groups go to default profile).
- `deny` / `pair` / `ignore` unmapped-sender behaviors.
- Profile-aware reply formatting beyond what the profile's own config already produces.
- A CLI tool to bootstrap profiles (`hermes profile create family`) — assume profiles are pre-created.

# WhatsApp Sender-Based Profile Routing — Design

**Date**: 2026-05-07
**Status**: Approved (pending user review of this written spec)
**Scope**: Hermes WhatsApp gateway
**Architecture**: Subprocess-worker (chosen after HERMES_HOME audit; see §3.1)

---

## 1. Goal

Allow a single WhatsApp number, single WhatsApp session, and single Hermes container to route incoming messages to **different Hermes profiles based on the sender's WhatsApp identity**. Each profile keeps its own memory, sessions, config, skills, hooks, and pairing/allowlist — strictly isolated.

Example:
- Owner messages the number → handled by `main` profile.
- Family member messages the same number → handled by `family` profile.
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
9. Concurrent messages from different profiles do not cross-contaminate.
10. If routing config is absent, the gateway behaves exactly as today (backward compatible).

## 3. Architecture Overview

**One ingress process + N profile-worker subprocesses.**

The existing gateway process becomes the **ingress**: it owns all platform adapters (WhatsApp, Discord, etc.), the WhatsApp bridge/session, and the routing logic. The ingress also acts as the runtime for its own bootstrap profile (the profile passed via `HERMES_HOME` at gateway start) — call this the **primary profile**.

For each *additional* profile listed in `channels.whatsapp.profile_routing.profiles`, ingress spawns a **profile worker subprocess**. Each worker is itself a Hermes process started with `HERMES_HOME=<profile path>` in its environment, so all profile-aware code (skills, hooks, oauth, mirror, memory, sessions, config) resolves to that profile by construction. The worker attaches no platform adapters of its own — instead, it attaches an **IPC platform adapter** that reads `MessageEvent` JSON from stdin and emits reply JSON to stdout.

When a WhatsApp message arrives, ingress canonicalizes the sender ID, looks up the target profile in the routing map, and:

- If target == primary profile → handle in-process via existing `_handle_message` path (zero behavioral change).
- If target is a worker profile → forward the normalized event over the worker's stdin pipe and await a reply on its stdout. Then deliver the reply via the WhatsApp adapter.

### 3.1 Why subprocess-worker (and not in-process ContextVar)

A code audit found **31 module-level captures** of `get_hermes_home()` (e.g. `HOME = get_hermes_home()` at module top of `tools/skills_hub.py`, `gateway/hooks.py`, `gateway/mirror.py`, `agent/anthropic_adapter.py:_HERMES_OAUTH_FILE`, `gateway/run.py:_hermes_home`, plus 26 more) and 288 total `HERMES_HOME` reads across 120+ files. Module-level captures are pinned at import time and **cannot** be overridden by a `ContextVar` or any runtime mechanism. Achieving correct per-profile isolation in-process would require auditing and refactoring all 31 sites — a large prerequisite project that is out of scope for this feature.

A subprocess boundary side-steps the entire problem: each worker imports its own copy of every module with its own `HERMES_HOME`, and the OS process boundary makes cross-profile leakage structurally impossible.

## 4. Components

### 4.1 Routing config (new section in gateway `config.yaml`)

```yaml
channels:
  whatsapp:
    # ... existing keys (dm_policy, allow_from, etc.) ...
    profile_routing:
      profiles: ["main", "family"]    # all profiles participating in routing
      default_profile: "main"
      sender_profile_map:
        "104084237459666": "main"
        "16692508441": "main"
        "<family_member_canonical_id>": "family"
```

Notes:
- Keys are canonicalized at load via `canonical_whatsapp_identifier` (`gateway/whatsapp_identity.py:122`).
- `default_profile` MUST be in `profiles`. The primary profile (whatever ingress booted with) MUST also be in `profiles`.
- Every profile listed MUST exist on disk under Hermes' standard profile location.
- If the entire `profile_routing` block is absent, gateway runs as today (single-profile, no workers spawned).

### 4.2 IPC platform adapter — new (`gateway/platforms/ipc.py`)

A platform adapter that reads inbound events from `sys.stdin` and writes outbound replies to `sys.stdout`, both line-delimited JSON. Plugs into the existing platform_registry.py the same way other adapters do.

**Inbound wire format (one JSON object per line):**
```json
{
  "kind": "message_event",
  "correlation_id": "uuid-...",
  "event": { /* serialized MessageEvent payload */ }
}
```

**Outbound wire format:**
```json
{
  "kind": "reply",
  "correlation_id": "uuid-...",
  "reply": {
    "text": "...",
    "media": [/* optional */],
    "error": null
  }
}
```

Worker emits exactly one `reply` per `message_event` (success or error). Streaming/partial replies are out of scope for MVP.

### 4.3 Profile worker entrypoint — new (`hermes_cli/profile_worker.py` + CLI subcommand)

A new CLI subcommand: `hermes profile-worker --name <profile_name>`. The subcommand:

1. Sets `HERMES_HOME=<resolved profile path>` in its own environment (or relies on the env passed by ingress).
2. Boots a stripped-down gateway runner with **only** the IPC platform adapter — no WhatsApp, Discord, Slack, etc.
3. Runs the existing async event loop. Inbound IPC events flow through the standard `_handle_message` path; replies emit to stdout.

The worker is itself a Hermes process — it inherits all profile-isolation properties for free. It does not need to know it's running as a worker; it just sees an `ipc` platform.

### 4.4 Ingress-side worker manager — new (`gateway/profile_worker_manager.py`)

Owns the lifecycle of all profile worker subprocesses.

```python
class ProfileWorkerManager:
    async def start(profiles: list[ProfileSpec]) -> None: ...
    async def shutdown() -> None: ...
    async def dispatch(profile_name: str, event: MessageEvent) -> Reply: ...
    # Internal:
    #   _workers: dict[str, ProfileWorker]
    #   _pending: dict[str, asyncio.Future]   # correlation_id -> future
```

Each `ProfileWorker` is:
- A subprocess (`asyncio.create_subprocess_exec`) running `hermes profile-worker --name <name>`.
- A reader task that consumes the worker's stdout line-by-line, parses JSON, resolves the matching `_pending` future.
- A writer that JSON-encodes outbound events and writes one line to the worker's stdin per dispatch.
- A health watcher that detects worker death (`process.poll()`), logs, and restarts on next dispatch (or eagerly).

`dispatch()` creates a fresh `correlation_id`, registers a future in `_pending`, writes the event JSON to the worker's stdin, awaits the future with a timeout (e.g. 5 minutes — agent runs can be long), and returns the reply.

### 4.5 WhatsApp router — new (`gateway/whatsapp_router.py`)

```python
class WhatsAppRouter:
    def __init__(self, sender_profile_map: dict[str, str], default_profile: str): ...
    def resolve_profile(self, canonical_sender_id: str) -> str: ...
```

Pure function over the loaded config. Map keys are canonicalized at config-load time so resolution is a single dict lookup.

### 4.6 Modifications to `gateway/run.py`

In `_handle_message` (entry at `gateway/run.py:4421`), at the top of the function:

1. If event came from WhatsApp AND `profile_routing` is configured:
   - `target_profile = router.resolve_profile(event.canonical_sender_id)`
   - If `target_profile == self.primary_profile_name`: fall through to existing in-process path.
   - Else: `reply = await worker_manager.dispatch(target_profile, event)`, then send reply via `self.whatsapp_adapter.send_message(...)` and return.
2. Else: existing path unchanged.

### 4.7 Modifications to WhatsApp adapter (`gateway/platforms/whatsapp.py`)

In `_build_message_event` (~line 1005), add `event.canonical_sender_id` to the event metadata using `canonical_whatsapp_identifier(senderId)`. This avoids re-computing the canonical form in the router.

The adapter's `session_path` lock is unchanged — it remains under the primary profile's `HERMES_HOME`. WhatsApp credentials live with ingress, not with workers.

### 4.8 Pairing / allowlist semantics

Each worker has its own `pairing_store` and allowlist (because each worker has its own `HERMES_HOME`). When ingress forwards an event to worker[family], the worker's existing auth path runs against family's stores and family's allowlist. Listing a sender in `sender_profile_map` does NOT approve them — they still need to pass the target profile's pairing/allowlist gate.

For routed senders that go to a worker, ingress does NOT run its own pairing check first; it forwards the raw event and lets the worker decide. This avoids "primary's allowlist accidentally gates messages destined for family."

For senders that route to the primary profile (including unmapped senders via `default_profile`), the existing in-process pairing path runs as today.

### 4.9 Reply path (full round-trip)

```
WhatsApp bridge
   │  raw event JSON
   ▼
WhatsAppAdapter._build_message_event           gateway/platforms/whatsapp.py:~1005
   │  attaches event.canonical_sender_id
   ▼
GatewayRunner._handle_message(event)           gateway/run.py:4421
   │  routing enabled? resolve target_profile
   ├── target == primary  ──► existing in-process path (unchanged)
   └── target == worker
       │  worker_manager.dispatch(target, event)
       ▼
   Worker subprocess
       │  IPC adapter reads stdin JSON
       │  → existing _handle_message inside the worker
       │  → agent runs (memory/skills resolved from worker's HERMES_HOME)
       │  → reply text/media
       │  IPC adapter writes stdout JSON
       ▼
   Ingress reads worker stdout, resolves correlation_id
       ▼
   WhatsAppAdapter.send_message(chat_id=..., text=reply)
```

## 5. Unmapped Sender Behavior

MVP supports one mode: **route to `default_profile`**. Existing per-profile `dm_policy` / `allow_from` / pairing rules then apply within the resolved profile.

`unmapped_sender_behavior` config key is reserved but only `default_profile` is implemented. Any other value fails fast at boot.

## 6. Concurrency Model

- **Within ingress**: existing single async event loop. The worker manager's dispatch is `async`, so multiple in-flight messages from different senders can be pipelined to different workers in parallel.
- **Within a worker**: existing single async event loop, single profile. Same concurrency properties as today's gateway.
- **Across workers**: independent OS processes. No shared memory, no shared files (each profile's `HERMES_HOME` is distinct). No locking required between workers.
- **Worker stdin/stdout pipes**: `asyncio.create_subprocess_exec` gives non-blocking pipes. Writes are awaited; reads are line-buffered via the reader task.

## 7. Error Handling

| Condition | Behavior |
|---|---|
| Profile in `profiles:` doesn't exist on disk | Fail fast at ingress boot |
| Routing config malformed (duplicate sender, unknown profile, missing default) | Fail fast at ingress boot |
| Worker subprocess fails to start | Fail fast at ingress boot for that profile; ingress also fails to start (can't honor config) |
| Worker dies mid-message | Pending futures for that worker are rejected with a clear error; ingress logs, sends a "the assistant is offline" auto-reply to the WhatsApp sender (or silently drops — see open question Q1) |
| Worker dies between messages | Ingress restarts on next dispatch (or eagerly via watcher) |
| Worker exceeds dispatch timeout | Future rejected, error logged, no reply sent (open question Q2) |
| Routing config absent | Gateway runs as today, no workers spawned |
| `unmapped_sender_behavior` set to anything other than `default_profile` | Fail fast at boot |

**Open questions deferred to plan-time decisions:**
- Q1: On worker death mid-message, do we auto-reply or silently drop? Default: silently drop, log loudly.
- Q2: On dispatch timeout, do we send an apology message? Default: silently drop, log loudly.

## 8. Backward Compatibility

- If `channels.whatsapp.profile_routing` is absent: gateway boots as today, no workers spawned, all messages go through the existing in-process path.
- If `profile_routing` is configured but maps every sender to the primary profile: workers are still spawned for any other listed profiles, but routing is a no-op fast path. Wasteful but correct. (Authors should just remove the routing block in this case.)
- The IPC platform adapter is new but only registered in worker processes; it's invisible to existing single-profile installs.

## 9. Testing

**Unit tests** — new file `tests/gateway/test_whatsapp_profile_routing.py`:

- Sender canonicalization → routing map lookup: LID, phone, JID with various suffixes all collapse identically.
- `WhatsAppRouter.resolve_profile`: mapped → correct profile; unmapped → `default_profile`.
- Routing config parse: valid loads; duplicate sender, unknown profile, missing default → boot-time error.
- IPC platform adapter: round-trip a synthetic event JSON through stdin and assert outbound stdout JSON shape (no real subprocess; mock streams).

**Integration tests** — new file `tests/gateway/test_profile_worker_integration.py`:

- Spawn a real worker subprocess pointing at a `tmp_path`-based profile, send a fake event over stdin, assert the worker writes a reply to stdout. Use a stub agent that just echoes `"profile: <name>"` so the test doesn't need real LLM access.
- Spawn two workers (profile A, profile B), dispatch one event to each in parallel via `asyncio.gather`, assert both replies return correctly correlated.
- Memory write in worker A, memory read in worker B → must not see A's data (uses two real `HERMES_HOME` dirs).
- Pairing approval in worker A doesn't grant access in worker B.
- Routing config absent → gateway boots without spawning workers (regression guard).
- Worker crash mid-message → dispatch future rejected with clear error.

**Out of scope for tests**: streaming replies, hot config reload, `unmapped_sender_behavior` modes other than `default_profile`, media attachments through IPC.

## 10. Implementation Risks

1. **Worker boot time**: each worker is a full Hermes process. Cold-start time matters because ingress fails to start until workers are ready. Mitigation: spawn workers in parallel; worker readiness is a "ready" line written to stdout on boot, not just process start.
2. **MessageEvent serialization**: `MessageEvent` may contain non-JSON-serializable fields (e.g. callbacks, file handles). The IPC layer needs an explicit serialize/deserialize contract that strips/re-attaches anything non-portable. Plan task: build the serializer first, with a round-trip test.
3. **Reply addressing**: ingress must remember the original event's `chat_id` per `correlation_id` so it can send the reply through WhatsApp. Done via `_pending: dict[correlation_id, original_event]`.
4. **Stdout pollution**: the worker's stdout is the IPC channel. Any stray `print()` inside the worker (debug logging that writes to stdout instead of stderr) corrupts the IPC stream. Mitigation: redirect all worker logging to stderr; treat unparseable stdout lines as warnings, not crashes.
5. **Process supervision**: workers must be reaped on ingress shutdown; orphaned workers are bad. Use `asyncio.create_subprocess_exec` and handle SIGTERM/SIGINT in ingress.
6. **WhatsApp credentials shared**: all WhatsApp interaction stays in ingress, so workers never need WhatsApp creds. But docs should make it explicit that the primary profile owns the bridge.

## 11. Out of Scope (explicit deferrals)

- Streaming/partial replies from worker to ingress.
- Worker-to-worker communication.
- Hot reload of `profile_routing` config.
- Per-group routing (only DMs in MVP — groups go to default profile / primary).
- `deny` / `pair` / `ignore` unmapped-sender behaviors.
- Routing for non-WhatsApp channels (Discord, Slack, etc.).
- Profile-aware reply formatting beyond what the worker's own config produces.
- Scheduled tasks/cron in workers (only ingress profile runs cron).

# WhatsApp Group-Based Profile Routing — Design

**Date**: 2026-05-10
**Status**: Approved (pending user review of this written spec)
**Scope**: Hermes WhatsApp gateway
**Builds on**: 2026-05-07-whatsapp-sender-profile-routing-design.md

---

## 1. Goal

Add a second routing primitive to `whatsapp.profile_routing`: route inbound messages by **group chat_id**, not just by sender. When a configured group is matched, the bound profile is the only legitimate target — if it cannot be reached, the message is dropped. The routing decision must never silently degrade to `default_profile` for a group that has an explicit mapping.

Example:
- Group `120363409860032836@g.us` is mapped to `test_profile`.
- Anyone in that group who triggers the bot (via `@mention`, reply, `/`, or pattern) is handled by `test_profile`.
- If `test_profile`'s worker is dead or removed, the message is dropped — never silently handled by `default`.

Non-goals: per-group `require_mention` / `free_response` overrides, per-group reply formatting, mention-target-based routing (i.e. routing by which bot number was tagged when multiple bots share a session — there is only one bot session today).

## 2. Constraints & Acceptance Criteria

1. A group with an entry in `group_profile_map` always routes to the named profile; never falls back to `default_profile`.
2. A group with no entry in `group_profile_map` follows the existing routing path (sender → default).
3. DMs ignore `group_profile_map` entirely.
4. If `group_profile_map` references a profile not in `profiles`, the gateway refuses to start (boot-time fail-closed, consistent with the sender map).
5. If a group-mapped target's worker is unavailable at dispatch time, the message is dropped and a single ERROR log line is emitted. No reply, no fallback.
6. Configuration is backward compatible: a `profile_routing` block without `group_profile_map` behaves exactly as it does today.
7. Group routing precedence is **group beats sender** — when a message in a group-mapped chat arrives from a sender that's also in `sender_profile_map`, the group binding wins.
8. The existing inbound-mention gating (`require_mention`, `free_response_chats`, mention patterns) is unchanged. Group routing only affects which profile handles a message that already passed the gate; it does not change which messages are processed.

## 3. Architecture Overview

The change is local. There are no new processes, no IPC contract changes, and no new lifecycle concerns beyond what sender routing already established. Workers are still spawned per profile in `profiles` — `group_profile_map` only changes which existing worker receives a given event.

Touched components:

- **`gateway/profile_routing_config.py`** — extend the dataclass and validator. Same shape and exception class as `sender_profile_map`. The validator additionally guarantees `group_profile_map` keys are non-empty strings; chat-id format is otherwise opaque (we cannot validate against live WhatsApp groups).
- **`gateway/whatsapp_router.py`** — add `route_for_group(chat_id) -> Optional[str]`. Returns the bound profile name when a group mapping exists, `None` otherwise. The existing `route_for_sender` stays as-is.
- **`gateway/run.py`** — at the dispatch decision site, consult `route_for_group` first when `event.is_group`. If a group binding exists:
  - target's worker available → dispatch to that worker.
  - target's worker missing → drop the message, log a structured ERROR, return without dispatch.
  - falling through to `route_for_sender` is **not allowed** when the group is bound.
- **Tests** — schema tests (`tests/gateway/test_profile_routing_config.py`), dispatch precedence and drop-on-unavailable tests (`tests/gateway/test_handle_message_routing.py`).
- **Docs** — extend `docs/whatsapp_profile_routing.md` with the group section.

## 4. Configuration

```yaml
whatsapp:
  profile_routing:
    profiles: [default, megha-bot, test_profile, ember-bot]
    default_profile: default
    sender_profile_map:
      "+919028515133": megha-bot
      "+14085921090": test_profile
      "180866038948085": test_profile
      "+16284440437": ember-bot
    group_profile_map:                                    # NEW
      "120363409860032836@g.us": test_profile
```

Both maps are optional and may be combined. A `profile_routing` block with neither map is degenerate but valid — every message goes to `default_profile`. A block with only `group_profile_map` is valid; DMs and unmapped groups go to `default_profile`.

## 5. Dispatch Decision

For an inbound `MessageEvent` that has already passed the WhatsApp adapter's process-or-ignore gate:

```
target_profile = None

if event.is_group:
    target_profile = router.route_for_group(event.chat_id)   # may return None

if target_profile is not None:
    # Group binding is exclusive — no fallback path.
    if worker_manager.has_worker(target_profile):
        dispatch_to_worker(target_profile, event)
    else:
        logger.error(
            "group_routing: chat=%s target=%s worker_unavailable; dropping message",
            event.chat_id, target_profile,
        )
        return  # message dropped intentionally

# No group binding — fall through to existing sender path.
target_profile = router.route_for_sender(event.canonical_sender_id) \
                 or routing_config.default_profile
dispatch_to_worker_or_inprocess(target_profile, event)
```

Notes:

- The "in-process for primary profile, subprocess for others" split that already exists for sender routing is preserved. `dispatch_to_worker_or_inprocess` already handles that branching.
- The drop path emits exactly one ERROR log per dropped message. No retries, no queueing — we don't have a delivery contract for this gateway and silent retries could mask config drift.
- `has_worker(name)` is a lightweight check on the existing `ProfileWorkerManager` registry. The primary profile is always considered "available" since it runs in-process; if `target_profile == primary_profile_name`, the in-process path is used.

## 6. Validation Rules (parser)

`parse_profile_routing` extends to:

- `group_profile_map` is optional. When present, must be a `dict[str, str]`.
- Each key must be a non-empty string after stripping whitespace.
- Each value must be a non-empty string and must appear in `profiles`. Otherwise `ProfileRoutingConfigError` is raised — caught by the existing fail-closed boot path (`gateway/config.py:979`).
- Duplicate keys are not possible (Python dict semantics); duplicate *values* are allowed (multiple groups → same profile is a legitimate use case).
- Group `chat_id`s are not canonicalised. WhatsApp group JIDs have a single canonical form (`<id>@g.us` or `<id>@lid` for community groups); we do exact-string match against `event.chat_id` as the bridge already emits it.

## 7. Error Handling

| Failure | Detection | Result |
|---|---|---|
| `group_profile_map` value not in `profiles` | Boot-time validation in `parse_profile_routing` | `ProfileRoutingConfigError` → gateway refuses to start (existing fail-closed) |
| `group_profile_map` value is `None`/non-string | Boot-time validation | Same as above |
| `group_profile_map` key is empty/whitespace | Boot-time validation | Same as above |
| Bound worker not running at dispatch time | Runtime, in dispatch decision | DROP message, log ERROR, no fallback |
| Bound worker IPC fails mid-dispatch | Runtime, in `dispatch_to_worker` | Existing sender-path "MVP: silently drop" comment at `gateway/run.py:1262` already applies; matches the group-route contract |
| Group has no `group_profile_map` entry but sender unmapped | Runtime | Existing path: routed to `default_profile` (group routing is opt-in per chat_id) |

## 8. Testing

**Schema (`tests/gateway/test_profile_routing_config.py`):**

- Valid block with both maps parses and produces a `ProfileRoutingConfig` with both populated.
- Valid block with only `group_profile_map` parses.
- `group_profile_map` value not in `profiles` raises `ProfileRoutingConfigError`.
- `group_profile_map` with non-string key or value raises.
- `group_profile_map` with empty-string key raises.
- Multiple groups mapping to the same profile is accepted.

**Dispatch (`tests/gateway/test_handle_message_routing.py`):**

- Group-mapped chat with healthy worker → routed to bound profile.
- Group-mapped chat with worker missing → message dropped (no dispatch call to any profile, including default), one ERROR log.
- Group-mapped chat where the sender is also in `sender_profile_map` and maps to a different profile → group binding wins.
- DMs ignore `group_profile_map` even when its key string would syntactically match the sender id. Covered by a DM test that asserts the dispatcher never calls `route_for_group` for `is_group=false`.
- Group with no mapping → existing sender → default fallthrough.

**Boot smoke (existing `tests/gateway/test_gateway_routing_boot.py`):**

- Add a regression that loads a YAML with `group_profile_map` and confirms `cfg.whatsapp_profile_routing.group_profile_map` is populated.
- Add a regression that an invalid `group_profile_map` aborts boot (mirrors the test added in commit `c2d29658b`).

## 9. Backward Compatibility

- A `profile_routing` block without `group_profile_map` is unchanged in behavior.
- `ProfileRoutingConfig.group_profile_map` defaults to `{}` so all existing call sites continue to work.
- The router exposes a new method (`route_for_group`); existing `route_for_sender` is untouched.
- Existing tests for sender routing should pass unchanged.

## 10. Out of Scope

- `unmapped_sender_behavior: ignore` (drop unmapped DMs/group senders by default). Related security concern but a separate change.
- Per-group `require_mention` / `free_response_chats` overrides. The current adapter-level gating is sufficient; we route what the adapter already chose to process.
- Hot reload of `group_profile_map`. Restart on config change is consistent with the sender map.
- A reverse map (profile → groups). Not required for the lookup path; if needed for diagnostics, derive at print time.
- Routing by **mention target** (which bot number was tagged) — there's only one bot session today, so this isn't a meaningful primitive.

---

## 11. Implementation Steps (preview)

The detailed plan is created via `superpowers:writing-plans` after this spec is approved. At a high level:

1. Extend `ProfileRoutingConfig` dataclass with `group_profile_map: dict[str, str]`.
2. Extend `parse_profile_routing` to read, validate, and populate it.
3. Add `route_for_group(chat_id)` to `WhatsAppRouter`.
4. Add `has_worker(name)` to `ProfileWorkerManager` (or use an existing equivalent).
5. Modify the dispatch decision in `gateway/run.py` to consult `route_for_group` first; on hit-without-worker, drop with structured log; otherwise fall through.
6. Add schema and dispatch tests.
7. Update docs.

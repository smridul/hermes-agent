# Profile-Worker Cron Delivery — Known Shortcomings

**Date**: 2026-05-09
**Status**: Recorded for later — not scheduled
**Scope**: Profile-worker subprocesses (introduced by WhatsApp sender-based profile routing, 2026-05-07)
**Related**: `docs/superpowers/specs/2026-05-07-whatsapp-sender-profile-routing-design.md`

---

## Context

When a profile-worker fires a cron job whose `deliver` target is a non-IPC platform (e.g. WhatsApp), `cron.scheduler._deliver_result` looks up the runtime adapter:

```python
runtime_adapter = (adapters or {}).get(platform)
```

Workers register only the IPC adapter (`profile_worker_cli.py`: `cfg.platforms = {Platform.IPC: PlatformConfig(enabled=True)}`), so this lookup misses for every external platform and the code falls through to the **standalone HTTP path** — a fresh `_send_to_platform` call that connects directly to the platform's bridge process (e.g. `localhost:3000` for the WhatsApp Node bridge).

This works most of the time, but it has two problems.

---

## Shortcoming 1 — Standalone delivery has no retry on transient errors

### Symptom
On 2026-05-09 ~05:06, three backlogged one-shot reminder jobs in `test_profile` fired the moment the freshly restarted container's worker came up. All three failed delivery with:

```
WhatsApp send failed: Cannot connect to host localhost:3000
[Errno 111] Connect call failed ('127.0.0.1', 3000)
```

The Python worker had started its cron ticker ~18 seconds after container start. The Node WhatsApp bridge inside the same container was still binding its socket. One TCP attempt → one failure → `mark_job_run(success=True, delivery_error=...)`. Because they are one-shot jobs (`repeat.times=1`), they were popped from `jobs.json` (`cron/jobs.py:707-710`) and never retried. The user received zero messages.

### Root cause
`_send_to_platform` (and `_deliver_result`'s standalone branch) make a single send attempt. There is no transient-error classifier and no backoff. Any momentary unavailability of the bridge — startup race, bridge restart, brief network blip — drops the message permanently for one-shot jobs and triggers a full re-fire on the next interval for recurring jobs.

### Why it's been latent
On the **default profile**, the cron ticker runs inside the main gateway, where `runner.adapters[Platform.WHATSAPP]` is a live, already-connected adapter. `_deliver_result` uses that live adapter — never the standalone path — so the bridge is up by definition. The bug existed in the standalone path forever; it just had no routine consumer.

### Why it surfaced now
The 2026-05-07 profile-worker work created the first routine consumer of the standalone path for cron deliveries: workers don't have a live WhatsApp adapter, so every WhatsApp delivery from a worker uses standalone HTTP.

### Proposed fix (small, safe, helps everyone)
In `tools/send_message_tool._send_to_platform` (or as a thin retry wrapper in `cron/scheduler._deliver_result`):

- Classify connect-refused, connect-timeout, and 5xx as **transient**.
- Retry 2–3 times with exponential backoff (e.g. 1s → 3s → 9s, jittered).
- Cap total retry budget at ~30s so we don't block the cron tick.
- Leave 4xx and auth errors as **terminal** (no retry).

This is a self-contained change in one file, and benefits every platform — not just WhatsApp on container boot.

---

## Shortcoming 2 — Workers bypass the main gateway for non-IPC sends

### Symptom
Same as above, plus deeper consequences: every WhatsApp/Telegram/Discord send a worker performs goes around the main gateway entirely. The main gateway holds the long-lived bridge connection, owns rate-limit state, retry/backoff state, reconnection logic, and (for some platforms) typing indicators and read receipts. The worker's standalone send re-opens a fresh HTTP connection and bypasses all of that.

### Root cause
The architectural decision in the 2026-05-07 design was that workers run a stripped-down `GatewayRunner` with **only IPC**. That's correct for *inbound* dispatch (ingress owns WhatsApp, forwards to worker via IPC, worker replies via IPC, ingress sends back). But cron-initiated sends and `send_message`-tool calls inside the worker take a **different path** — they call `_send_to_platform` directly, which the worker is not architecturally meant to do.

### Proposed fix (correct but larger)
Add an IPC-back-to-ingress send path. When a worker wants to send to a non-IPC platform:

1. Worker IPC adapter publishes a `send.request` envelope upstream (similar to inbound dispatch but in reverse).
2. Ingress receives, looks up its live adapter for that platform, performs the send, returns success/error to the worker.
3. Worker `_send_to_platform` short-circuits: if the worker is "IPC-only" and the target platform is non-IPC, route through the IPC adapter instead of `httpx`.

This restores single-owner semantics for the platform connection (the main gateway), gives workers free access to the gateway's reconnection logic, and removes the bridge-race entirely (workers never talk to the bridge directly).

Trade-offs:
- More IPC traffic on busy profiles (every send round-trips through ingress).
- Need a request/reply correlation pattern on the IPC adapter — partial scaffolding may already exist for inbound dispatch (see `gateway/profile_worker.py` correlation IDs).

### Why we'd still want Shortcoming 1 fixed even with #2
Even with #2, the ingress's live adapter can hit transient bridge errors during reconnection. Retry-on-transient is useful regardless of the call site.

---

## Recommended order

1. **Now (small):** Fix Shortcoming 1 — retry on transient connection errors in `_send_to_platform`. Single-file change, benefits every platform on every profile, prevents one-shot job loss on container restart.
2. **Later (architectural):** Fix Shortcoming 2 — route worker→external-platform sends back through the ingress over IPC. Larger change, but it removes a class of subtle bugs (bypassed rate-limits, duplicate connection state, race conditions) and is the architecturally consistent design.

Neither is urgent in steady-state operation; both matter on container restart, bridge reconnect, or any scenario with a backlog of due jobs.

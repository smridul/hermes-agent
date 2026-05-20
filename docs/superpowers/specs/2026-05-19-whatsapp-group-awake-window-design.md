# WhatsApp Group Awake-Window — Design

**Date:** 2026-05-19
**Status:** Approved — ready for implementation plan

## Problem

In a WhatsApp group where the agent runs with `require_mention` on, the agent
only responds when a message explicitly @-mentions the bot. Sustained
back-and-forth in a group is awkward: every single message must re-tag the bot.

We want a lighter mode: one @-mention "wakes" the agent for the group, and for
the next 15 minutes it answers **every** message with no tag required. The
window can be extended by re-mentioning, and ended early with a sleep command.

## Behavior specification

Scope: group chats only, and only meaningfully active when `require_mention` is
on for that group (when it is off, the agent already answers everything). The
window is keyed by group chat JID (`chatId`).

### Open

When the window is **closed** and an inbound message @-mentions the agent (and
is not a sleep command):

- The window opens: `awake_until = now + group_session_minutes`.
- The agent posts a deterministic **wake notice** to the group.
- A per-group expiry task is scheduled.
- The triggering message is still forwarded to the agent and answered normally
  (the wake notice is a separate message, not a replacement for the reply).

### Reset

When the window is **open** and an inbound message @-mentions the agent (not a
sleep command):

- `awake_until` is pushed to `now + group_session_minutes` (measured from this
  mention).
- The expiry task is cancelled and rescheduled.
- This is **silent** — no repeated wake notice.

Plain (non-mention) messages received while the window is open are answered but
do **not** extend the window. The window always expires 15 minutes after the
*most recent @-mention*.

### Sleep

When an inbound message @-mentions the agent **and** its mention-stripped body
exactly matches a sleep keyword:

- The window closes immediately; the expiry task is cancelled.
- The agent posts a deterministic **sleep notice**.
- The sleep command is **not** forwarded to the LLM — it is a control message,
  swallowed at the gate.

Sleep keywords (hard-coded, case-insensitive, exact match on the cleaned body):
`sleep`, `stop`, `go to sleep`, `sleep now`, `stop listening`, `quiet`.

Exact match guards against accidental shutdowns — e.g. "@eureka I need to sleep"
is **not** a sleep command (body is not exactly a keyword), so it is treated as
an ordinary mention.

### Expiry

`group_session_minutes` after the last @-mention with no intervening sleep, the
expiry task fires:

- The agent posts a deterministic **expiry notice**.
- Window state is cleared.
- The group reverts to mention-required (classic) behavior.

### Closed window

When the window is closed, the message falls through to today's exact classic
gate (`_should_process_message`): free-response chats, `require_mention`,
leading `/` command, reply-to-bot, mention, mention-patterns. With the feature
disabled there is **zero** behavior change.

### Non-triggers

Reply-to-bot and mention-pattern matches continue to trigger one-off responses
under the classic gate, but they do **not** open or reset a window. Only an
explicit @-mention does.

## Architecture

### Components

**`GroupSessionManager`** — new, isolated, unit-testable class.

- State: `dict[chat_id -> GroupSession]`.
- `GroupSession`: `awake_until: float`, `expiry_task: Optional[asyncio.Task]`.
- Injected dependencies (for testability — no real 15-minute waits):
  - `window_seconds: float`
  - `now_fn: Callable[[], float]` — clock
  - `on_expire: Callable[[str], Awaitable[None]]` — async expiry callback
- Methods:
  - `is_awake(chat_id) -> bool`
  - `open_or_reset(chat_id) -> bool` — returns `True` if newly opened, `False`
    if an existing window was reset; (re)schedules the expiry task.
  - `close(chat_id)` — cancels the expiry task and drops state.

The expiry task is `await asyncio.sleep(window_seconds)` then `on_expire`. On
reset, the old task is cancelled and a fresh one created.

**`classify_group_control(data) -> "wake" | "sleep" | None`** — pure function.
Reuses existing `_message_mentions_bot` / `_clean_bot_mention_text` to decide
whether a message is a wake mention, a sleep command, or neither.

**`whatsapp.py` wiring** — in the async `_build_message_event` group path:

1. Classify the message via `classify_group_control`.
2. Drive `GroupSessionManager` (open / reset / close).
3. Post the deterministic wake / sleep / expiry notices via the adapter's
   existing outbound send path.
4. Decide process vs. swallow:
   - sleep command → swallow (return no event)
   - wake / window-open → process
   - window-closed → defer to `_should_process_message` (classic gate)

`_should_process_message` is kept as-is and consulted only when the window is
closed.

### State location & lifecycle

- State lives in the **primary gateway's** single `WhatsAppAdapter` instance.
  The adapter polls the bridge and runs this gate before any profile-worker IPC
  routing, so there is no cross-process state concern.
- State is **in-memory**. A gateway restart clears all windows; groups revert
  to mention-required. A 15-minute ephemeral window surviving a restart has
  little value, so this is intentional — no persistence.
- Expiry tasks are owned by the manager and die with the process; no orphans.

### Rejected alternatives

- **Cron scheduler for expiry** — cron is disk-persisted delivery jobs on a
  60-second tick; heavyweight and imprecise for an ephemeral window.
- **Persist window in SessionStore (JSON+SQLite)** — surviving a restart adds
  storage complexity for negligible benefit.

## Configuration

Two new keys in the `whatsapp` config block, with `WHATSAPP_*` env fallbacks,
following existing config patterns in `whatsapp.py.__init__`:

| Key | Type | Default | Env fallback |
|-----|------|---------|--------------|
| `group_session_window` | bool | `false` | `WHATSAPP_GROUP_SESSION_WINDOW` |
| `group_session_minutes` | int | `15` | `WHATSAPP_GROUP_SESSION_MINUTES` |

`group_session_window` defaults to **off** so other profiles (megha-bot,
ember-bot, etc.) are unaffected; it is opt-in per profile config. The user
enables it in their group's profile `config.yaml`.

Sleep keywords and the wake/sleep/expiry notice texts are hard-coded in v1.
Making the notice texts configurable per profile is a possible future
enhancement, out of scope here.

## Notice texts (v1, hard-coded)

- **Wake:** brief — agent is now listening to the group for the next N minutes,
  and how to stop it (`@<agent> sleep`).
- **Sleep:** brief — agent is going quiet; tag it to wake it again.
- **Expiry:** brief — the listening window ended; tag the agent to continue.

Exact wording to be finalized during implementation; keep them short and
unobtrusive.

## Testing (TDD)

**`GroupSessionManager`** (fake clock, tiny `window_seconds`):

- `open_or_reset` on a closed window returns `True`; on an open window returns
  `False`.
- `is_awake` reflects the fake clock crossing `awake_until`.
- The expiry callback fires after `window_seconds` (set to ~10ms in tests).
- Reset cancels the previous expiry task and reschedules.
- `close` cancels the expiry task and clears state.

**`classify_group_control`** (pure):

- Plain mention → `"wake"`.
- Mention + exact sleep keyword → `"sleep"`.
- Mention + sleep word embedded in a sentence ("I need to sleep") → `"wake"`,
  not `"sleep"`.
- No mention → `None`.

**Gate integration (`_build_message_event`)**:

- Closed window + mention → window opens, message processed.
- Open window + plain message → processed without a tag.
- Open window + sleep command → window closes, message swallowed (no event).
- Feature disabled → classic behavior unchanged.

## Out of scope

- Persisting windows across gateway restarts.
- Configurable sleep keywords or notice templates.
- Per-user windows (the window is shared per group, matching existing group
  session keying).
- Opening a window via reply-to-bot or mention-patterns.

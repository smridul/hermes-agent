---
name: find-my
description: Find or ring lost keys, wallet, AirTags, Apple devices.
version: 1.0.0
author: Mridul Shrivastava, Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Smart-Home, Apple, Find-My, AirTag, Lost-Item]
    category: smart-home
prerequisites:
  commands: [jq, curl]
---

# Find My Skill

Rings Mridul's AirTags, Find My tags and Apple devices ("ring my keys", "where's my wallet?",
"make my iPad beep") and lists their last-known status. It works through a bridge service on
the always-on Mac mini that drives the Find My app. It does not give map coordinates, and it
cannot ring a tag that is out of Bluetooth range of the Mac mini.

## When to Use

**Physical items, not money:** "where is my wallet" means the Find My wallet tag, not a finance
account. Voice dictation often turns "Where is my wallet?" into "Here is my wallet." — if a
short message mentions keys/wallet/passport/car with no attachment, treat it as a Find My
request (run `list` and report that item's status).

- The user asks to ring, beep, find or locate keys, wallet, passport, car tag, AirPods, iPad,
  iPhone, Apple Watch, MacBook — anything in Apple Find My.
- The user asks to stop a ringing item.
- The user asks what Find My items exist, or roughly where one is ("Nearby, Home · 5 min. ago").

## Prerequisites

- `/opt/data/findmy/findmy` with the token in `/opt/data/findmy/.env`
  (`FINDMY_BRIDGE_TOKEN=...`). Not on `$PATH`; call it by full path.
- The Mac mini bridge (`com.mridul.findmy-bridge` LaunchAgent, `100.80.89.122:8787`,
  Tailscale-only) is up. `findmy health` checks it.

## How to Run

Run with the `terminal` tool. A call can take up to ~15 s (locked Mac, cold Find My) —
let it finish; the script has its own 75 s timeout.

## Quick Reference

```bash
/opt/data/findmy/findmy list            # name <TAB> item|device <TAB> status
/opt/data/findmy/findmy play "keys"     # -> Ringing Mridul’s Keys
/opt/data/findmy/findmy stop "keys"     # -> Stopped ringing Mridul’s Keys
/opt/data/findmy/findmy health          # reachable / screen_locked / ax_trusted
```

Names match case-insensitively, exact first, then a unique substring — `"keys"` finds
`Mridul’s Keys`. Straight and curly apostrophes are equivalent. Exit code 0 = done;
non-zero = failed, with a plain-English reason on stderr.

## Procedure

1. Map the request to a name ("my keys" → `keys`, "my wallet" → `wallet`). If unsure, run
   `list` first.
2. **Tags** (`kind` = item — keys, wallet, passport file, car, ...): run `play` directly.
3. **Apple devices** (`kind` = device — iPad, iPhone, Mac, Watch, AirPods): ringing them is
   loud. Run `play` only when the user explicitly asked to ring that device; for a vague
   "find my iPad" give the `list` status and offer to ring it.
4. Report the outcome in one line. On success: "Ringing your keys now." Never say it rang
   if the script exited non-zero.
5. Never ring anything unprompted, and never loop or retry `play` more than once.

## Pitfalls

- **"not in Bluetooth range"** — the Mac mini plays the sound over Bluetooth. Tell the user
  the item is away from home / out of range, and give its `list` status (last seen).
- **"Mac mini is locked"** — unlocking relies on Apple Watch Auto Unlock. Tell the user to
  have the watch on, unlocked and near the Mac mini, then retry.
- **"unreachable"** — the Mac mini is off, asleep off-network, or the bridge is down. Don't
  retry in a loop; report it.
- **"busy"** — another Find My action is running; retry once after a few seconds.
- Each action brings the Find My app to the front on the Mac mini. Harmless.
- Device-ringing (iPad etc.) and third-party tags are less tested than AirTags.

## Verification

`findmy health` prints `reachable: yes` and `ax_trusted: true`. `findmy list` returns items
(e.g. Mridul’s Keys). A successful `play` prints `Ringing <name>` and the item audibly beeps.

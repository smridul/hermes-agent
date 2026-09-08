---
name: alexa-voice
description: "Control Alexa-only smart home devices (Amazon Smart Plug, Echo-linked switches) by sending text commands through an Echo device — for devices that expose no local or cloud API of their own. Also does TTS, routines, and playback control on Echo speakers."
version: 1.0.0
author: community
license: MIT
metadata:
  hermes:
    tags: [Smart-Home, Alexa, Echo, Plug, IoT, Automation, Cloud]
    homepage: https://github.com/thorsten-gehrig/alexa-remote-control
prerequisites:
  commands: [jq, curl]
---

# Alexa Voice Control

Drives Alexa by submitting text to an Echo device exactly as if it had been spoken. Use this
for devices that can **only** be reached through Alexa — the Amazon-branded Smart Plug being
the main one, since it has no local API, no third-party cloud API, and no Home Assistant
integration.

For anything with a real API, prefer that instead: `hue-cloud` / `openhue` for Hue, or the
built-in `ha_*` tools for Home Assistant devices. This skill is the last resort, and it
depends on an unofficial Amazon endpoint.

## Command

`/opt/data/alexa/alexa` — a wrapper that loads credentials and pins the US endpoints
(`amazon.com` / `pitangui.amazon.com`).

It lives under `/opt/data`, a bind mount to `/data/hermes-agent` on the host, so it and its
credentials survive container rebuilds. It is **not** on `$PATH` — always call it by full path.

## Common Commands

### Control a device through Alexa

```bash
/opt/data/alexa/alexa -d "Mridul's Echo Dot" -e textcommand:"turn off the plug"
/opt/data/alexa/alexa -d "Mridul's Echo Dot" -e textcommand:"turn on the plug"
```

The phrase must match what you would actually say out loud — the device name has to be the
one configured in the Alexa app. A wrong name fails silently from our side; Alexa answers
"I don't know that device" on the Echo.

### List devices

```bash
/opt/data/alexa/alexa -a
```

### Verify a command landed

```bash
/opt/data/alexa/alexa -lastcommand
```

Returns what Alexa's speech recognizer registered. `ASR_REPLACEMENT_TEXT` echoes the command;
an empty `TTS_REPLACEMENT_TEXT` normally means a device command succeeded (Alexa chimes
rather than speaking).

### Other

```bash
/opt/data/alexa/alexa -d "<device>" -e speak:"dinner is ready"     # TTS
/opt/data/alexa/alexa -d "<device>" -e automation:"Good Morning"   # run a routine
/opt/data/alexa/alexa -d "<device>" -e pause|play|next|vol:30      # playback
```

## Verifying a Toggle Actually Worked

`textcommand` is fire-and-forget — a successful send does not prove the device changed state.
If a plug is already off, "turn off the plug" is a silent no-op and looks identical to success.
When it matters, change the device to the **opposite** of its current state and confirm in the
Alexa app.

## Setup (one-time, operator)

`REFRESH_TOKEN` in `/opt/data/alexa/.env` (mode 600, owned by uid 10000), quoted — the token
contains a `|` that the shell would otherwise read as a pipe:

```
REFRESH_TOKEN='Atnr|...'
```

Obtaining that token is the hard part and is **not** reliably repeatable — see
`docs/alexa-token-setup.md` before attempting it. Back up the token; do not assume you can
mint a new one on demand.

## Limitations

- Unofficial. Amazon can break it without notice; the upstream script tracks their changes.
- The token registers a device on the Amazon account (`alexa_cookie_cli`). Deregistering it
  from the Amazon device list revokes access immediately.
- Every command routes through a physical Echo. If the Echo is offline, commands do nothing.

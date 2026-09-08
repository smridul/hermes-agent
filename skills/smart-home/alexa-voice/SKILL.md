---
name: alexa-voice
description: "Read and control Alexa-linked smart home devices — the Amazon Smart Plug, Echo-linked switches and lights — including devices with no local or cloud API of their own. Query on/off state, turn devices on/off, and optionally send spoken commands or TTS through an Echo."
version: 2.0.0
author: community
license: MIT
metadata:
  hermes:
    tags: [Smart-Home, Alexa, Echo, Plug, IoT, Automation, Cloud]
    homepage: https://github.com/thorsten-gehrig/alexa-remote-control
prerequisites:
  commands: [jq, curl]
---

# Alexa Device Control

Reads and controls anything linked to the Alexa account. Use this for devices reachable
**only** through Alexa — the Amazon-branded Smart Plug especially, which has no local API, no
third-party cloud API, and no Home Assistant integration.

For devices with their own API, prefer that: `hue-cloud` / `openhue` for Hue, or the built-in
`ha_*` tools for Home Assistant.

## Preferred: direct API (`alexa-device`)

`/opt/data/alexa/alexa-device` — talks to Amazon's smart-home API directly. **Use this by
default.** No Echo is involved, it returns real success/failure, and state is verifiable.

```bash
/opt/data/alexa/alexa-device list                  # all device names
/opt/data/alexa/alexa-device state "First plug"    # -> ON | OFF
/opt/data/alexa/alexa-device on    "First plug"    # -> "First plug is now ON"
/opt/data/alexa/alexa-device off   "First plug"    # -> "First plug is now OFF"
```

`on`/`off` read the state back after acting, so their output reflects what the device actually
did rather than just that the request was accepted.

Names match exactly first, then case-insensitively by substring — `"plug"` finds
`"First plug"`. Run `list` if a name doesn't resolve.

**The Amazon Smart Plug on this account is named `First plug`.**

## Fallback: voice (`alexa`)

`/opt/data/alexa/alexa` submits text to an Echo as if spoken. Use it only for things the
direct API can't do — running Alexa routines, TTS, media playback.

```bash
/opt/data/alexa/alexa -a                                           # list Echo devices
/opt/data/alexa/alexa -d "<echo>" -e textcommand:"turn on the plug"
/opt/data/alexa/alexa -d "<echo>" -e speak:"dinner is ready"       # TTS
/opt/data/alexa/alexa -d "<echo>" -e automation:"Good Morning"     # run a routine
/opt/data/alexa/alexa -lastcommand                                 # what Alexa heard
```

`textcommand` is **fire-and-forget** — a clean send does not prove anything happened. If a
plug is already off, "turn off the plug" is an invisible no-op that looks exactly like
success. Verify with `alexa-device state`.

## Location and credentials

Everything lives in `/opt/data/alexa/` — a bind mount to `/data/hermes-agent` on the host, so
scripts, cookies and credentials survive container rebuilds. **Not on `$PATH`** — always call
by full path.

`REFRESH_TOKEN` sits in `/opt/data/alexa/.env` (mode 600, uid 10000), quoted, because the
token contains a `|` the shell would read as a pipe:

```
REFRESH_TOKEN='Atnr|...'
```

Cookies are minted from that token automatically and refreshed when stale; `alexa-device`
retries once on a bad device list before giving up.

## Limitations

- Unofficial API. Amazon can change it without notice.
- Getting a *new* refresh token is painful and unreliable — see `docs/alexa-token-setup.md`.
  Back the token up; do not assume it can be re-minted on demand.
- The token registers a device named `alexa_cookie_cli` on the Amazon account. Deregistering
  it from the Amazon device list revokes access immediately.
- The voice fallback needs a physically online Echo; the direct API does not.

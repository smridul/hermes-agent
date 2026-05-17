---
name: hue-cloud
description: "Control Philips Hue lights, scenes, rooms remotely via the Hue Remote API (cloud) — no LAN access to the bridge required."
version: 1.0.0
author: community
license: MIT
metadata:
  hermes:
    tags: [Smart-Home, Hue, Lights, IoT, Automation, Cloud]
    homepage: https://developers.meethue.com/develop/hue-api/remote-api-quick-start-guide/
prerequisites:
  commands: [hue-cloud]
---

# Hue Cloud (Remote API)

Control Philips Hue lights and scenes from anywhere using the Philips Hue Remote API. Use this when Hermes runs off-LAN (e.g. on a cloud VM) and cannot reach the Hue Bridge directly. For local-LAN setups, prefer the bundled `openhue` skill instead.

## Setup (one-time, operator)

1. Register a Remote Hue API app at https://developers.meethue.com/ → top-right menu → "Remote Hue API app IDs" → "Add new Remote Hue API app". You'll receive an `AppID`, `ClientID`, and `ClientSecret`.
2. Write credentials to `/opt/data/hue-cloud/.env` inside the container (mode 600, owned by uid 10000):
   ```
   HUE_APP_ID=...
   HUE_CLIENT_ID=...
   HUE_CLIENT_SECRET=...
   ```
3. Run the interactive bootstrap once:
   ```bash
   docker exec -it <hermes-container> hue-cloud-bootstrap
   ```
   It prints an authorize URL, you open it in a browser, authorize, paste the redirected URL back, and it writes `/opt/data/hue-cloud/tokens.json`.

## When to Use

- "Turn on/off the lights"
- "Dim the living room lights"
- "Set a scene" or "movie mode"
- Controlling specific Hue rooms, zones, or individual bulbs
- Adjusting brightness, color, or color temperature
- Any of the above when Hermes is running off your home network

## Common Commands

### List Resources

```bash
hue-cloud get light       # List all lights (id, name, on/off, brightness, reachable)
hue-cloud get room        # List Room-type groups
hue-cloud get room --all  # Include Zone and other group types
hue-cloud get scene       # List all scenes (id, name, group)
```

### Control Lights

```bash
hue-cloud set light "Bedroom Lamp" --on
hue-cloud set light "Bedroom Lamp" --off
hue-cloud set light "Bedroom Lamp" --on --brightness 50           # 0-100
hue-cloud set light "Bedroom Lamp" --on --temperature 300         # 153-500 mirek
hue-cloud set light "Bedroom Lamp" --on --color red               # named color
hue-cloud set light "Bedroom Lamp" --on --rgb "#FF5500"           # hex
```

### Control Rooms

```bash
hue-cloud set room "Bedroom" --off
hue-cloud set room "Bedroom" --on --brightness 30
```

### Scenes

```bash
hue-cloud set scene "Relax" --room "Bedroom"
hue-cloud set scene "Concentrate" --room "Office"
```

## Quick Presets

```bash
# Bedtime (dim warm)
hue-cloud set room "Bedroom" --on --brightness 20 --temperature 450

# Work mode (bright cool)
hue-cloud set room "Office" --on --brightness 100 --temperature 250

# Movie mode (dim)
hue-cloud set room "Living Room" --on --brightness 10

# Everything off
hue-cloud set room "Bedroom" --off
hue-cloud set room "Office" --off
hue-cloud set room "Living Room" --off
```

## Named Colors

`red`, `green`, `blue`, `yellow`, `orange`, `purple`, `pink`, `white`, `warm`, `cool`. For anything else, use `--rgb #RRGGBB`.

## Notes

- The CLI auto-refreshes access tokens on `401` and persists the rotated refresh token atomically — no manual maintenance required.
- Light, room, and scene names are matched case-insensitively. If two share a name, the CLI errors out; rename one.
- Rate-limited by Philips: avoid hammering the API in tight loops.
- If a 401 keeps recurring after auto-refresh, the refresh token is dead (revoked or expired through inactivity) — re-run `hue-cloud-bootstrap` to re-authorize.
- Works great with cron jobs for scheduled lighting (e.g. dim at bedtime, bright at wake).

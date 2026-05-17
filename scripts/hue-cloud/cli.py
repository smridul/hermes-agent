#!/usr/bin/env python3
"""hue-cloud: openhue-compatible CLI that talks to the Philips Hue Remote API.

Reads ClientID/Secret from $HUE_CLOUD_DIR/.env (default /opt/data/hue-cloud/.env)
and access/refresh tokens + whitelist username from $HUE_CLOUD_DIR/tokens.json
(written by `hue-cloud-bootstrap`). Auto-refreshes access tokens on 401.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.meethue.com"
HUE_DIR = Path(os.environ.get("HUE_CLOUD_DIR", "/opt/data/hue-cloud"))
ENV_FILE = HUE_DIR / ".env"
TOKENS_FILE = HUE_DIR / "tokens.json"


def die(msg: str, code: int = 1) -> None:
    print(f"hue-cloud: {msg}", file=sys.stderr)
    sys.exit(code)


def load_env() -> dict:
    if not ENV_FILE.exists():
        die(f"missing credentials file: {ENV_FILE}")
    env: dict = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    for key in ("HUE_CLIENT_ID", "HUE_CLIENT_SECRET"):
        if not env.get(key):
            die(f"missing {key} in {ENV_FILE}")
    return env


def load_tokens() -> dict:
    if not TOKENS_FILE.exists():
        die(f"missing tokens — run hue-cloud-bootstrap once first")
    return json.loads(TOKENS_FILE.read_text())


def save_tokens(tokens: dict) -> None:
    HUE_DIR.mkdir(parents=True, exist_ok=True)
    tokens["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = TOKENS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tokens, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(TOKENS_FILE)


def basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return f"Basic {base64.b64encode(raw).decode()}"


def http(method: str, url: str, *, headers: dict | None = None, body=None) -> tuple[int, str]:
    headers = dict(headers or {})
    data: bytes | None = None
    if isinstance(body, dict):
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        data = body.encode()
    elif isinstance(body, bytes):
        data = body
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode() if e.fp else "")


def refresh(env: dict, tokens: dict) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    })
    status, text = http(
        "POST", f"{API}/v2/oauth2/token",
        headers={
            "Authorization": basic_auth(env["HUE_CLIENT_ID"], env["HUE_CLIENT_SECRET"]),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body=body,
    )
    if status != 200:
        die(f"token refresh failed ({status}): {text}")
    data = json.loads(text)
    tokens["access_token"] = data["access_token"]
    tokens["refresh_token"] = data["refresh_token"]
    tokens["expires_at"] = (
        datetime.now(timezone.utc).timestamp() + int(data.get("expires_in", 604800))
    )
    save_tokens(tokens)
    return tokens


def api_call(env: dict, tokens: dict, method: str, path: str, body=None):
    url = f"{API}/route/api/{tokens['username']}{path}"
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    status, text = http(method, url, headers=headers, body=body)
    if status == 401:
        tokens = refresh(env, tokens)
        headers["Authorization"] = f"Bearer {tokens['access_token']}"
        status, text = http(method, url, headers=headers, body=body)
    if status >= 400:
        die(f"API error {status}: {text}")
    return json.loads(text) if text else {}


def find_by_name(items: dict, name: str, kind: str) -> tuple[str, dict]:
    matches = [(i, obj) for i, obj in items.items() if obj.get("name", "").lower() == name.lower()]
    if not matches:
        die(f"no {kind} named '{name}' — try `hue-cloud get {kind}` to list")
    if len(matches) > 1:
        die(f"multiple {kind}s named '{name}' — rename one or pass an ID")
    return matches[0]


NAMED_COLORS = {
    "red":    (0.7000, 0.3000),
    "green":  (0.3000, 0.6000),
    "blue":   (0.1500, 0.0600),
    "yellow": (0.5000, 0.4500),
    "orange": (0.5800, 0.3800),
    "purple": (0.3000, 0.1500),
    "pink":   (0.5500, 0.3000),
    "white":  (0.3127, 0.3290),
    "warm":   (0.4570, 0.4100),
    "cool":   (0.3100, 0.3200),
}


def hex_to_xy(hex_str: str) -> list[float]:
    h = hex_str.lstrip("#")
    if len(h) != 6:
        die(f"--rgb expects #RRGGBB, got {hex_str}")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    # sRGB gamma
    r = ((r + 0.055) / 1.055) ** 2.4 if r > 0.04045 else r / 12.92
    g = ((g + 0.055) / 1.055) ** 2.4 if g > 0.04045 else g / 12.92
    b = ((b + 0.055) / 1.055) ** 2.4 if b > 0.04045 else b / 12.92
    X = r * 0.4124 + g * 0.3576 + b * 0.1805
    Y = r * 0.2126 + g * 0.7152 + b * 0.0722
    Z = r * 0.0193 + g * 0.1192 + b * 0.9505
    total = X + Y + Z
    return [0.3127, 0.3290] if total == 0 else [round(X / total, 4), round(Y / total, 4)]


def build_state(args) -> dict:
    state: dict = {}
    if args.on:
        state["on"] = True
    if args.off:
        state["on"] = False
    if args.brightness is not None:
        if not 0 <= args.brightness <= 100:
            die("--brightness must be 0-100")
        state["bri"] = max(1, round(args.brightness / 100 * 253) + 1)
    if args.temperature is not None:
        if not 153 <= args.temperature <= 500:
            die("--temperature must be 153-500 mirek")
        state["ct"] = args.temperature
    if args.color:
        key = args.color.lower()
        if key not in NAMED_COLORS:
            die(f"unknown color '{args.color}' — supported: {', '.join(NAMED_COLORS)}")
        state["xy"] = list(NAMED_COLORS[key])
    if args.rgb:
        state["xy"] = hex_to_xy(args.rgb)
    return state


def report_result(result) -> None:
    """Print PUT result and exit non-zero if any entry contains an error."""
    print(json.dumps(result, indent=2))
    if isinstance(result, list):
        errors = [entry["error"] for entry in result if isinstance(entry, dict) and "error" in entry]
        if errors:
            for err in errors:
                print(f"hue-cloud: bridge reported error: {err}", file=sys.stderr)
            sys.exit(1)


def cmd_get_light(env, tokens, _args):
    data = api_call(env, tokens, "GET", "/lights")
    print(f"{'ID':<4} {'Name':<30} {'On':<4} {'Brightness':<11} {'Reachable'}")
    for lid, light in data.items():
        st = light.get("state", {})
        on_str = "on" if st.get("on") else "off"
        bri_raw = st.get("bri", 0)
        bri_pct = round((bri_raw - 1) / 253 * 100) if st.get("on") else 0
        reach = "yes" if st.get("reachable") else "no"
        print(f"{lid:<4} {light.get('name', ''):<30} {on_str:<4} {bri_pct}%{'':<7} {reach}")


def cmd_get_room(env, tokens, args):
    data = api_call(env, tokens, "GET", "/groups")
    print(f"{'ID':<4} {'Name':<30} {'Type':<10} {'Lights'}")
    for gid, group in data.items():
        if args.all or group.get("type") == "Room":
            lights = ",".join(group.get("lights", []))
            print(f"{gid:<4} {group.get('name', ''):<30} {group.get('type', ''):<10} [{lights}]")


def cmd_get_scene(env, tokens, _args):
    data = api_call(env, tokens, "GET", "/scenes")
    print(f"{'ID':<22} {'Name':<30} {'Group'}")
    for sid, scene in data.items():
        print(f"{sid:<22} {scene.get('name', ''):<30} {scene.get('group', '')}")


def cmd_set_light(env, tokens, args):
    state = build_state(args)
    if not state:
        die("nothing to do — pass --on/--off/--brightness/--temperature/--color/--rgb")
    lights = api_call(env, tokens, "GET", "/lights")
    lid, _ = find_by_name(lights, args.name, "light")
    report_result(api_call(env, tokens, "PUT", f"/lights/{lid}/state", body=state))


def cmd_set_room(env, tokens, args):
    state = build_state(args)
    if not state:
        die("nothing to do — pass --on/--off/--brightness/--temperature/--color/--rgb")
    groups = api_call(env, tokens, "GET", "/groups")
    gid, _ = find_by_name(groups, args.name, "room")
    report_result(api_call(env, tokens, "PUT", f"/groups/{gid}/action", body=state))


def cmd_set_scene(env, tokens, args):
    groups = api_call(env, tokens, "GET", "/groups")
    gid, _ = find_by_name(groups, args.room, "room")
    scenes = api_call(env, tokens, "GET", "/scenes")
    candidates = [
        (sid, sc) for sid, sc in scenes.items()
        if sc.get("name", "").lower() == args.name.lower()
    ]
    if not candidates:
        die(f"no scene named '{args.name}'")
    # prefer scene that belongs to the requested room
    same_room = [(sid, sc) for sid, sc in candidates if sc.get("group") == gid]
    scene_id = (same_room or candidates)[0][0]
    report_result(api_call(env, tokens, "PUT", f"/groups/{gid}/action", body={"scene": scene_id}))


def add_set_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("name", help="name (case-insensitive)")
    p.add_argument("--on", action="store_true")
    p.add_argument("--off", action="store_true")
    p.add_argument("--brightness", type=int, metavar="0-100")
    p.add_argument("--temperature", type=int, metavar="153-500", help="mirek (warm 500 ↔ cool 153)")
    p.add_argument("--color", help=f"one of: {', '.join(NAMED_COLORS)}")
    p.add_argument("--rgb", metavar="#RRGGBB", help="hex color")


def main() -> None:
    parser = argparse.ArgumentParser(prog="hue-cloud", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    get = sub.add_parser("get", help="list lights/rooms/scenes")
    get_sub = get.add_subparsers(dest="resource", required=True)
    get_sub.add_parser("light", help="list all lights")
    g_room = get_sub.add_parser("room", help="list rooms (Room-type groups)")
    g_room.add_argument("--all", action="store_true", help="include zones and other groups")
    get_sub.add_parser("scene", help="list all scenes")

    setp = sub.add_parser("set", help="control lights/rooms or activate scenes")
    set_sub = setp.add_subparsers(dest="resource", required=True)
    s_light = set_sub.add_parser("light")
    add_set_flags(s_light)
    s_room = set_sub.add_parser("room")
    add_set_flags(s_room)
    s_scene = set_sub.add_parser("scene")
    s_scene.add_argument("name", help="scene name")
    s_scene.add_argument("--room", required=True, help="room to apply scene in")

    args = parser.parse_args()
    env = load_env()
    tokens = load_tokens()

    handlers = {
        ("get", "light"): cmd_get_light,
        ("get", "room"):  cmd_get_room,
        ("get", "scene"): cmd_get_scene,
        ("set", "light"): cmd_set_light,
        ("set", "room"):  cmd_set_room,
        ("set", "scene"): cmd_set_scene,
    }
    handler = handlers.get((args.cmd, args.resource))
    if not handler:
        parser.print_help()
        sys.exit(2)
    handler(env, tokens, args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""hue-cloud-bootstrap: one-time OAuth setup for the Philips Hue Remote API.

Reads ClientID/Secret from $HUE_CLOUD_DIR/.env (default /opt/data/hue-cloud/.env),
walks the operator through the authorization code flow interactively, simulates
the remote linkbutton press, creates a whitelist Hue user for the bridge, and
writes $HUE_CLOUD_DIR/tokens.json with everything `hue-cloud` needs at runtime.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
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
DEVICE_ID = os.environ.get("HUE_DEVICE_ID", "hermes-agent")
DEVICE_NAME = os.environ.get("HUE_DEVICE_NAME", "eureka-vm")


def die(msg: str, code: int = 1) -> None:
    print(f"\n[error] {msg}", file=sys.stderr)
    sys.exit(code)


def load_env() -> dict:
    if not ENV_FILE.exists():
        die(f"missing {ENV_FILE} — must contain HUE_CLIENT_ID, HUE_CLIENT_SECRET, HUE_APP_ID")
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


def authorize_url(client_id: str, state: str) -> str:
    qs = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "state": state,
        "deviceid": DEVICE_ID,
        "devicename": DEVICE_NAME,
    })
    return f"{API}/v2/oauth2/authorize?{qs}"


def exchange_code_for_tokens(env: dict, code: str) -> dict:
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code})
    status, text = http(
        "POST", f"{API}/v2/oauth2/token",
        headers={
            "Authorization": basic_auth(env["HUE_CLIENT_ID"], env["HUE_CLIENT_SECRET"]),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body=body,
    )
    if status != 200:
        die(f"token exchange failed ({status}): {text}")
    return json.loads(text)


def press_remote_linkbutton(access_token: str) -> None:
    status, text = http(
        "PUT", f"{API}/route/api/0/config",
        headers={"Authorization": f"Bearer {access_token}"},
        body={"linkbutton": True},
    )
    if status != 200:
        die(f"linkbutton press failed ({status}): {text}")
    parsed = json.loads(text)
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "error" in parsed[0]:
        die(f"linkbutton error: {parsed[0]['error']}")


def create_whitelist_user(access_token: str) -> str:
    status, text = http(
        "POST", f"{API}/route/api/",
        headers={"Authorization": f"Bearer {access_token}"},
        body={"devicetype": f"{DEVICE_ID}#{DEVICE_NAME}"},
    )
    if status not in (200, 201):
        die(f"user creation failed ({status}): {text}")
    parsed = json.loads(text)
    if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
        die(f"unexpected /route/api/ response: {text}")
    entry = parsed[0]
    if "error" in entry:
        die(f"user creation error: {entry['error']}")
    username = entry.get("success", {}).get("username")
    if not username:
        die(f"no username in response: {text}")
    return username


def write_tokens(token_data: dict, username: str) -> None:
    HUE_DIR.mkdir(parents=True, exist_ok=True)
    tokens = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "expires_at": datetime.now(timezone.utc).timestamp() + int(token_data["expires_in"]),
        "username": username,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = TOKENS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tokens, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(TOKENS_FILE)


def main() -> None:
    print("hue-cloud bootstrap — one-time Philips Hue Remote API setup\n")
    env = load_env()

    state = secrets.token_urlsafe(32)
    url = authorize_url(env["HUE_CLIENT_ID"], state)

    print("Step 1 of 4 — open this URL in your browser and authorize the app:\n")
    print(f"  {url}\n")
    print("After authorizing, your browser will redirect to the callback URL you")
    print("registered. It will probably show a 404 or the Hermes dashboard — that's")
    print("expected. Copy the FULL URL from the browser's address bar and paste it")
    print("below.\n")

    redirected = input("Paste redirected URL here:\n> ").strip()
    if not redirected:
        die("empty input")
    parsed = urllib.parse.urlparse(redirected)
    qs = urllib.parse.parse_qs(parsed.query)
    code = (qs.get("code") or [None])[0]
    returned_state = (qs.get("state") or [None])[0]
    if not code:
        err = (qs.get("error") or ["?"])[0]
        die(f"no 'code' in URL (error={err}) — did you authorize?")
    if returned_state != state:
        die(f"state mismatch (CSRF guard) — expected {state[:8]}…, got {(returned_state or '')[:8]}…")

    print("\nStep 2 of 4 — exchanging code for tokens…")
    token_data = exchange_code_for_tokens(env, code)
    print(f"  ✓ access_token received (expires in {int(token_data['expires_in']) // 86400} days)")

    print("\nStep 3 of 4 — pressing remote linkbutton…")
    press_remote_linkbutton(token_data["access_token"])
    print("  ✓ linkbutton accepted")

    print("\nStep 4 of 4 — creating Hue whitelist user…")
    username = create_whitelist_user(token_data["access_token"])
    print(f"  ✓ whitelist user created: {username[:8]}…")

    write_tokens(token_data, username)
    print(f"\n  ✓ wrote {TOKENS_FILE}")
    print("\nDone. Test with:\n  hue-cloud get light")


if __name__ == "__main__":
    main()

# Fork Changes (vs `upstream/main` — NousResearch/hermes-agent)

This file is the canonical, durable record of customizations carried in this
fork (`smridul/hermes-agent`) that are **not** present upstream. Read it
before every `git merge upstream/main` so you know exactly what to preserve
when conflicts appear.

Keep this file up to date: any time you add, modify, or drop a fork-only
change, update the relevant section here in the same commit.

---

## How to use this during an upstream merge

1. `git fetch upstream && git merge upstream/main`
2. If conflicts appear, look up the conflicted file in the **Files touched**
   table below — it tells you what your side intends.
3. Default resolution rule: **take upstream's structural/refactor changes;
   re-apply the fork-only behavior listed here on top.** Most upstream
   conflicts are unrelated changes near our hooks; we want the upstream
   improvement, not to revert it.
4. Update this file if the resolution shifts how a fork change is wired.

---

## Files touched by this fork

| File | Type | Conflict risk | Section |
|------|------|---------------|---------|
| `Dockerfile` | Modified | High | [Docker image](#1-docker-image-dockerfile) |
| `docker/entrypoint.sh` | Modified | High | [Container entrypoint](#2-container-entrypoint-dockerentrypointsh) |
| `gateway/platforms/whatsapp.py` | Modified | Medium | [WhatsApp bridge install](#3-whatsapp-bridge-install-gatewayplatformswhatsapppy) |
| `hermes_cli/web_server.py` | Modified | Medium | [Trusted-proxy gate on SPA route](#4-trusted-proxy-gate-on-the-spa-route-hermes_cliweb_serverpy) |
| `hermes_cli/trusted_proxy.py` | New | None | [Trusted-proxy helpers](#5-trusted-proxy-helpers-new-file-hermes_clitrusted_proxypy) |
| `tests/gateway/test_whatsapp_connect.py` | New | None | covers (3) |
| `tests/hermes_cli/test_trusted_proxy_dashboard.py` | New | None | covers (4)+(5) |

"Conflict risk" means: how often upstream churns the same lines we touch.
None = file is fork-only and cannot conflict.

---

## 1. Docker image (`Dockerfile`)

Three additive customizations on top of upstream's image:

- **`tmux`** added to the apt install list (used for in-container terminal
  multiplexing during interactive sessions).
- **`EXPOSE 9119`** declared so deployment platforms (Coolify, generic Docker
  hosts) discover the dashboard port automatically.
- **`CMD [ "serve" ]`** so a bare `docker run <image>` boots the
  gateway + dashboard pair via our entrypoint's `serve` mode (see §2).

**Merge guidance**

- Upstream owns the apt list, the `ENTRYPOINT` line, and the base image
  pin. Take all of those.
- After taking upstream, ensure `tmux` is still in the apt list and that
  `EXPOSE 9119` and `CMD [ "serve" ]` still appear immediately after the
  `ENTRYPOINT`. Upstream's tini-wrapped entrypoint must be preserved.

---

## 2. Container entrypoint (`docker/entrypoint.sh`)

Adds a fork-only `serve` / `coolify` mode that launches the gateway and
the dashboard together as background processes with a shared
`SIGINT`/`SIGTERM` trap. The block sits **above** upstream's
executable-vs-`hermes` dispatcher, so any other invocation
(`docker run <image> bash`, `sleep infinity`, `chat -q ...`) still falls
through to upstream's logic.

```sh
if [ "$#" -eq 0 ] || [ "$1" = "serve" ] || [ "$1" = "coolify" ]; then
    # start `hermes gateway run --replace` + `hermes dashboard` in background
    # trap shutdown, wait -n, exit with first child's status
fi
```

Honors:

- `HERMES_DASHBOARD_HOST` (default `0.0.0.0`)
- `HERMES_DASHBOARD_PORT` (default `9119`)

**Merge guidance**

- Upstream owns everything **above** our `serve`/`coolify` block (root
  drop, gosu re-exec, ownership fix-up, .env / config.yaml / SOUL.md
  bootstrap, skills sync) and the dispatcher **below** it. Take both.
- Keep the `serve`/`coolify` block intact between them. If upstream
  refactors the bootstrap order, the block must still execute *after*
  the venv `source` and *before* the final `exec` so `hermes` is on PATH.

---

## 3. WhatsApp bridge install (`gateway/platforms/whatsapp.py`)

Hardens the bridge-dependency install step in `WhatsAppAdapter`:

- New env var `WHATSAPP_BRIDGE_INSTALL_TIMEOUT` (seconds, default `300`).
  Replaces a hardcoded 60s timeout that was too tight for slow networks
  and Coolify cold starts.
- When `package-lock.json` exists, install via `npm ci --silent
  --no-audit --no-fund` instead of `npm install --silent` so deploys are
  deterministic.
- Failure log line says "Dependency install failed" instead of
  "npm install failed".

Helpers added at module scope: `_bridge_install_timeout_seconds()`,
`_bridge_install_command()`, plus the constant
`_DEFAULT_BRIDGE_INSTALL_TIMEOUT_SECONDS = 300`.

Test coverage: `tests/gateway/test_whatsapp_connect.py`.

**Merge guidance**

- The fork-only logic is local to the `if not (bridge_dir /
  "node_modules").exists():` branch inside `WhatsAppAdapter`. If upstream
  rewrites the surrounding install flow, re-apply the `npm ci` preference
  and the env-var-driven timeout to whatever shape the new code takes.

---

## 4. Trusted-proxy gate on the SPA route (`hermes_cli/web_server.py`)

Adds an authentication gate to the dashboard's catch-all SPA route. When
`HERMES_TRUSTED_PROXY_HEADER` and `HERMES_TRUSTED_PROXY_VALUE` are both
set in the environment, requests for non-asset paths must include the
configured header with the configured value, otherwise they get a `401
Unauthorized`. Static asset requests (anything under `/assets/...` or any
existing file path) are unaffected.

Diff is small and localized to `serve_spa`:

- Imports `has_valid_trusted_proxy_header` and
  `trusted_proxy_unauthorized_response` from `hermes_cli.trusted_proxy`.
- Adds `request: Request` to `serve_spa`'s signature.
- Inserts the header check after the existing static-file branch but
  before the SPA fallback.

Test coverage: `tests/hermes_cli/test_trusted_proxy_dashboard.py`.

**Merge guidance**

- This is the file most likely to conflict if upstream changes how the
  SPA mount is wired. Preserve: the import, the `Request` parameter, and
  the header check sitting **between** the static-file branch and
  `_serve_index()`.

---

## 5. Trusted-proxy helpers (new file: `hermes_cli/trusted_proxy.py`)

Tiny module, fork-only, cannot conflict on merge. Exposes:

- `is_trusted_proxy_protection_enabled() -> bool` — true only when both
  `HERMES_TRUSTED_PROXY_HEADER` and `HERMES_TRUSTED_PROXY_VALUE` are set.
- `has_valid_trusted_proxy_header(request) -> bool` — returns `True` when
  protection is disabled (fail-open by config), otherwise compares the
  header on the request against the configured value.
- `trusted_proxy_unauthorized_response()` — returns a plain `HTMLResponse
  ("Unauthorized", status_code=401)`.

Settings are read fresh on every call via a small dataclass — no module-
level caching, so env changes take effect without a restart in tests.

---

## Configuration surface added by this fork

| Env var | Default | Purpose |
|---------|---------|---------|
| `HERMES_DASHBOARD_HOST` | `0.0.0.0` | Bind host for `serve` mode dashboard |
| `HERMES_DASHBOARD_PORT` | `9119` | Bind port for `serve` mode dashboard |
| `WHATSAPP_BRIDGE_INSTALL_TIMEOUT` | `300` | Seconds for `npm install` / `npm ci` of the WhatsApp bridge |
| `HERMES_TRUSTED_PROXY_HEADER` | unset | Header name a trusted proxy must set; unset disables the gate |
| `HERMES_TRUSTED_PROXY_VALUE` | unset | Required value for that header; unset disables the gate |

---

## Branch layout reminder

- `origin` = `git@github.com:smridul/hermes-agent.git` (this fork)
- `upstream` = `git@github.com:NousResearch/hermes-agent.git`
- The fork's customizations live on `main` (and `test`); rebase or merge
  `upstream/main` into `main` and resolve using this file as the spec.

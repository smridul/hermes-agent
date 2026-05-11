# HANDOFF.md

## Pickup Task (2026-05-11): Make MCP tools work inside profile workers

### Background

Project-logger ships an in-process MCP server at `http://app:8770/mcp`
exposing 25+4 tools to hermes (see project-logger commit `71adc2e`). The
gateway (in-process **primary profile**, currently named `default`)
successfully discovers all 33 tools at startup — verified by:

```
/data/hermes-agent/logs/agent.log
2026-05-11 02:42:48 INFO tools.mcp_tool: MCP server 'eureka-mcp' (HTTP):
  registered 33 tool(s): mcp_eureka_mcp_log_recent, ...,
  mcp_eureka_mcp_elevenlabs_list_voices, mcp_eureka_mcp_elevenlabs_tts,
  mcp_eureka_mcp_elevenlabs_voice_to_voice,
  mcp_eureka_mcp_elevenlabs_clone_voice, ...
```

**Problem:** profile-worker subprocesses (`test_profile`, `megha-bot`,
`ember-bot`, `mast-family-bot`, `chatur-char-bot`) have **zero** MCP
tools, even when their per-profile `config.yaml` declares `mcp_servers`
with the right URL+auth.

### Root cause (verified)

`discover_mcp_tools()` (the function that reads `mcp_servers` from
config and registers tools) is **only** called from
`gateway/run.py:14108-14110`, inside `start_gateway()`. Profile workers
bypass that wrapper:

```
hermes_cli/main.py             (top-level CLI; calls discover_mcp_tools)
hermes_cli/profile_worker_cli.py
  └─ main(): sets os.environ["HERMES_HOME"] = <profile path>   ✓
  └─ asyncio.run(_run_worker(name))
       └─ load_gateway_config()
       └─ runner = GatewayRunner(cfg)
       └─ runner.start()                                        # MCP discovery NOT here
```

So the `mcp_servers` block in
`/data/hermes-agent/profiles/<name>/config.yaml` is read by nothing —
the worker boots an `IPCPlatformAdapter`-only `GatewayRunner` and never
touches MCP. (User already verified the HERMES_HOME override works:
test_profile worker's runtime `HERMES_HOME` is correctly set to
`/opt/data/profiles/test_profile`.)

### What to do

Patch `hermes_cli/profile_worker_cli.py:_run_worker()` to invoke
`discover_mcp_tools()` after the worker has loaded gateway config but
before it starts the runner. Mirror the pattern in
`gateway/run.py:14101-14112`:

```python
# After load_gateway_config(), before runner.start():
try:
    from tools.mcp_tool import discover_mcp_tools
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, discover_mcp_tools)
except Exception:
    logger.exception(
        "profile-worker[%s]: MCP tool discovery failed", profile_name,
    )
```

Important constraints:

1. **Must run in an executor** (not direct `await`). Same reason as
   `gateway/run.py:14101` — `discover_mcp_tools` blocks up to 120s
   while waiting for slow MCP servers; running it on the loop thread
   freezes IPC heartbeats.

2. **Must run AFTER HERMES_HOME override.** It already does — the
   override happens in `main()` before `_run_worker()` is called. So
   `_load_mcp_config()` will read the profile's own `config.yaml`.

3. **Add `shutdown_mcp_servers()` to worker shutdown path** for
   symmetry. Look at `tools/mcp_tool.py` for the right hook.

### Per-profile MCP config requirement

Profile config schema (in `/data/hermes-agent/profiles/<name>/config.yaml`)
needs to declare `mcp_servers` itself — the global config is no longer
consulted once HERMES_HOME is overridden. Example (test_profile already
has this):

```yaml
mcp_servers:
  eureka-mcp:
    url: http://app:8770/mcp
    enabled: true
    headers:
      Authorization: Basic <base64-user:pass>
    tools:
      include:
        - elevenlabs_list_voices
        - elevenlabs_tts
        - elevenlabs_voice_to_voice
        - elevenlabs_clone_voice
      prompts: false
      resources: false
```

Document this in the patch's commit message / docs so operators know
each profile owns its own MCP config now.

### Cost / trade-off note

Each profile worker will hold an independent MCP session to
`app:8770`. With 5 profile workers + 1 primary = 6 concurrent sessions
to project-logger's MCP server. Project-logger's MCP can handle that
(it's FastMCP with streamable HTTP), but worth noting for capacity
planning. If this becomes a problem, an alternative architecture is a
shared MCP-proxy process per container that all workers IPC-route
through — out of scope for the first patch.

### Verification plan

1. Apply patch, rebuild image, redeploy `eureka-hermes` via Coolify.
2. On VM, tail the per-profile log:
   `sudo tail -f /data/hermes-agent/profiles/test_profile/logs/agent.log`
   Expect at startup: `"MCP server 'eureka-mcp' (HTTP): registered N tool(s)"`.
3. Send a WhatsApp message from `+14085921090` (maps to `test_profile`):
   "list elevenlabs voices via mcp". Expect a real voice list, not
   "no MCP tools available".
4. Verify session count on project-logger app side:
   `docker logs $(docker ps -f name=^app- --format "{{.Names}}") 2>&1 | grep "Created new transport" | tail -10`
   should show ~6 sessions (one per profile + the primary), not just 1.

### Useful references

- Spawn config: `gateway/run.py:1201-1234` (`_spawn_profile_workers`).
- Profile worker entry: `hermes_cli/profile_worker_cli.py` (whole file).
- MCP discovery: `tools/mcp_tool.py:2891-` (`discover_mcp_tools`).
- Profile→sender routing: `/data/hermes-agent/config.yaml`
  `channels.whatsapp.profile_routing.sender_profile_map`.
- Today's analysis transcript: see project-logger conversation
  `2026-05-11` re: ElevenLabs MCP tools rollout.

### Open questions for next session

1. Should `discover_mcp_tools` in the worker honour a worker-level
   timeout shorter than the default 120s? Slow MCP servers will delay
   worker readiness — `gateway/profile_worker_manager.py` has its own
   readiness deadline; need to check the budget.

2. Reload behaviour: `cli.py:7752-7900` has a config-watcher that
   reloads MCP on `mcp_servers` change in the **main** config. Should
   profile workers grow the same watcher, or is "restart the worker"
   acceptable for the v1 patch?

3. Tool-name collisions: every worker registers tools as
   `mcp_eureka_mcp_<tool>`. The global registry is per-process so
   workers don't actually collide with each other, but worth confirming
   no shared state in `tools/mcp_tool.py` module-level dicts gets
   shared across workers via fork/copy-on-write (workers are
   subprocess, not fork — so likely fine, but verify).

---

## Completed this session (2026-05-11): Profile-worker filesystem sandbox

Shipped Layer 1 + Layer 2 code in a single pass. Layer 2 is no-op until
the container is reconfigured (see "Activation" below).

**Files changed:**
- `tools/_sandbox.py` *(new)* — `enabled()`, `check_path()`,
  `check_paths()`, `bwrap_supported()`, `maybe_wrap_command()`. Single
  source of truth for allowlist + bwrap argv.
- `tools/file_tools.py` — guards at top of `read_file_tool`,
  `write_file_tool`, `patch_tool` (covers V4A multi-file too via
  `_paths_to_check`), and `search_tool`.
- `tools/terminal_tool.py` — `maybe_wrap_command(cmd, env_type)` after
  the dangerous-command guards, before bg/fg branch. Also guards
  `workdir` against the allowlist.
- `hermes_cli/profile_worker_cli.py` — `_apply_sandbox_env()` reads
  `sandbox: strict` from the profile's `config.yaml` and exports
  `HERMES_SANDBOX=strict` before any tool import.
- `Dockerfile` — added `bubblewrap` to the apt install list.
- `docs/profile_worker_sandboxing.md` — status header + operator
  activation checklist.
- `tests/tools/test_sandbox.py` *(new)* — 19 tests covering enabled
  predicate, path acceptance/rejection (incl. symlink + `..` escapes),
  Layer 2 no-op decisions, and e2e through `read/write/search_files`.

**Verification (local):**
- `scripts/run_tests.sh tests/tools/test_sandbox.py` — 19/19 pass.
- `scripts/run_tests.sh tests/tools/test_file_tools.py tests/tools/test_terminal_tool.py` — 37/37 pass (no regression with sandbox off).
- `scripts/run_tests.sh tests/hermes_cli/test_profile_worker_cli.py tests/gateway/test_profile_worker*.py` — 12/12 pass.

**Container blocker (Layer 2 only):** Verified from the VM that
`eureka-hermes` runs with `CapAdd=[]`, `SecurityOpt=[]`,
`Privileged=false`. `bwrap --unshare-user ...` fails inside the
container ("No permissions to create new namespace") because Docker's
default seccomp profile blocks the user-namespace clone syscall. The
`bwrap_supported()` probe detects this at worker startup and falls back
to no-op wrapping — Layer 1 still applies; nothing breaks.

**Activation when ready (operator):**
1. Add `sandbox: strict` to a non-primary profile's
   `/data/hermes-agent/profiles/<name>/config.yaml`. Restart that
   worker (gateway respawns on next message). Layer 1 is live; check
   the worker's `agent.log` for the `"filesystem sandbox active"`
   line.
2. To activate Layer 2: in Coolify, add
   `security_opt: ["seccomp=unconfined"]` (or `cap_add: ["SYS_ADMIN"]`)
   to the `eureka-hermes` service; redeploy; verify with
   `docker exec eureka-hermes bwrap --unshare-user --ro-bind /usr /usr /bin/true`.
   The next worker spawn picks it up automatically.

## Deferred features (not yet scheduled)

- **Coolify config change** for `seccomp=unconfined` on eureka-hermes.
  Code is ready; only the ops flip is outstanding. See activation steps
  above.

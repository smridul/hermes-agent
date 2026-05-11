# Profile worker filesystem sandboxing — design note

Status: **proposed, not implemented**
Captured: 2026-05-11

## The ask

Give profile-worker agents terminal/file access scoped to:

- Read/write within their own profile dir (`$HERMES_HOME` = `/data/hermes-agent/profiles/<name>`)
- Read/write within a per-profile scratch under `/tmp`

…and **no** access to:

- The primary/default profile's dir (`/data/hermes-agent/profiles/default/`)
- Any other profile's dir
- The rest of the container filesystem (beyond what's needed to run Python + shell)

## Why this isn't already true

Profile workers are subprocesses of the gateway and inherit its UID, so by
default they can `cat`, `ls`, `rm` anywhere the gateway user can. Setting
`HERMES_HOME` redirects *Hermes's own* config/session resolution but does
not restrict the kernel-level filesystem view — the agent's `bash` and
file tools still see the full container.

## Three layers, increasing strength

### Layer 1 — Tool-level path guard (Python)

Add a path validator at the entry of every read/write/edit/list tool. The
validator canonicalizes the requested path and rejects it unless it
resolves under `$HERMES_HOME` or `/tmp/<profile-name>/...`.

- **Effort:** ~50 LoC, one new helper + decorator on existing tools.
- **Strength:** guardrail only. Catches well-behaved agents; an LLM that
  reaches for `bash -c 'cat /data/hermes-agent/profiles/default/...'`
  bypasses it because the bash tool spawns a subprocess outside the
  Python guard.
- **Use as:** first line, plus clearer error messages for the agent.

### Layer 2 — `bwrap` (bubblewrap) jail around the bash tool

When the worker invokes its bash tool, wrap the subprocess argv in:

```
bwrap \
  --ro-bind /usr /usr  --ro-bind /lib /lib  --ro-bind /lib64 /lib64 \
  --ro-bind /bin /bin  --ro-bind /etc /etc \
  --bind <profile_dir> <profile_dir> \
  --bind /tmp/<profile>-scratch /tmp \
  --dev /dev --proc /proc \
  bash -c "$cmd"
```

The subprocess **physically cannot see** other profile dirs — the kernel
mount namespace excludes them. This is the real security boundary.

- **Effort:** ~20 LoC at the subprocess-build site in `terminal_tool.py`,
  plus the argv-builder helper.
- **Container reqs:** `apt-get install -y bubblewrap` in the Dockerfile,
  and `--cap-add=SYS_ADMIN` *or* `--security-opt seccomp=unconfined` on
  the container so bwrap can create user namespaces. **Without this,
  bwrap fails at runtime** — needs validation on the Coolify-managed
  container.

### Layer 3 — Per-profile container

Run each worker in its own Docker container with a bind mount of only
its profile dir + scratch. Complete OS-level isolation; biggest rewrite.
Currently out of scope.

## Recommended scope

Ship **Layer 1 + Layer 2** together:
- Layer 1 gives clean error semantics + protects against accidental
  cross-profile access.
- Layer 2 makes it a real boundary against a creative agent.

## File-by-file impact

### Layer 1 — path guard

| File | LoC | Change |
|---|---|---|
| `tools/file_tools.py` | 1125 | Call `_check_allowlist(path)` at the top of each public read/write/edit tool. |
| `tools/file_operations.py` | 1257 | Same treatment. |
| `tools/_sandbox.py` *(new)* | ~80 | Single source of truth: `_check_allowlist(path)`, allowlist resolution from `HERMES_HOME` + `/tmp/<profile>`. |

### Layer 2 — bwrap wrap on terminal_tool

| File | LoC | Change |
|---|---|---|
| `tools/terminal_tool.py` | 2342 | One wrap site. `terminal_tool()` at line 1628 builds subprocess argv around line 2005; prepend bwrap argv when sandbox is enabled. ~20 LoC. |
| `tools/_sandbox.py` *(same)* | +50 | `build_bwrap_argv(profile_dir, scratch_dir, real_argv)`. |

### Worker + container wiring

| File | Change |
|---|---|
| `hermes_cli/profile_worker_cli.py` | Set `os.environ["HERMES_SANDBOX"] = "strict"` (or read from profile config) before `_run_worker()`, so tool modules see it on import. |
| `Dockerfile` | `apt-get install -y bubblewrap`. |
| `gateway/config.py` (per-profile config schema) | Add `sandbox: strict \| off` field to per-profile `config.yaml`. Default off for back-compat. |
| `docker-compose.yml` *(or Coolify env)* | `--cap-add=SYS_ADMIN` or `--security-opt seccomp=unconfined`. |

Total: ~250 LoC across 4-5 files.

## Risks and open questions

1. **Container capabilities.** Coolify-managed containers may not allow
   `--cap-add=SYS_ADMIN`. If denied, bwrap needs the alternative
   `seccomp=unconfined` mode (weaker but still works for namespace
   creation in most kernels). Verify on the live VM before committing.

2. **Python module imports.** Hermes pulls in heavy deps (transformers,
   etc). The bwrap jail needs `--ro-bind /usr/lib/python*` etc. Walk
   through with a smoke test before locking the mount list.

3. **`/tmp` cross-profile leak.** `/tmp/<profile>-scratch` keeps profiles
   apart, but if any tool hardcodes `/tmp/foo.json` that path lands in
   the scratch dir, isolated per profile. Existing code that *expects*
   to share via `/tmp` will break — needs audit.

4. **Existing tools writing outside the allowlist.** Some tools (e.g.
   downloads to `~/Downloads`, cache writes to `~/.cache/hermes`) write
   to user-home paths. Either: (a) redirect those caches into
   `$HERMES_HOME/cache/...` in worker context, or (b) extend the
   allowlist. Survey needed.

5. **Primary profile.** Should the default/primary profile also run
   sandboxed? Probably yes for symmetry, but it has tools (e.g. session
   migration) that legitimately reach across profiles — keep off for
   default, on for non-primary workers in v1.

## Out of scope for v1

- Per-profile UID separation.
- Network policy (egress restrictions per profile).
- Seccomp filter beyond what bwrap provides.
- Memory/CPU quotas per worker.

#!/bin/bash
# Docker/Podman entrypoint: bootstrap config files into the mounted volume, then run hermes.
set -e

HERMES_HOME="${HERMES_HOME:-/opt/data}"
INSTALL_DIR="/opt/hermes"

# --- Privilege dropping via gosu ---
# When started as root (the default for Docker, or fakeroot in rootless Podman),
# optionally remap the hermes user/group to match host-side ownership, fix volume
# permissions, then re-exec as hermes.
if [ "$(id -u)" = "0" ]; then
    if [ -n "$HERMES_UID" ] && [ "$HERMES_UID" != "$(id -u hermes)" ]; then
        echo "Changing hermes UID to $HERMES_UID"
        usermod -u "$HERMES_UID" hermes
    fi

    if [ -n "$HERMES_GID" ] && [ "$HERMES_GID" != "$(id -g hermes)" ]; then
        echo "Changing hermes GID to $HERMES_GID"
        # -o allows non-unique GID (e.g. macOS GID 20 "staff" may already exist
        # as "dialout" in the Debian-based container image)
        groupmod -o -g "$HERMES_GID" hermes 2>/dev/null || true
    fi

    echo "Fixing $HERMES_HOME ownership for hermes"
    # In rootless Podman the container's "root" is mapped to an unprivileged
    # host UID — chown will fail.  That's fine when the volume is already owned
    # by the mapped user on the host side.
    chown -R hermes:hermes "$HERMES_HOME" 2>/dev/null || \
        echo "Warning: chown failed (rootless container?) — continuing anyway"

    echo "Dropping root privileges"
    exec gosu hermes "$0" "$@"
fi

# --- Running as hermes from here ---
source "${INSTALL_DIR}/.venv/bin/activate"

# Create essential directory structure.  Cache and platform directories
# (cache/images, cache/audio, platforms/whatsapp, etc.) are created on
# demand by the application — don't pre-create them here so new installs
# get the consolidated layout from get_hermes_dir().
# The "home/" subdirectory is a per-profile HOME for subprocesses (git,
# ssh, gh, npm …).  Without it those tools write to /root which is
# ephemeral and shared across profiles.  See issue #4426.
mkdir -p "$HERMES_HOME"/{cron,sessions,logs,hooks,memories,skills,skins,plans,workspace,home}

# .env
if [ ! -f "$HERMES_HOME/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
fi

# config.yaml
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
    cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
fi

# SOUL.md
if [ ! -f "$HERMES_HOME/SOUL.md" ]; then
    cp "$INSTALL_DIR/docker/SOUL.md" "$HERMES_HOME/SOUL.md"
fi

# Sync bundled skills (manifest-based so user edits are preserved)
if [ -d "$INSTALL_DIR/skills" ]; then
    python3 "$INSTALL_DIR/tools/skills_sync.py"
fi

if [ "$#" -eq 0 ] || [ "$1" = "serve" ] || [ "$1" = "coolify" ]; then
    dashboard_host="${HERMES_DASHBOARD_HOST:-0.0.0.0}"
    dashboard_port="${HERMES_DASHBOARD_PORT:-9119}"

    echo "Starting Hermes Gateway in background..."
    hermes gateway run --replace &
    gateway_pid="$!"

    echo "Starting Hermes Dashboard on ${dashboard_host}:${dashboard_port}..."
    hermes dashboard \
        --host "$dashboard_host" \
        --port "$dashboard_port" \
        --no-open \
        --insecure &
    dashboard_pid="$!"

    shutdown() {
        kill -TERM "$gateway_pid" "$dashboard_pid" 2>/dev/null || true
        wait "$gateway_pid" "$dashboard_pid" 2>/dev/null || true
    }

    trap shutdown INT TERM
    set +e
    wait -n "$gateway_pid" "$dashboard_pid"
    status="$?"
    set -e
    shutdown
    exit "$status"
fi

exec hermes "$@"

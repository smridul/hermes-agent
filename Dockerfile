FROM ghcr.io/astral-sh/uv:0.11.6-python3.13-trixie@sha256:b3c543b6c4f23a5f2df22866bd7857e5d304b67a564f4feab6ac22044dde719b AS uv_source
FROM tianon/gosu:1.19-trixie@sha256:3b176695959c71e123eb390d427efc665eeb561b1540e82679c15e992006b8b9 AS gosu_source
FROM debian:13.4

# Disable Python stdout buffering to ensure logs are printed immediately
ENV PYTHONUNBUFFERED=1

# Store Playwright browsers outside the volume mount so the build-time
# install survives the /opt/data volume overlay at runtime.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright

# Install system dependencies in one layer, clear APT cache
# tini reaps orphaned zombie processes (MCP stdio subprocesses, git, bun, etc.)
# that would otherwise accumulate when hermes runs as PID 1. See #15012.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential curl nodejs npm python3 ripgrep ffmpeg gcc python3-dev libffi-dev procps git openssh-client docker-cli tini tmux \
    vim less bash-completion bubblewrap \
    zsh zsh-autosuggestions zsh-syntax-highlighting fzf && \
    rm -rf /var/lib/apt/lists/*

# Non-root user for runtime; UID can be overridden via HERMES_UID at runtime
RUN useradd -u 10000 -m -d /opt/data hermes

COPY --chmod=0755 --from=gosu_source /gosu /usr/local/bin/
COPY --chmod=0755 --from=uv_source /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

WORKDIR /opt/hermes

# ---------- Layer-cached dependency install ----------
# Copy only package manifests first so npm install + Playwright are cached
# unless the lockfiles themselves change.
#
# ui-tui/packages/hermes-ink/ is copied IN FULL (not just its manifests)
# because it is referenced as a `file:` workspace dependency from
# ui-tui/package.json.  Copying the tree up front lets npm resolve the
# workspace to real content instead of stopping at a bare package.json.
COPY package.json package-lock.json ./
COPY web/package.json web/package-lock.json web/
COPY ui-tui/package.json ui-tui/package-lock.json ui-tui/
COPY ui-tui/packages/hermes-ink/ ui-tui/packages/hermes-ink/

# `npm_config_install_links=false` forces npm to install `file:` deps as
# symlinks (the npm 10+ default) even on Debian's older bundled npm 9.x,
# which defaults to `install-links=true` and installs file deps as *copies*.
# The host-side package-lock.json is generated with a newer npm that uses
# symlinks, so an install-as-copy produces a hidden node_modules/.package-lock.json
# that permanently disagrees with the root lock on the @hermes/ink entry.
# That disagreement trips the TUI launcher's `_tui_need_npm_install()`
# check on every startup and triggers a runtime `npm install` that then
# fails with EACCES (node_modules/ is root-owned from build time).
ENV npm_config_install_links=false

RUN npm install --prefer-offline --no-audit && \
    npx playwright install --with-deps chromium --only-shell && \
    (cd web && npm install --prefer-offline --no-audit) && \
    (cd ui-tui && npm install --prefer-offline --no-audit) && \
    npm cache clean --force

# ---------- Source code ----------
# .dockerignore excludes node_modules, so the installs above survive.
COPY --chown=hermes:hermes . .

# Build browser dashboard and terminal UI assets.
RUN cd web && npm run build && \
    cd ../ui-tui && npm run build

# ---------- Permissions ----------
# Make install dir world-readable so any HERMES_UID can read it at runtime.
# The venv needs to be traversable too.
USER root
RUN chmod -R a+rX /opt/hermes

# ---------- Smart interactive shell (zsh + autosuggestions + history search) ----------
# Activated for any user who shells in via `docker exec -it <container> zsh`.
# Mac-like behaviours: gray inline suggestions, live syntax highlighting,
# Up/Down history-prefix search, Ctrl-R fzf history, Ctrl-T fzf file finder,
# case-insensitive tab completion.  Bash falls back to a slimmer setup in
# /etc/profile.d/hermes-shell.sh below.
# Debian's /etc/zsh/zshrc does not source /etc/zsh/zshrc.d/ by default,
# so drop a tiny sourcer alongside the customizations.
RUN cat >> /etc/zsh/zshrc <<'EOF'

# Source Hermes customizations dropped into /etc/zsh/zshrc.d/.
if [ -d /etc/zsh/zshrc.d ]; then
    for f in /etc/zsh/zshrc.d/*.zsh; do
        [ -r "$f" ] && source "$f"
    done
    unset f
fi
EOF

RUN mkdir -p /etc/zsh/zshrc.d && cat > /etc/zsh/zshrc.d/hermes.zsh <<'EOF'
HISTFILE=$HOME/.zsh_history
HISTSIZE=20000
SAVEHIST=20000
setopt INC_APPEND_HISTORY HIST_IGNORE_ALL_DUPS HIST_FIND_NO_DUPS HIST_REDUCE_BLANKS SHARE_HISTORY
setopt AUTO_CD INTERACTIVE_COMMENTS NO_BEEP EXTENDED_GLOB
autoload -Uz compinit && compinit -u
zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}' 'r:|=*' 'l:|=* r:|=*'
zstyle ':completion:*' menu select
autoload -Uz history-search-end
zle -N history-beginning-search-backward-end history-search-end
zle -N history-beginning-search-forward-end history-search-end
bindkey "\e[A" history-beginning-search-backward-end
bindkey "\e[B" history-beginning-search-forward-end
[ -r /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh ] && . /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh
[ -r /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ] && . /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
[ -r /usr/share/doc/fzf/examples/key-bindings.zsh ] && . /usr/share/doc/fzf/examples/key-bindings.zsh
[ -r /usr/share/doc/fzf/examples/completion.zsh ] && . /usr/share/doc/fzf/examples/completion.zsh
autoload -Uz colors && colors
PROMPT='%F{green}%n@hermes%f:%F{blue}%~%f%# '
alias ls='ls --color=auto'
alias ll='ls -lah'
alias grep='grep --color=auto'
export LESS='-R'
EOF

# Bash users get tab-completion + a sane prompt via /etc/profile.d/.
RUN cat > /etc/profile.d/hermes-shell.sh <<'EOF'
if [ -n "$BASH_VERSION" ] && [ -z "$BASH_COMPLETION_VERSINFO" ]; then
    if [ -r /usr/share/bash-completion/bash_completion ]; then
        . /usr/share/bash-completion/bash_completion
    fi
fi
if [[ $- == *i* ]]; then
    export PS1='\[\e[32m\]\u@hermes\[\e[0m\]:\[\e[34m\]\w\[\e[0m\]\$ '
    export LESS='-R'
    alias ls='ls --color=auto'
    alias ll='ls -lah'
    alias grep='grep --color=auto'
fi
EOF
RUN chmod 0644 /etc/profile.d/hermes-shell.sh

# Make zsh the default shell for the hermes runtime user so `docker exec
# -it <container> $SHELL` (or just no-shell-flag) lands you in the smart shell.
RUN chsh -s /usr/bin/zsh hermes

# Start as root so the entrypoint can usermod/groupmod + gosu.
# If HERMES_UID is unset, the entrypoint drops to the default hermes user (10000).

# ---------- Python virtualenv ----------
RUN uv venv && \
    uv pip install --no-cache-dir -e ".[all]"

# ---------- Runtime ----------
USER root
ENV HERMES_WEB_DIST=/opt/hermes/hermes_cli/web_dist
ENV HERMES_HOME=/opt/data
ENV PATH="/opt/data/.local/bin:${PATH}"
VOLUME [ "/opt/data" ]
EXPOSE 9119
ENTRYPOINT [ "/usr/bin/tini", "-g", "--", "/opt/hermes/docker/entrypoint.sh" ]
CMD [ "serve" ]

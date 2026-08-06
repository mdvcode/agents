#!/bin/sh

set -eu

OFFICIAL_SOURCE="git+https://github.com/mdvcode/agents.git"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)

if [ -n "${AI_HARNESS_INSTALL_SOURCE:-}" ]; then
    INSTALL_SOURCE=$AI_HARNESS_INSTALL_SOURCE
elif [ -f "$SCRIPT_DIR/pyproject.toml" ] && grep -q 'name = "ai-harness"' "$SCRIPT_DIR/pyproject.toml"; then
    INSTALL_SOURCE=$SCRIPT_DIR
else
    INSTALL_SOURCE=$OFFICIAL_SOURCE
fi

PYTHON_BIN=${AI_HARNESS_PYTHON:-}
if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(command -v python3 || true)
fi
if [ -z "$PYTHON_BIN" ]; then
    printf '%s\n' "Installation stopped: Python 3.11 or newer is required."
    printf '%s\n' "Install Python from https://www.python.org/downloads/ and run this command again."
    exit 2
fi
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    printf '%s\n' "Installation stopped: Python 3.11 or newer is required."
    printf '%s\n' "Current Python: $PYTHON_BIN"
    exit 2
fi

PIPX_BIN=${AI_HARNESS_PIPX:-}
if [ -z "$PIPX_BIN" ]; then
    PIPX_BIN=$(command -v pipx || true)
fi

run_pipx() {
    if [ -n "$PIPX_BIN" ]; then
        "$PIPX_BIN" "$@"
    else
        "$PYTHON_BIN" -m pipx "$@"
    fi
}

if [ -z "$PIPX_BIN" ]; then
    if command -v brew >/dev/null 2>&1; then
        printf '%s\n' "Installing the application manager..."
        brew install pipx
        PIPX_BIN=$(command -v pipx || true)
    else
        if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
            printf '%s\n' "Installation stopped: pip is unavailable for $PYTHON_BIN."
            printf '%s\n' "Install a current Python from https://www.python.org/downloads/ and try again."
            exit 2
        fi
        printf '%s\n' "Installing the application manager..."
        "$PYTHON_BIN" -m pip install --user pipx
    fi
fi

printf '%s\n' "Installing AI Harness..."
run_pipx install --force --python "$PYTHON_BIN" "$INSTALL_SOURCE"
run_pipx ensurepath >/dev/null 2>&1 || true

PIPX_BIN_DIR=$(run_pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)
AGENT_BIN="$PIPX_BIN_DIR/agent"
if [ ! -x "$AGENT_BIN" ]; then
    AGENT_BIN=$(command -v agent || true)
fi
if [ -z "$AGENT_BIN" ] || [ ! -x "$AGENT_BIN" ]; then
    printf '%s\n' "AI Harness was installed, but the agent command is not on PATH yet."
    printf '%s\n' "Open a new Terminal window and run: agent --version"
    exit 1
fi

VERSION=$($AGENT_BIN --version)
printf '\n%s\n' "Installed: $VERSION"
printf '%s\n' "Use it in a project:"
printf '%s\n' "  cd /path/to/your-project"
printf '%s\n' "  agent init"
printf '%s\n' "  git add .agent/project.yaml AGENTS.md"
printf '%s\n' "  git commit -m \"Configure agent workflow\""
printf '%s\n' "  agent doctor --full"
printf '%s\n' "  agent task \"Describe what to do\""
printf '%s\n' "Update later with: agent update"

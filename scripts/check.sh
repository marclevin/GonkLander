#!/usr/bin/env bash
# Everything CI runs, in the same order. Run this before pushing.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

step "Format";     uv run ruff format --check .
step "Lint";       uv run ruff check .
step "Types";      uv run mypy
step "Tests";      uv run pytest

step "Shell scripts"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck lander/install.sh lander/shim/gonk scripts/*.sh
else
    uvx --quiet --from shellcheck-py shellcheck lander/install.sh lander/shim/gonk scripts/*.sh
fi

printf '\n\033[32mAll checks passed.\033[0m\n'

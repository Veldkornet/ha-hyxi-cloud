#!/usr/bin/env bash
# Runs once inside the devcontainer service after it's created. Mirrors the
# "Install dependencies" step of .github/workflows/tests.yml as closely as
# possible so a passing local run means the same thing CI's run does.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Pinned rather than the bare .../uv/install.sh (always "latest"): a fixed
# version means what's installed here is the version this setup was last
# validated against, not whatever astral happens to be serving the moment
# someone opens the devcontainer. Bump deliberately, like a lockfile.
curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/0.12.3/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# The .venv named volume (see docker-compose.yml) is created root-owned by
# Docker on first use; postCreateCommand runs as the non-root `vscode` user,
# so hand the mount point over before `uv sync` tries to write into it.
sudo chown -R "$(id -u):$(id -g)" .venv

# UV_LOCKED and UV_NO_INSTALL_PROJECT (same two flags tests.yml sets at the
# job level, so `uv sync` behaves like CI's rather than uv's looser
# interactive defaults) come from devcontainer.json's `remoteEnv` -- per the
# devcontainer spec that's injected into lifecycle scripts too, so this
# script already runs with them set.
uv sync --extra test
uv tool install pre-commit
pre-commit install

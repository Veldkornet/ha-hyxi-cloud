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
#
# Downloaded to a temp file and checksummed before execution, rather than
# piped straight into `sh`, so a compromised or corrupted download is caught
# before anything runs. Recompute UV_INSTALL_SHA256 whenever
# UV_INSTALL_VERSION is bumped: `curl ... -o f && sha256sum f`.
UV_INSTALL_VERSION="0.12.3"
UV_INSTALL_SHA256="a7e3924ea1cd06bf1518c577d635c624ae2e2db030e0fc8ff8cf426224384e17"
uv_install_script="$(mktemp)"
trap 'rm -f "$uv_install_script"' EXIT
curl --proto '=https' --tlsv1.2 -LsSf "https://astral.sh/uv/${UV_INSTALL_VERSION}/install.sh" -o "$uv_install_script"
echo "${UV_INSTALL_SHA256}  ${uv_install_script}" | sha256sum -c -
sh "$uv_install_script"
export PATH="$HOME/.local/bin:$PATH"

# The .venv named volume (see docker-compose.yml) is created root-owned by
# Docker on first use; postCreateCommand runs as the non-root `vscode` user,
# so hand the mount point over before `uv sync` tries to write into it. Only
# the mount point itself is ever root-owned -- everything created inside it
# afterwards is already owned by this user -- so check before recursing:
# unconditional -R would re-walk an ever-growing venv on every rebuild.
if [[ "$(stat -c '%u' .venv)" != "$(id -u)" ]]; then
  sudo chown "$(id -u):$(id -g)" .venv
fi

# UV_LOCKED and UV_NO_INSTALL_PROJECT (same two flags tests.yml sets at the
# job level, so `uv sync` behaves like CI's rather than uv's looser
# interactive defaults) come from devcontainer.json's `remoteEnv` -- per the
# devcontainer spec that's injected into lifecycle scripts too, so this
# script already runs with them set.
uv sync --extra test
uv tool install pre-commit
pre-commit install

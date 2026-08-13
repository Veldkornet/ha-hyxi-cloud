#!/usr/bin/env bash
# Runs on the *host* (your machine, or the Codespaces VM) before
# docker-compose brings up the dev container stack -- see initializeCommand
# in devcontainer.json. This is the only hook that runs early enough to
# matter here: ../dev_env/docker-compose.yml bind-mounts a sibling checkout
# of hyxi-cloud-api at ../../hyxi-cloud-api (relative to dev_env/), and that
# mount is evaluated by the host Docker daemon at container-creation time --
# a postCreateCommand running *inside* the container would be too late to
# influence it.
#
# Locally this is a no-op if you already keep hyxi-cloud-api checked out
# next to this repo (e.g. both under ~/Git/). In a fresh Codespace, or any
# clone that doesn't have that sibling yet, it clones a read-only copy so
# the mount has something real to serve instead of silently binding an
# empty directory.
set -euo pipefail

# Fail with a clear, specific message rather than whatever raw error the
# first `docker`/`git` call below would otherwise surface -- this runs
# before either container exists, so it's the first thing that can go
# wrong in a fresh environment.
if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker is not installed (or not on PATH) -- the dev container needs Docker to build and run." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "error: docker is installed but not reachable -- is the Docker daemon (Docker Desktop, colima, etc.) running?" >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "error: git is not installed (or not on PATH) -- needed to clone the hyxi-cloud-api sibling checkout below." >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sibling="$(dirname "$repo_root")/hyxi-cloud-api"

# dev_env/docker-compose.yml also hard-codes container_name: ha_dev_hyxi and
# static host ports 8123/5678, since dev_env/manage.sh's `start` action
# relies on that fixed name. That means this stack and a manage.sh-started
# stack can't run at the same time -- whichever comes up second would hit a
# raw "container name already in use" / "port is already allocated" error
# from Docker. Catch it here with a clearer message instead.
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx ha_dev_hyxi; then
  echo "error: a container named ha_dev_hyxi is already running (started via dev_env/manage.sh?)." >&2
  echo "       Stop it first: ./dev_env/manage.sh stop" >&2
  exit 1
fi

if [ -d "$sibling" ]; then
  exit 0
fi

echo "hyxi-cloud-api not found at $sibling -- cloning a read-only copy for dev_env's live-HA testing mount..."
# Clone to a temp dir and move it into place only on success, so an
# interrupted clone (killed session, dropped network) can't leave a
# half-cloned directory at $sibling that the `-d` check above would then
# treat as "already there" on every future run.
tmp="${sibling}.partial-$$"
rm -rf "$tmp"
if git clone --depth 1 https://github.com/Veldkornet/hyxi-cloud-api.git "$tmp"; then
  mv "$tmp" "$sibling"
else
  rm -rf "$tmp"
  echo "warning: could not clone hyxi-cloud-api (no network?) -- the homeassistant service's cross-repo mount will be empty until you clone it yourself at $sibling" >&2
fi

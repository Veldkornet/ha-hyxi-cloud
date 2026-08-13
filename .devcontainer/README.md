# Dev Container

Opens this repo in a Python 3.14 shell with `uv`, `pre-commit`, Ruff, and
mypy set up to match CI (`.github/workflows/tests.yml`), alongside a live
Home Assistant instance for manual testing.

## How the pieces fit together

- **`docker-compose.yml`** (this folder) defines the `devcontainer` service
  VS Code / Codespaces attaches to -- this repo mounted into a plain dev
  shell, with `.venv` shadowed by its own named volume rather than sharing
  the repo's bind mount. Without that, `uv sync` running in this (Linux)
  container would silently overwrite a host-side `.venv` (macOS, say) with
  Linux binaries -- broken outside the container the next time you use it
  there. This isn't hypothetical: it happened during testing, and the fix
  is exactly this indirection.
- **`../dev_env/docker-compose.yml`** defines the `homeassistant` service:
  a real HA instance with `custom_components/hyxi_cloud` live-mounted, the
  same one `dev_env/manage.sh` drives directly with `docker compose up -d`
  (container name `ha_dev_hyxi`, ports `8123`/`5678`). This file's
  `include:` pulls it in unmodified, rather than devcontainer.json listing
  both files in `dockerComposeFile` — `include:` resolves the included
  file's relative paths against its own directory, so `dev_env/`'s paths
  keep meaning what `manage.sh` needs them to mean, and this file's own
  paths resolve against `.devcontainer/`, independently — no dependency on
  which file happens to come first or how deep either directory is nested
  (verified directly, not assumed — see the comment at the top of
  `docker-compose.yml`). Because the container name and ports are fixed,
  this stack and a `manage.sh`-started one can't run at the same time;
  `initialize.sh` checks for that and fails with a clear message rather
  than letting Docker's own error be the first you see.
- **`initialize.sh`** runs on the *host* (your machine, or the Codespaces
  VM) before either container starts. `dev_env/docker-compose.yml`
  bind-mounts a sibling checkout of
  [hyxi-cloud-api](https://github.com/Veldkornet/hyxi-cloud-api) at
  `../../hyxi-cloud-api` (relative to `dev_env/`) for cross-repo debugging.
  Locally that's a no-op if you already keep both repos side by side (e.g.
  under `~/Git/`); in a fresh Codespace it clones a read-only copy so the
  mount isn't silently empty. Unlike `manage.sh`'s `start` action, nothing
  here runs `pip install -e` on that checkout inside the HA container. The
  mount plus `PYTHONPATH` already make `hyxi_cloud_api` importable, and its
  only runtime dependency (`aiohttp`) ships with the HA base image, so this
  has been verified to behave the same as `manage.sh start` without that
  step. If a future `hyxi-cloud-api` change adds a dependency `aiohttp`
  doesn't cover, or you want the checkout's packaging metadata/entry points
  to exist too, run `docker exec ha_dev_hyxi pip install -e
  /workspaces/hyxi-cloud-api` manually to match `manage.sh` exactly.
- **`postCreate.sh`** runs once inside the `devcontainer` service: installs
  `uv`, runs `uv sync --extra test`, and installs the repo's `pre-commit`
  hooks. `devcontainer.json`'s `remoteEnv` sets the same `UV_LOCKED=1` /
  `UV_NO_INSTALL_PROJECT=1` CI sets at the job level, so it's already in
  effect here (`remoteEnv` covers lifecycle scripts, not just terminals) —
  one place to look, not two.

## Using it

- **Home Assistant UI:** forwarded on `8123` once the container is up.
- **debugpy:** forwarded on `5678` for attaching a remote debugger to the
  `homeassistant` container.
- **Running tests:** same commands as CI --
  `uv run pytest tests/ --ignore=tests/integration` and, for the
  integration suite, `HYXI_INTEGRATION_TEST=1 uv run pytest
  tests/integration` (see the project's CONTRIBUTING.md for why these
  shouldn't be run in isolation).

If you don't need the live HA instance, working directly on your host with
`uv sync --extra test` still works exactly as before -- this dev container
is optional, not a replacement for that.

# Notes for AI coding assistants

Repository facts for tools working on this integration. People should start with [CONTRIBUTING.md](CONTRIBUTING.md); this file adds the conventions a change has to follow.

## What this is

A Home Assistant custom integration for HYXI inverters, batteries and microinverters, with two transports:

- **Cloud**: HYXI's OpenAPI through the [`hyxi-cloud-api`](https://github.com/Veldkornet/hyxi-cloud-api) client, pinned to an exact version in `pyproject.toml`. Optional real-time and alarm push arrive on Home Assistant webhooks.
- **Modbus**: a local RS485 connection (through a TCP gateway or a serial adapter) over Home Assistant's own `modbus` integration.

Battery control (mode commands, battery protection, the Energy Manager) only runs on Modbus entries: HYXI doesn't forward battery commands sent with owners' API keys (`CLOUD_BATTERY_CONTROL_SUPPORTED` in `const.py`).

## Layout (`custom_components/hyxi_cloud/`)

| Module | Holds |
|---|---|
| `__init__.py` | Entry setup, unload, removal and reload; adding devices discovered after setup |
| `coordinator.py` | The cloud polling and discovery coordinators, the device cache, and the API-key checks every fetch goes through |
| `modbus_coordinator.py`, `modbus/` | The Modbus coordinator and the HALO and hybrid register maps and clients |
| `devices.py` | Device registry entries and their parent links |
| `migrations.py` | One-off registry migrations and clean-ups run at setup |
| `webhooks.py`, `subscriptions.py` | Push webhooks and HYXI push subscriptions (one spec per channel: real-time data and alarms) |
| `services.py` | The `hyxi_cloud.*` services |
| `control.py`, `control_verify.py`, `protection.py`, `engine.py` | Battery control primitives, control-result checks, battery protection, the Energy Manager |
| `diagnostics.py` | The diagnostics download, with its redaction |
| `const.py` | Constants, entry-data keys (`CONF_*`, `DATA_*`) and the masking helpers |

## Checks (exactly what CI runs)

```bash
uv sync --extra test
uv run pytest tests/ --ignore=tests/integration -p no:warnings --cov --cov-report=
HYXI_INTEGRATION_TEST=1 uv run pytest tests/integration -p no:warnings --cov --cov-append --cov-report=
uv run coverage report -m --fail-under=100
uv run mypy --config-file mypy.ini
pre-commit run --files <changed files>
```

- Run the unit and integration suites separately, as above. The unit tests replace `homeassistant` and `hyxi_cloud_api` with stubs in `sys.modules`, so a single file run alone, or both suites in one run, can fail in ways CI doesn't.
- Combined coverage must stay at 100%.
- The pre-commit `mypy` hook can't see Home Assistant; `uv run mypy --config-file mypy.ini` is the real type check.

## Tests

- `tests/` holds hand-mocked unit tests; `tests/integration/` runs against a real Home Assistant (`pytest-homeassistant-custom-component`: `hass`, `MockConfigEntry`, `hass_storage`, `aioclient_mock`). Shared integration helpers live in `tests/integration/entries.py`.
- Anything Home Assistant itself validates or drives (entity state and attributes, config and options flows, setup, reload and reauth) needs a test through the real `hass`, not only a mocked one. To exercise the real API client, answer its HTTP requests with `aioclient_mock`, as `tests/integration/test_auth.py` does.
- A test guarding a shipped file (translations, the manifest) reads that file, not a copy.
- Patch a name in the module that looks it up, which is the one importing it, not the one defining it. Patch through the package (`custom_components.hyxi_cloud.…`), never `custom_components.hyxi_cloud.__init__.…`: that path imports a second copy of the module.

## Conventions

- **Translations:** every user-facing string goes in `strings.json` and in all 20 files under `translations/`; `tests/test_translations.py` checks them.
- **Logs and diagnostics:** never log API keys, tokens, serial numbers, plant IDs or addresses as they are. Use `mask_sn`, `mask_subscription_code`, `mask_url` and `mask_sensitive_key_value` from `const.py`; diagnostics remove them with `is_sensitive_key`.
- **Entry data:** use the `CONF_*` and `DATA_*` constants from `const.py`, not string literals.
- **Comments and docstrings** describe what the code does now. History, issue or PR numbers and lint-rule citations belong in the commit message.
- **PR titles** use Conventional Commits (`feat:`, `fix:`, `refactor:`, …); the type drives the release version (see CONTRIBUTING.md).
- **Dependencies:** edit only `pyproject.toml`'s `dependencies`; the `sync-versions` hook regenerates `manifest.json`'s `requirements`. After a `hyxi-cloud-api` release, `uv lock --refresh-package hyxi-cloud-api` can fail for a few minutes until PyPI's package index has it.
- **Issues and PRs are public:** no API keys, serial numbers or plant IDs in them.

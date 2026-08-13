---
name: code-review
description: Project-specific checklist for reviewing pull requests to the HYXi Cloud Home Assistant integration — async/coordinator patterns, entity naming/translation keys, config-entry exception handling, device registry linkage, manifest/version sync, services.yaml drift, secure logging, exception-catch/telemetry-parsing discipline, GitHub Actions hardening, and where Home Assistant's own runtime validation catches things hand-rolled tests miss. Use whenever reviewing a pull request or diff in this repository.
---

# Code Review: HYXi Cloud Home Assistant Integration

This repo is a Home Assistant custom integration. When reviewing a PR, check
the following in addition to general correctness.

## 1. Async correctness
- No synchronous HTTP clients (`requests`, etc.) — cloud calls must go through
  `aiohttp`.
- No `time.sleep()` — use `asyncio.sleep()`.
- Lifecycle methods (`async_setup_entry`, `async_unload_entry`, ...) stay `async`.

## 2. Coordinator pattern
- Data is fetched once in the `DataUpdateCoordinator`, not per-entity.
- Entities inherit from `CoordinatorEntity` and read `self.coordinator.data`.
- Flag any entity making an independent API call instead of going through the
  coordinator.

## 3. Entity naming and translation keys
- New entities set `self._attr_has_entity_name = True`.
- Names come from `self._attr_translation_key`, not a hardcoded
  `self._attr_name`.
- `unique_id` is built from the device serial number plus the sensor key
  (e.g. `f"{serial_number}_{sensor_key}"`) and stays unique.
- A new/renamed key needs to exist in **both** `strings.json` (config/options
  flow UI) and `translations/en.json` (entity/service strings) — they're two
  separate files here, not one generated from the other. If a PR adds an
  English key to `translations/en.json`, check that `scripts/sync_translations.py`
  was run (or ask for it) so the key propagates as an untranslated placeholder
  to the other ~19 locale files instead of silently drifting out of sync.
- Confirm the key actually exists in the shipped file, not just a dict built
  inside the test module — that doesn't prove the real translation file has
  the entry.

## 4. Config entry lifecycle: use the exception HA expects
Which exception a failure raises directly controls what Home Assistant does
next — using the wrong one is a real bug, not just style:
- `ConfigEntryNotReady` (raised in `async_setup_entry`) → HA retries setup
  later. Use for transient connectivity failures at startup.
- `ConfigEntryAuthFailed` (raised from the coordinator's update) → HA starts
  the **reauth flow** (`async_step_reauth` / `async_step_reauth_confirm`).
  Use for invalid/expired credentials. A PR that catches an auth error and
  raises `UpdateFailed` or a generic error instead means the user never sees
  the reauth prompt.
- `UpdateFailed` (raised inside `_async_update_data`) → triggers the
  coordinator's exponential backoff. Use for ordinary API/transient errors
  during polling.
- `HomeAssistantError` → surfaced to the user as the service-call failure
  message (see `cancel_subscription` in `__init__.py`). Use for service
  handlers, with a message that makes sense to an end user.
- New config flow steps that create a config entry should call
  `await self.async_set_unique_id(...)` and `self._abort_if_unique_id_configured()`
  to prevent duplicate entries for the same account/device.

## 5. Device registry relationships
- Devices are identified via `identifiers={(DOMAIN, serial_number)}`.
- Child devices (batteries, energy managers) link to their parent via
  `via_device=(DOMAIN, parent_sn)` so they nest correctly under the parent
  in the HA device page. A new device type should follow this pattern rather
  than registering as a flat, unlinked device.

## 6. `manifest.json` / `pyproject.toml` are the only places to bump version or deps
- The integration version is bumped **only** in
  `custom_components/hyxi_cloud/manifest.json`'s `"version"` field —
  `const.py`'s `VERSION` reads it at import time, and `pyproject.toml`'s
  version is auto-corrected by the `sync-versions` pre-commit hook. Flag a
  PR that hardcodes a version anywhere else.
- Runtime dependencies are changed **only** in `pyproject.toml`'s
  `dependencies` list — `manifest.json`'s `requirements` array is
  auto-regenerated from it by the same hook. Flag a PR that hand-edits
  `requirements` in `manifest.json` directly; that's exactly how a stale,
  unused pin drifted silently in this repo before.
- If a PR relies on a new `hyxi-cloud-api` feature, check the pinned version
  in `pyproject.toml`'s dependencies was actually bumped to a release that
  contains it.

## 7. `services.yaml` must match the real schema
- If a PR changes a registered service's `vol.Schema` (e.g.
  `cancel_subscription` in `__init__.py`), check `services.yaml`'s fields,
  descriptions, and selectors were updated to match — HA's UI is driven by
  `services.yaml`, not the schema itself.

## 8. Logging
- No `print()` — use `_LOGGER = logging.getLogger(__name__)`.
- Debug logs may include raw API payloads for troubleshooting, but never API
  keys, passwords, or other secrets. Identifiers like subscription codes are
  masked before logging (see `mask_subscription_code`) — new log lines
  carrying similarly sensitive values should go through the same kind of
  masking, not raw.

## 9. Sensor state/attribute changes need real Home Assistant validation
- `SensorDeviceClass` / `SensorStateClass` must match the actual value being
  reported (e.g. `SensorStateClass.TOTAL_INCREASING` for energy yields), and
  a sensor's reported value must be a legal member of its declared `options`
  when it's an enum sensor.
- Home Assistant enforces this itself at the `SensorEntity.state` property,
  which only runs once the entity is registered with a real entity platform
  — a hand-rolled `FakeCoordinator`/`SimpleNamespace`-based unit test that
  asserts on internal fields directly can stay green while the real
  framework would reject the state. If a PR changes entity state or
  attributes, look for (or ask for) a test that goes through the real `hass`
  fixture + `MockConfigEntry` under `tests/integration/` (run via
  `HYXI_INTEGRATION_TEST=1`), not just the hand-mocked `tests/` suite.

## 10. Exception-catch discipline
- No bare `except Exception:` / `except BaseException:`, except at the
  top-level boundary of an async task or runner loop where the sole purpose
  is to log via `logging.exception` and keep the loop alive — and that
  boundary catch must carry a comment explaining why it's intentional.
- Multiple exceptions in one `except` are comma-separated without
  surrounding parentheses (PEP 758), unless an `as` clause is present:
  `except ValueError, TypeError:` — not `except (ValueError, TypeError):`.
  This is enforced by a pre-commit hook (`check-exception-parentheses`); see
  `.agents/AGENTS.md`. Don't "fix" this into the parenthesized form — it's
  intentional, not legacy syntax.

## 11. Telemetry and API-value parsing
- Raw values coming from the API or from another entity's state are
  sensor/telemetry data that can be `None`, non-numeric, `NaN`, or `Inf`.
  New code parsing them should go through a guarded helper (the codebase
  already has several: `_get_metric_float`/`_metric_float` in
  sensor.py/protection.py, `_get_ha_state_float`/`_get_coordinator_metric`
  in engine.py) rather than calling `float()`/`int()` directly on the raw
  value with no `try`/`except` around it — an unguarded conversion turns one
  malformed API payload into an unhandled exception.

## 12. GitHub Actions workflow hardening
Only applies when a PR touches `.github/workflows/*`:
- New or modified `uses:` steps pin the action to a full 40-character commit
  SHA with a version comment (e.g. `actions/checkout@<sha> # v7.0.1`), not a
  tag or branch.
- New workflows include `step-security/harden-runner` as their first step
  with `egress-policy: block`.
- `GITHUB_TOKEN` permissions are explicitly declared and scoped to the
  minimum the job needs, not left at the default/broad permission set.

## 13. Typing and lint
- `ruff` clean, PEP 8 compliant.
- Strict type hints (`-> None`, `: dict[str, Any]`, `: str`, ...) wherever
  practical.

## 14. Reuse, simplification, dead code
- Watch for a list/enum/mapping duplicated in two places instead of shared
  from one source — this project has shipped that bug before.
- Watch for properties or helpers that are no longer read anywhere after the
  change — flag them for removal rather than leaving them dead.

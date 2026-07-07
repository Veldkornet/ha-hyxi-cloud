"""DataUpdateCoordinator for HYXI Cloud."""

import logging
from datetime import datetime, timedelta
from typing import Any, TypedDict

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from hyxi_cloud_api import HyxiApiClient

from .const import (
    CONF_BACK_DISCOVERY,
    DOMAIN,
    get_raw_device_code,
    get_software_version,
    mask_sensitive_key_value,
    mask_sn,
    normalize_device_type,
)

_LOGGER = logging.getLogger(__name__)


class HyxiMetadata(TypedDict):
    """Type definition for HYXI Metadata."""

    last_attempts: int
    last_success: datetime | None
    last_error: str | None
    api_status: str


CACHE_MAX_AGE = timedelta(days=7)


def _extract_cached_devices(raw: dict | None) -> dict | None:
    """Extract the device dict from cache storage, handling old and new formats."""
    if not raw:
        return None
    if "devices" in raw:
        return raw["devices"]  # New format: {"cached_at": ..., "devices": {...}}
    return raw  # Old format: bare device dict (expired by _is_cache_expired)


def _is_cache_expired(raw: dict | None) -> bool:
    """Return True if cache data is missing, old-format, or older than CACHE_MAX_AGE."""
    if not raw or "cached_at" not in raw:
        return True  # Old format or missing — treat as expired
    try:
        cached_at = datetime.fromisoformat(raw["cached_at"])
        return dt_util.utcnow() - cached_at > CACHE_MAX_AGE
    except ValueError, TypeError:
        return True


class HyxiDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from HYXI API."""

    def __init__(self, hass: HomeAssistant, client: HyxiApiClient, entry: ConfigEntry):
        """Initialize the coordinator with dynamic interval."""
        interval = entry.options.get("update_interval", 5)

        _LOGGER.debug(
            "Initializing HYXI Coordinator for '%s' with polling interval: %s minutes",
            entry.title,
            interval,
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
            config_entry=entry,
        )
        self.client = client
        self.entry = entry
        self.options = dict(entry.options)
        self.protection_controllers: dict[str, Any] = {}
        self.engine: Any = None

        # 🚀 Store metadata on the object, not in the data dictionary!
        self.hyxi_metadata: HyxiMetadata = {
            "last_attempts": 0,
            "last_success": None,
            "last_error": None,
            "api_status": "Starting",
        }

        # Real-time Webhook Push state tracking
        self.push_enabled: bool = False
        self.subscribe_code: str | None = None
        self.webhook_id: str | None = None
        self.push_url: str | None = None
        self.last_push_received: datetime | None = None
        self.push_status: str = "inactive"
        self.push_error: str | None = None

        # Alarm Webhook Push state tracking
        self.alarm_subscribe_code: str | None = None
        self.alarm_webhook_id: str | None = None
        self.alarm_push_status: str = "inactive"
        self.alarm_push_url: str | None = None
        self.alarm_last_push_received: datetime | None = None

        self.device_store: Store[dict[str, Any]] = Store(
            hass, 1, f"hyxi_cloud_devices_{entry.entry_id}"
        )
        self.known_subscription_codes: list[str] = []

    async def async_preload_cache(self) -> None:
        """Pre-seed coordinator.data from persistent cache before the first API call.

        Called from async_setup_entry before async_config_entry_first_refresh().
        This ensures the coordinator has data immediately if the API is slow or
        unreachable at startup, so the fallback in _async_update_data requires
        no additional disk read and setup completes without ConfigEntryNotReady.
        """
        try:
            raw = await self.device_store.async_load()
            devices = _extract_cached_devices(raw)
            if devices and not _is_cache_expired(raw):
                _LOGGER.debug(
                    "Pre-seeding coordinator from cache (%d devices) before first API call",
                    len(devices),
                )
                self.data = devices  # pylint: disable=attribute-defined-outside-init
                self.hyxi_metadata["api_status"] = "Starting (cached)"
            elif devices:
                _LOGGER.debug(
                    "Cache found but expired (>%d days old), skipping pre-seed",
                    CACHE_MAX_AGE.days,
                )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Cache pre-seed failed, will rely on API for first load")

    async def _async_update_data(self):
        """Fetch data and manage metadata attributes."""
        # Read Discovery Toggle
        allow_discovery = self.entry.options.get(CONF_BACK_DISCOVERY, False)
        _LOGGER.debug(
            "HYXI Recursive device discovery via alarms is %s",
            "ENABLED" if allow_discovery else "DISABLED",
        )

        try:
            result = await self.client.get_all_device_data(
                allow_back_discovery=allow_discovery
            )

            if result == "auth_failed":
                raise ConfigEntryAuthFailed("Invalid API keys or expired token")

            if result is None:
                self.hyxi_metadata["last_attempts"] = 3  # Hard fail after retries
                raise UpdateFailed(
                    "HYXI Cloud unreachable. Check internet or API status."
                )

            # ✅ Success! Update metadata attributes.
            devices = result["data"]

            if not devices:
                _LOGGER.warning(
                    "HYXI Cloud returned success, but no plants or devices were found. "
                    "If your developer email differs from your app email, you must share your Plant "
                    "from the app to the developer email first."
                )
                # The API succeeded — this is an account/plant configuration issue,
                # not a connectivity failure. Loading stale cache here would mask the
                # real cause and incorrectly mark the integration as Offline.
                self.hyxi_metadata["api_status"] = "Degraded"
                self.hyxi_metadata["last_error"] = "API returned 0 devices"
                return self.data or {}
            try:
                await self.device_store.async_save(
                    {"cached_at": dt_util.utcnow().isoformat(), "devices": devices}
                )
            except Exception as save_err:  # pylint: disable=broad-except
                # Intentional broad catch to ensure cache save failures never break the update loop
                _LOGGER.warning("Failed to persist devices to storage: %s", save_err)

            # Warn (but don't fail) when telemetry is empty.
            # Raising UpdateFailed here triggers HA exponential backoff,
            # which compounds polling delays and causes stale-data perception.
            non_collectors = [
                dev_data
                for dev_data in devices.values()
                if normalize_device_type(get_raw_device_code(dev_data)) != "collector"
            ]
            if non_collectors and all(
                not (set(dev_data.get("metrics") or {}) - {"last_seen"})
                for dev_data in non_collectors
            ):
                _LOGGER.warning(
                    "HYXI Cloud returned success but telemetry metrics are empty. "
                    "Sensors may show stale values until next successful poll."
                )

            self.hyxi_metadata["last_attempts"] = result.get("attempts", 1)
            self.hyxi_metadata["last_success"] = dt_util.utcnow()
            self.hyxi_metadata["api_status"] = "Online"
            self.hyxi_metadata["last_error"] = None

            self._merge_metrics(devices)
            self._log_polled_telemetry(devices)

            # Return pure device dictionary
            await self._async_sync_device_metadata(devices)
            return devices

        except (ClientError, TimeoutError, UpdateFailed) as err:
            try:
                raw = await self.device_store.async_load()
                cached_devices = _extract_cached_devices(raw)
                if cached_devices and not _is_cache_expired(raw):
                    self.hyxi_metadata["last_error"] = str(err)
                    self.hyxi_metadata["api_status"] = "Offline"
                    _LOGGER.warning(
                        "HYXI Cloud API fetch failed. Falling back to %d cached devices from storage.",
                        len(cached_devices),
                    )
                    self._merge_metrics(cached_devices)
                    await self._async_sync_device_metadata(cached_devices)
                    return cached_devices
                if cached_devices and _is_cache_expired(raw):
                    _LOGGER.warning(
                        "HYXI Cloud API fetch failed and cache is expired (>%d days old). "
                        "Not loading stale cache.",
                        CACHE_MAX_AGE.days,
                    )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning("Cache fallback recovery failed")

            self._handle_update_error(err)
            raise
        except Exception as err:
            self._handle_update_error(err)
            raise

    def _merge_metrics(self, devices: dict) -> None:
        """Merge pulled metrics with existing cached metrics to preserve push-only keys."""
        if not self.data:
            return

        for sn, dev_data in devices.items():
            if sn in self.data:
                existing_metrics = dict(self.data[sn].get("metrics") or {})
                new_metrics = dev_data.get("metrics") or {}

                # Update existing metrics with new values
                existing_metrics.update(
                    {k: v for k, v in new_metrics.items() if v is not None}
                )

                # Recalculate derived metrics on the merged dataset
                derived = self.client.compute_derived_metrics(
                    existing_metrics, dev_data.get("device_type_code", "")
                )
                existing_metrics.update(derived)

                dev_data["metrics"] = existing_metrics

    def _log_polled_telemetry(self, devices: dict) -> None:
        """Log the polled metrics for visibility."""
        for sn, dev_data in devices.items():
            if "metrics" in dev_data:
                logged_metrics = {
                    k: mask_sensitive_key_value(k, v)
                    for k, v in dev_data["metrics"].items()
                }
                _LOGGER.debug(
                    "HYXI Polled Telemetry for Device %s: %s",
                    mask_sn(sn),
                    logged_metrics,
                )

    def _handle_update_error(self, err: Exception) -> None:
        """Handle exceptions during update and map to HA exceptions."""
        if isinstance(err, (ConfigEntryAuthFailed, UpdateFailed)):
            self.hyxi_metadata["last_error"] = str(err)
            self.hyxi_metadata["api_status"] = "Error"
        elif isinstance(err, (ClientError, TimeoutError)):
            _LOGGER.error("Unexpected error in HYXI update: %s", err)
            self.hyxi_metadata["last_attempts"] += 1
            self.hyxi_metadata["last_error"] = str(err)
            self.hyxi_metadata["api_status"] = "Error"
            raise UpdateFailed(f"Unexpected error: {err}") from err
        else:
            _LOGGER.error("Unhandled exception in HYXI update: %s", err)
            self.hyxi_metadata["last_attempts"] += 1
            self.hyxi_metadata["last_error"] = str(err)
            self.hyxi_metadata["api_status"] = "Error"
            raise UpdateFailed(f"Unhandled exception: {err}") from err

    async def _async_sync_device_metadata(self, devices):
        """Sync software/hardware versions to the Device Registry."""
        dev_reg = dr.async_get(self.hass)
        for sn, dev_data in devices.items():
            # We reuse the logic from sensor.py to generate the exact strings
            # and cache it for the individual sensors to avoid re-calculation
            sw_version = get_software_version(dev_data)
            dev_data["_sw_version_cached"] = sw_version

            device = dev_reg.async_get_device(identifiers={(DOMAIN, sn)})
            if not device:
                continue

            model = dev_data.get("model")
            hw_version = dev_data.get("hw_version")

            # Only update if changed
            if (
                device.model != model
                or device.sw_version != sw_version
                or device.hw_version != hw_version
            ):
                _LOGGER.debug(
                    "Updating device registry for %s: %s", mask_sn(sn), sw_version
                )
                dev_reg.async_update_device(
                    device.id,
                    model=model,
                    sw_version=sw_version,
                    hw_version=hw_version,
                )

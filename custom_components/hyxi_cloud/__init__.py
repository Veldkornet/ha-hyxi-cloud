"""HYXi Cloud Integration for Home Assistant."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .api import HyxiApiClient
from .const import BASE_URL
from .const import CONF_ACCESS_KEY
from .const import CONF_SECRET_KEY
from .const import DOMAIN
from .const import PLATFORMS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HYXi Cloud from a config entry."""

    access_key = entry.data.get(CONF_ACCESS_KEY)
    secret_key = entry.data.get(CONF_SECRET_KEY)

    if not access_key or not secret_key:
        _LOGGER.error("HYXi Integration could not find Access/Secret keys.")
        return False

    session = async_get_clientsession(hass)
    client = HyxiApiClient(access_key, secret_key, BASE_URL, session)

    coordinator = HyxiDataUpdateCoordinator(hass, client, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        _LOGGER.error("Authentication failed during setup")
        raise
    except Exception as err:
        _LOGGER.warning("HYXi Cloud not ready: %s", err)
        raise ConfigEntryNotReady(f"Connection error: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class HyxiDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from HYXi API."""

    def __init__(self, hass: HomeAssistant, client: HyxiApiClient, entry: ConfigEntry):
        """Initialize the coordinator with dynamic interval."""
        interval = entry.options.get("update_interval", 5)

        _LOGGER.debug(
            "Initializing HYXi Coordinator for '%s' with polling interval: %s minutes",
            entry.title,
            interval,
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
        )
        self.client = client
        self.entry = entry

        # 🚀 Store metadata on the object, not in the data dictionary!
        self.hyxi_metadata = {
            "last_attempts": 0,
            "last_success": None,
        }

    async def _async_update_data(self):
        """Fetch data and manage metadata attributes."""
        try:
            result = await self.client.get_all_device_data()

            if result == "auth_failed":
                raise ConfigEntryAuthFailed("Invalid API keys or expired token")

            if result is None:
                self.hyxi_metadata["last_attempts"] = 3  # Hard fail after retries
                raise UpdateFailed(
                    "HYXi Cloud unreachable. Check internet or API status."
                )

            # ✅ Success! Update metadata attributes.
            devices = result["data"]
            self.hyxi_metadata["last_attempts"] = result["attempts"]
            self.hyxi_metadata["last_success"] = dt_util.utcnow().isoformat()

            # Return pure device dictionary
            return devices

        except ConfigEntryAuthFailed:
            raise
        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.error("Unexpected error in HYXi update: %s", err)
            self.hyxi_metadata["last_attempts"] += 1
            raise UpdateFailed(f"Unexpected error: {err}") from err

    def get_battery_summary(self):
        """Calculate aggregated data across all battery units."""
        if not self.data:
            return None

        def safe_float(value):
            try:
                return float(value or 0)
            except (ValueError, TypeError):
                return 0.0

        totals = {
            "total_pbat": 0.0,
            "avg_soc": 0.0,
            "avg_soh": 0.0,
            "bat_charge_total": 0.0,
            "bat_discharge_total": 0.0,
            "bat_charging": 0.0,
            "bat_discharging": 0.0,
            "count": 0,
        }

        for _sn, dev in self.data.items():
            dtype = str(dev.get("device_type_code", "")).upper()
            if any(x in dtype for x in ["BATTERY", "EMS", "HYBRID", "ALL_IN_ONE"]):
                metrics = dev.get("metrics", {})

                totals["total_pbat"] += safe_float(metrics.get("pbat"))
                totals["avg_soc"] += safe_float(metrics.get("batSoc"))
                totals["avg_soh"] += safe_float(metrics.get("batSoh"))
                totals["bat_charge_total"] += safe_float(
                    metrics.get("bat_charge_total")
                )
                totals["bat_discharge_total"] += safe_float(
                    metrics.get("bat_discharge_total")
                )
                totals["bat_charging"] += safe_float(metrics.get("bat_charging"))
                totals["bat_discharging"] += safe_float(metrics.get("bat_discharging"))
                totals["count"] += 1

        if totals["count"] == 0:
            return None

        totals["avg_soc"] = round(totals["avg_soc"] / totals["count"], 1)
        totals["avg_soh"] = round(totals["avg_soh"] / totals["count"], 1)
        return totals


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    _LOGGER.debug("HYXi: Options updated, reloading integration to apply new settings")
    await hass.config_entries.async_reload(entry.entry_id)

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

    # 🛠️ Pass 'entry' to the coordinator so it can read options
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

    # Listen for option changes (the slider) and reload if they happen
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
        # 🛠️ Read interval from options (default to 5 mins)
        interval = entry.options.get("update_interval", 5)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self):
        """Fetch data with first-load check and namespaced metadata."""
        try:
            result = await self.client.get_all_device_data()

            if result == "auth_failed":
                raise ConfigEntryAuthFailed("Invalid API keys or expired token")

            if result is None:
                if not self.data:
                    _LOGGER.error(
                        "Initial fetch failed: No data received from HYXi Cloud."
                    )
                    raise UpdateFailed(
                        "Could not connect to HYXi Cloud for initial setup."
                    )

                _LOGGER.warning("HYXi Cloud unreachable. Using cached data.")
                new_data = dict(self.data)
                old_meta = self.data.get("_metadata", {})

                new_data["_metadata"] = {
                    "cloud_online": False,
                    "last_attempts": 3,
                    "last_success": old_meta.get("last_success"),
                }
                return new_data

            devices = result["data"]
            attempts = result["attempts"]

            return {
                **devices,
                "_metadata": {
                    "cloud_online": True,
                    "last_attempts": attempts,
                    "last_success": dt_util.utcnow().isoformat(),
                },
            }

        except ConfigEntryAuthFailed:
            raise
        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.error("Unexpected error in HYXi update: %s", err)
            if not self.data:
                raise UpdateFailed(f"Setup failed: {err}") from err

            new_data = dict(self.data)
            new_data["_metadata"] = {
                **self.data.get("_metadata", {}),
                "cloud_online": False,
                "last_attempts": 1,
            }
            return new_data

    def get_battery_summary(self):
        """Calculate aggregated data across all battery units."""
        if not self.data:
            return None

        # Helper to prevent crashes if the API returns non-numeric values
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
            # 🛠️ UPDATED: Skip the Namespaced metadata key
            if _sn == "_metadata":
                continue

            dtype = str(dev.get("device_type_code", "")).upper()
            if any(x in dtype for x in ["BATTERY", "EMS", "HYBRID", "ALL_IN_ONE"]):
                metrics = dev.get("metrics", {})

                totals["total_pbat"] += float(metrics.get("pbat", 0) or 0)
                totals["avg_soc"] += float(metrics.get("batSoc", 0) or 0)
                totals["avg_soh"] += float(metrics.get("batSoh", 0) or 0)
                totals["bat_charge_total"] += float(
                    metrics.get("bat_charge_total", 0) or 0
                )
                totals["bat_discharge_total"] += float(
                    metrics.get("bat_discharge_total", 0) or 0
                )
                totals["bat_charging"] += float(metrics.get("bat_charging", 0) or 0)
                totals["bat_discharging"] += float(
                    metrics.get("bat_discharging", 0) or 0
                )
                totals["count"] += 1

        if totals["count"] == 0:
            return None

        totals["avg_soc"] = round(totals["avg_soc"] / totals["count"], 1)
        totals["avg_soh"] = round(totals["avg_soh"] / totals["count"], 1)
        return totals


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)

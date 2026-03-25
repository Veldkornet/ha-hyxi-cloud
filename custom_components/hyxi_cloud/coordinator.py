"""DataUpdateCoordinator for HYXI Cloud."""

import logging
from datetime import timedelta

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from hyxi_cloud_api import HyxiApiClient

from .const import CONF_BACK_DISCOVERY
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _safe_float(value) -> float:
    """Helper to safely convert values to float."""
    try:
        return float(value or 0)
    except (
        ValueError,
        TypeError,
        OverflowError,
    ):
        return 0.0


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

        # 🚀 Store metadata on the object, not in the data dictionary!
        self.hyxi_metadata = {
            "last_attempts": 0,
            "last_success": None,
            "last_error": None,
            "api_status": "Starting",
        }

    async def _async_update_data(self):
        """Fetch data and manage metadata attributes."""
        # Read Discovery Toggle
        allow_discovery = self.entry.options.get(CONF_BACK_DISCOVERY, False)
        _LOGGER.debug(
            "HYXI: Recursive device discovery via alarms is %s",
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
            self.hyxi_metadata["last_attempts"] = result.get("attempts", 1)
            self.hyxi_metadata["last_success"] = dt_util.utcnow()
            self.hyxi_metadata["api_status"] = "Online"
            self.hyxi_metadata["last_error"] = None

            # Return pure device dictionary
            return devices

        except (
            ConfigEntryAuthFailed,
            UpdateFailed,
        ) as err:
            self.hyxi_metadata["last_error"] = str(err)
            self.hyxi_metadata["api_status"] = "Error"
            raise
        except (ClientError, TimeoutError) as err:
            _LOGGER.error("Unexpected error in HYXI update: %s", err)
            self.hyxi_metadata["last_attempts"] += 1
            self.hyxi_metadata["last_error"] = str(err)
            self.hyxi_metadata["api_status"] = "Error"
            raise UpdateFailed(f"Unexpected error: {err}") from err

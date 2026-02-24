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

from .api import HyxiApiClient
from .const import BASE_URL
from .const import CONF_ACCESS_KEY
from .const import CONF_SECRET_KEY
from .const import DOMAIN
from .const import PLATFORMS

# Setup logging
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HYXi Cloud from a config entry."""

    access_key = entry.data.get(CONF_ACCESS_KEY)
    secret_key = entry.data.get(CONF_SECRET_KEY)

    if not access_key or not secret_key:
        _LOGGER.error("HYXi Integration could not find Access/Secret keys.")
        return False

    # 1. Get the shared Home Assistant aiohttp session
    session = async_get_clientsession(hass)

    # 2. Initialize the Async API Client
    client = HyxiApiClient(access_key, secret_key, BASE_URL, session)

    # 3. Setup the Data Coordinator
    coordinator = HyxiDataUpdateCoordinator(hass, client)

    # 4. Fetch initial data
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        # If this hits, Home Assistant WILL show the RECONFIGURE button
        _LOGGER.error("Authentication failed during setup")
        raise
    except Exception as err:
        _LOGGER.warning("HYXi Cloud not ready: %s", err)
        raise ConfigEntryNotReady(f"Connection error: {err}") from err

    # 5. Store the coordinator for use in platforms (sensor.py, etc.)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # 6. Forward the setup to the defined platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload all platforms defined in the PLATFORMS constant
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class HyxiDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from HYXi API."""

    def __init__(self, hass: HomeAssistant, client: HyxiApiClient):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # Update every 5 minutes (300 seconds)
            update_interval=timedelta(seconds=300),
        )
        self.client = client

    async def _async_update_data(self):
        """Fetch data from API asynchronously."""
        try:
            data = await self.client.get_all_device_data()

            if data == "auth_failed":
                # Raise this specifically
                raise ConfigEntryAuthFailed("Invalid API keys or expired token")

            if data is None:
                raise UpdateFailed("Failed to communicate with HYXi Cloud API.")

            return data

        except ConfigEntryAuthFailed:
            # DO NOT log this as an "Unexpected error"
            # Just raise it so Home Assistant sees it
            raise
        except UpdateFailed:
            raise
        except Exception as err:
            # Only log actual unexpected crashes here
            _LOGGER.error("Unexpected error fetching HYXi data: %s", err)
            raise UpdateFailed(f"Error communicating with HYXi API: {err}") from err

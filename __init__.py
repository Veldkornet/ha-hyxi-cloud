import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HyxiApiClient
from .const import DOMAIN, CONF_ACCESS_KEY, CONF_SECRET_KEY, BASE_URL

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HYXi Cloud from a config entry."""
    
    # 1. Safely retrieve keys from the entry data
    # We use .get() so it doesn't crash if the keys aren't there
    access_key = entry.data.get(CONF_ACCESS_KEY)
    secret_key = entry.data.get(CONF_SECRET_KEY)

    if not access_key or not secret_key:
        _LOGGER.error(
            "HYXi Integration could not find Access/Secret keys. "
            "Please delete the integration and add it again."
        )
        return False

    # 2. Initialize the API Client
    client = HyxiApiClient(access_key, secret_key, BASE_URL)

    # 3. Setup the Data Coordinator
    coordinator = HyxiDataUpdateCoordinator(hass, client)
    
    # 4. Fetch initial data so entities have values on startup
    await coordinator.async_config_entry_first_refresh()

    # 5. Store the coordinator for use in sensor.py
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    
    # 6. Forward the setup to the sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry (called when deleting the integration)."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

class HyxiDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from HYXi API."""

    def __init__(self, hass: HomeAssistant, client: HyxiApiClient):
        super().__init__(
            hass, 
            _LOGGER, 
            name=DOMAIN, 
            update_interval=timedelta(seconds=60)
        )
        self.client = client

    async def _async_update_data(self):
        """Fetch data from API using the executor thread to avoid blocking."""
        try:
            # Since 'requests' is synchronous, we run it in the executor
            data = await self.hass.async_add_executor_job(self.client.get_all_device_data)
            
            if data is None:
                raise UpdateFailed("Failed to communicate with HYXi Cloud API")
            
            if not data:
                _LOGGER.warning("HYXi API returned successfully but found no devices")
                
            return data
        except Exception as err:
            raise UpdateFailed(f"Error communicating with HYXi API: {err}")
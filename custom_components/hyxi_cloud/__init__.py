import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HyxiApiClient
from .const import DOMAIN, CONF_ACCESS_KEY, CONF_SECRET_KEY, BASE_URL

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

    # 2. Initialize the NEW Async API Client with the session
    client = HyxiApiClient(access_key, secret_key, BASE_URL, session)

    # 3. Setup the Data Coordinator
    coordinator = HyxiDataUpdateCoordinator(hass, client)
    
    # 4. Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
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
            # 5 minutes is a good balance for cloud polling
            update_interval=timedelta(seconds=300)
        )
        self.client = client

    async def _async_update_data(self):
        """Fetch data from API asynchronously."""
        try:
            # 3. We now AWAIT the call directly. No executor needed!
            data = await self.client.get_all_device_data()
            
            if data is None:
                raise UpdateFailed("Failed to communicate with HYXi Cloud API")
            
            return data
        except Exception as err:
            raise UpdateFailed(f"Error communicating with HYXi API: {err}")
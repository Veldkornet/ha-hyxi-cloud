import sys
sys.path.insert(0, "/workspaces/hyxi-cloud-api/src")
"""HYXi Cloud Integration for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from hyxi_cloud_api import HyxiApiClient

from .const import BASE_URL
from .const import CONF_ACCESS_KEY
from .const import CONF_SECRET_KEY
from .const import DOMAIN
from .const import PLATFORMS
from .coordinator import HyxiDataUpdateCoordinator

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


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    _LOGGER.debug("HYXi: Options updated, reloading integration to apply new settings")
    await hass.config_entries.async_reload(entry.entry_id)

"""HYXI Cloud Integration for Home Assistant."""
# pylint: disable=wrong-import-position

import logging

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from hyxi_cloud_api import HyxiApiClient
from hyxi_cloud_api import __version__ as API_VERSION

from .const import BASE_URL
from .const import CONF_ACCESS_KEY
from .const import CONF_SECRET_KEY
from .const import DOMAIN
from .const import MANUFACTURER
from .const import PLATFORMS
from .const import VERSION
from .coordinator import HyxiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HYXI Cloud from a config entry."""
    _LOGGER.debug(
        "Starting HYXI Cloud Integration (Integration: %s, API: %s)",
        VERSION,
        API_VERSION,
    )

    access_key = entry.data.get(CONF_ACCESS_KEY)
    secret_key = entry.data.get(CONF_SECRET_KEY)

    if not access_key or not secret_key:
        _LOGGER.error("HYXI Integration could not find Access/Secret keys.")
        return False

    session = async_get_clientsession(hass)
    client = HyxiApiClient(access_key, secret_key, BASE_URL, session)

    coordinator = HyxiDataUpdateCoordinator(hass, client, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        _LOGGER.error("Authentication failed during setup")
        raise
    except (
        UpdateFailed,
        ClientError,
        TimeoutError,
    ) as err:
        _LOGGER.warning("HYXI Cloud not ready: %s", err)
        raise ConfigEntryNotReady(f"Connection error: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    device_registry = dr.async_get(hass)

    # Pre-register devices to fix 'via_device' order dependency (Two-pass)
    # Pass 1: Ensure all base devices exist in the registry
    for sn, dev_data in coordinator.data.items():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, sn)},
            name=dev_data.get("device_name", f"Device {sn}"),
            manufacturer=MANUFACTURER,
            model=dev_data.get("model"),
            sw_version=dev_data.get("sw_version"),
            hw_version=dev_data.get("hw_version"),
            serial_number=sn,
        )

    # Pass 2: Establish relationships (Battery -> Inverter, Inverter -> Collector)
    for sn, dev_data in coordinator.data.items():
        metrics = dev_data.get("metrics", {})

        # 1. Handle Battery relationship
        bat_sn = metrics.get("batSn")
        if bat_sn:
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, bat_sn)},
                name=f"Battery {bat_sn}",
                manufacturer=MANUFACTURER,
                model="Energy Storage System",
                serial_number=bat_sn,
                via_device=(DOMAIN, sn),
            )

        # 2. Handle Parent Collector relationship
        parent_sn = metrics.get("parentSn")
        if parent_sn:
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, sn)},
                via_device=(DOMAIN, parent_sn),
            )

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
    _LOGGER.debug("HYXI: Options updated, reloading integration to apply new settings")
    await hass.config_entries.async_reload(entry.entry_id)

"""HYXI Cloud Integration for Home Assistant."""
# pylint: disable=wrong-import-position

import logging

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from hyxi_cloud_api import HyxiApiClient
from hyxi_cloud_api import __version__ as API_VERSION

from .const import (
    BASE_URL_DEFAULT,
    CONF_ACCESS_KEY,
    CONF_EM_ENABLED,
    CONF_EM_FORECAST_ENTITY,
    CONF_EM_FORECAST_POWER_ENTITY,
    CONF_EM_INVERTER_SN,
    CONF_EM_P1_ENTITY,
    CONF_SECRET_KEY,
    DOMAIN,
    MANUFACTURER,
    PLATFORMS,
    VERSION,
    detect_phase_type,
    get_raw_device_code,
    normalize_device_type,
)
from .coordinator import HyxiDataUpdateCoordinator
from .protection import HyxiBatteryProtectionController

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

    # Base URL always defaults to global OpenAPI.
    base_url = entry.data.get("base_url") or BASE_URL_DEFAULT

    session = async_get_clientsession(hass)
    client = HyxiApiClient(access_key, secret_key, base_url, session)

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

    # Two-pass device registration to guarantee correct via_device ordering.
    # Without Pass 1, a child registered before its parent would fail the
    # via_device lookup and appear as an orphaned device in Home Assistant.
    #
    # Pass 1: Register every device as a standalone entry (no relationships).
    #         This ensures all SNs are present in the registry before Pass 2
    #         attempts to link them.
    for sn, dev_data in coordinator.data.items():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, sn)},
            name=dev_data.get("device_name") or f"Device {sn}",
            manufacturer=MANUFACTURER,
            model=dev_data.get("model"),
            sw_version=dev_data.get("sw_version"),
            hw_version=dev_data.get("hw_version"),
            serial_number=sn,
        )

    # Pass 2: Establish parent→child relationships now that all devices exist.
    for sn, dev_data in coordinator.data.items():
        metrics = dev_data.get("metrics", {})

        # 1. Handle Battery relationship.
        #    Guard: if bat_sn is already a first-class device in coordinator.data
        #    it was registered in Pass 1 with full metadata — skip the sparse stub
        #    and just link it via_device to avoid overwriting the full entry.
        bat_sn = metrics.get("batSn")
        if bat_sn:
            if bat_sn in coordinator.data:
                # Already registered with full metadata in Pass 1; just set the link.
                device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={(DOMAIN, bat_sn)},
                    via_device=(DOMAIN, sn),
                )
            else:
                # Battery is not a standalone device — create a minimal stub.
                device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={(DOMAIN, bat_sn)},
                    name=f"Battery {bat_sn}",
                    manufacturer=MANUFACTURER,
                    model="Energy Storage System",
                    serial_number=bat_sn,
                    via_device=(DOMAIN, sn),
                )

        # 2. Handle Parent Collector relationship.
        parent_sn = metrics.get("parentSn")
        if parent_sn:
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, sn)},
                via_device=(DOMAIN, parent_sn),
            )

    _remove_legacy_select_entities(hass, coordinator.data)
    await _async_setup_battery_protection(hass, coordinator)

    # Energy Manager: create engine if configured and enabled
    em_enabled = entry.options.get(CONF_EM_ENABLED, False)
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)
    if em_enabled and em_sn and em_sn in coordinator.data:
        from .engine import EMEntityConfig, EnergyManagerEngine

        em_config = EMEntityConfig(
            sn=em_sn,
            p1_entity=entry.options.get(CONF_EM_P1_ENTITY, ""),
            forecast_entity=entry.options.get(CONF_EM_FORECAST_ENTITY),
            forecast_power_entity=entry.options.get(CONF_EM_FORECAST_POWER_ENTITY),
        )
        engine = EnergyManagerEngine(hass, coordinator, em_config)
        coordinator.engine = engine

        # Register EM virtual device
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{em_sn}_energy_manager")},
            name="Energy Manager",
            manufacturer=MANUFACTURER,
            model="Energy Manager",
            via_device=(DOMAIN, em_sn),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start EM engine after platforms are loaded (entities need to exist first)
    if coordinator.engine is not None:
        await coordinator.engine.async_start()

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is not None:
        if coordinator.engine is not None:
            await coordinator.engine.async_stop()
        for controller in coordinator.protection_controllers.values():
            await controller.async_stop()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    _LOGGER.debug("HYXI: Options updated, reloading integration to apply new settings")
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_legacy_select_entities(hass: HomeAssistant, devices: dict) -> None:
    """Remove obsolete select entities replaced by stateless buttons."""
    registry = er.async_get(hass)
    for sn in devices:
        for unique_id in (
            f"hyxi_{sn}_operating_mode",
            f"hyxi_{sn}_peak_shaving",
        ):
            entity_id = registry.async_get_entity_id("select", DOMAIN, unique_id)
            if entity_id is not None:
                _LOGGER.debug("Removing legacy HYXI select entity %s", entity_id)
                registry.async_remove(entity_id)


async def _async_setup_battery_protection(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Start battery protection on supported battery control devices."""
    from .const import is_battery_control_enabled

    if not is_battery_control_enabled(coordinator.entry, coordinator):
        _LOGGER.debug("Battery control and protection is disabled by user settings")
        return

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if device_type not in ("hybrid_inverter", "all_in_one"):
            continue
        phase = detect_phase_type(dev_data)
        if phase not in ("three_phase", "single_phase"):
            continue

        controller = HyxiBatteryProtectionController(hass, coordinator, sn)
        coordinator.protection_controllers[sn] = controller
        await controller.async_start()

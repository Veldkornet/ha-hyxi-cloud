"""The integration's hyxi_cloud.* services."""

import logging

import probatio as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_register_admin_service

from .const import (
    DOMAIN,
    ENERGY_STORAGE_DEVICE_TYPES,
    device_type_of,
    is_control_capable_device_type,
    is_modbus_entry,
    mask_sn,
    mask_subscription_code,
)
from .coordinator import HyxiDataUpdateCoordinator
from .subscriptions import (
    SubscriptionError,
    _hyxi_holds,
    async_cancel_subscription,
    async_refresh_subscriptions,
)

_LOGGER = logging.getLogger(__name__)


def setup_services(hass: HomeAssistant) -> None:
    """Set up custom services for HYXI Cloud."""

    async def async_handle_cancel_subscription(call) -> None:
        """Handle the cancel_subscription service call."""
        subscribe_code = call.data["subscribe_code"]
        coordinator = _cancel_subscription_coordinator(
            hass, subscribe_code, call.data.get("config_entry_id")
        )
        _LOGGER.info(
            "Manually cancelling HYXI subscription: %s",
            mask_subscription_code(subscribe_code),
        )
        try:
            await async_cancel_subscription(coordinator.client, subscribe_code)
        except Exception as err:
            # HYXI's rejection needs no traceback; anything else does.
            _LOGGER.error(
                "Error manual cancelling HYXI subscription %s: %s",
                mask_subscription_code(subscribe_code),
                err,
                exc_info=not isinstance(err, SubscriptionError),
            )
            raise HomeAssistantError(f"Failed to cancel subscription: {err}") from err
        await _async_refresh_subscriptions_for(hass, coordinator)

    async_register_admin_service(
        hass,
        DOMAIN,
        "cancel_subscription",
        async_handle_cancel_subscription,
        schema=vol.Schema(
            {
                vol.Required("subscribe_code"): vol.All(
                    cv.string, str.strip, vol.Length(min=1)
                ),
                vol.Optional("config_entry_id"): cv.string,
            }
        ),
    )

    _register_set_battery_mode_service(hass)


def _cancel_subscription_coordinator(
    hass: HomeAssistant, subscribe_code: str, entry_id: str | None
) -> HyxiDataUpdateCoordinator:
    """The cloud entry to cancel a subscription with: the one named, else
    the one whose HYXI subscription list holds the code, else the only one."""
    loaded = _loaded_coordinators(hass)
    cloud = {
        loaded_id: coord
        for loaded_id, coord in loaded.items()
        if not is_modbus_entry(coord.entry)
    }
    if entry_id:
        if entry_id in cloud:
            return cloud[entry_id]
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_is_modbus"
            if entry_id in loaded
            else "entry_not_loaded",
        )
    if not cloud:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_cloud_entry"
        )
    if holding := next(
        (coord for coord in cloud.values() if _hyxi_holds(coord, subscribe_code)),
        None,
    ):
        return holding
    if len(cloud) == 1:
        return next(iter(cloud.values()))
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="entry_unknown_for_code"
    )


_BATTERY_MODES = ("idle", "charge", "discharge", "self_consume")


def _register_set_battery_mode_service(hass: HomeAssistant) -> None:
    """Register hyxi_cloud.set_battery_mode."""
    from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID

    hass.services.async_register(
        DOMAIN,
        "set_battery_mode",
        _async_handle_set_battery_mode,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_DEVICE_ID, default=list): vol.All(
                    cv.ensure_list, [cv.string]
                ),
                vol.Optional(ATTR_ENTITY_ID, default=list): vol.All(
                    cv.ensure_list, [cv.string]
                ),
                vol.Required("mode"): vol.In(_BATTERY_MODES),
                vol.Optional("power"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=20000)
                ),
                vol.Optional("force", default=False): cv.boolean,
            }
        ),
    )


async def _async_handle_set_battery_mode(call) -> None:
    """Send a battery mode command to every inverter the call targets."""
    from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
    from homeassistant.exceptions import ServiceValidationError

    from .const import is_battery_control_enabled
    from .control import async_send_battery_mode, preflight_battery_mode

    hass = call.hass
    mode = call.data["mode"]
    power = call.data.get("power")
    force = call.data["force"]

    targets = _resolve_battery_mode_targets(
        hass,
        set(call.data[ATTR_DEVICE_ID]),
        set(call.data[ATTR_ENTITY_ID]),
    )
    if not targets:
        raise ServiceValidationError(
            "No HYXI inverter matched the target -- point the action at the "
            "inverter device (or one of its entities)."
        )

    # Validate every matched inverter before touching any, so a rejected
    # second one doesn't leave the first already switched.
    for entry, coordinator, sn in targets:
        device_type = device_type_of(coordinator.data[sn])
        if not is_control_capable_device_type(entry, device_type):
            raise ServiceValidationError(
                f"{mask_sn(sn)} on {entry.title} can't be switched between "
                "battery modes over the HYXI Cloud; use a local Modbus entry."
                if device_type in ENERGY_STORAGE_DEVICE_TYPES
                and not is_modbus_entry(entry)
                else f"{mask_sn(sn)} on {entry.title} has no battery to switch "
                "between modes."
            )
        if not is_battery_control_enabled(entry):
            raise ServiceValidationError(
                f"Device Control is not enabled for {entry.title}. Turn on "
                "'Device Control & Protection' in the integration options first."
            )
        if not force and _energy_manager_manages(coordinator, sn):
            raise ServiceValidationError(
                f"The Energy Manager is managing {mask_sn(sn)}. Turn it off, or "
                "pass force: true to override it for this one command."
            )
        preflight_battery_mode(coordinator, sn, mode)

    for _entry, coordinator, sn in targets:
        await async_send_battery_mode(hass, coordinator, sn, mode, power=power)


def _resolve_battery_mode_targets(
    hass: HomeAssistant, device_ids: set[str], entity_ids: set[str]
) -> list[tuple[ConfigEntry, HyxiDataUpdateCoordinator, str]]:
    """Resolve a set_battery_mode target to (entry, coordinator, serial) rows.

    Accepts device ids and entity ids (each entity contributes its device);
    a device maps to a HYXI inverter through its ``(DOMAIN, serial)``
    identifier and the coordinator that is currently polling that serial.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    all_device_ids = set(device_ids)
    for entity_id in entity_ids:
        if (entity := ent_reg.async_get(entity_id)) is not None and entity.device_id:
            all_device_ids.add(entity.device_id)

    coordinators = _loaded_coordinators(hass)
    return [
        row
        for device_id in all_device_ids
        if (row := _battery_mode_row(dev_reg.async_get(device_id), coordinators))
    ]


def _battery_mode_row(
    device, coordinators: dict
) -> tuple[ConfigEntry, HyxiDataUpdateCoordinator, str] | None:
    """Map one device to (entry, coordinator, serial), or None if it is not
    a HYXI inverter this integration is currently polling."""
    if device is None:
        return None
    serial = next(
        (ident[1] for ident in device.identifiers if ident[0] == DOMAIN), None
    )
    if serial is None:
        return None
    rows = [
        (coordinator.entry, coordinator, serial)
        for entry_id in device.config_entries
        if (coordinator := coordinators.get(entry_id)) is not None
        and serial in (coordinator.data or {})
    ]
    # A device on both a cloud and a Modbus entry is switched through the
    # entry that can control its battery.
    return next(
        (
            row
            for row in rows
            if is_control_capable_device_type(
                row[0], device_type_of(row[1].data[serial])
            )
        ),
        rows[0] if rows else None,
    )


def _energy_manager_manages(
    coordinator: HyxiDataUpdateCoordinator, serial: str
) -> bool:
    """True when the Energy Manager engine is actively driving `serial`.

    False when there is no engine, its loop is stopped, its ``em_enabled``
    switch is off, it is paused on stale data, or it manages a different
    inverter.
    """
    engine = getattr(coordinator, "engine", None)
    return engine is not None and engine.sn == serial and engine.is_managing


def _loaded_coordinators(
    hass: HomeAssistant,
) -> dict[str, HyxiDataUpdateCoordinator]:
    """The coordinators of the loaded HYXI entries, by entry ID."""
    return {
        entry.entry_id: entry.runtime_data
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
    }


async def _async_refresh_subscriptions_for(
    hass: HomeAssistant, coordinator: HyxiDataUpdateCoordinator
) -> None:
    """Refresh HYXI's subscription list once, for every loaded cloud entry
    with the same credentials as coordinator."""
    await async_refresh_subscriptions(coordinator)
    access_key = getattr(coordinator.client, "access_key", None)
    for other in _loaded_coordinators(hass).values():
        if (
            other is not coordinator
            and not is_modbus_entry(other.entry)
            and getattr(other.client, "access_key", None) == access_key
        ):
            other.subscriptions = coordinator.subscriptions
            other.async_update_listeners()

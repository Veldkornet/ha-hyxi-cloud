"""Device registry entries for a HYXI config entry's devices."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import HyxiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _link_battery_device(
    device_registry: dr.DeviceRegistry,
    entry: ConfigEntry,
    devices: dict[str, dr.DeviceEntry],
    sn: str,
    bat_sn: str,
) -> None:
    """Register/link the battery that ``sn``'s metrics report.

    Guard: a bat_sn that's already a first-class device was registered in
    Pass 1 with full metadata -- only set the link, don't re-send the
    sparse stub over it.
    """
    if bat_sn in devices:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, bat_sn)},
            via_device_id=devices[sn].id,
        )
    else:
        devices[bat_sn] = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, bat_sn)},
            name=f"Battery {bat_sn}",
            manufacturer=MANUFACTURER,
            model="Energy Storage System",
            serial_number=bat_sn,
            via_device_id=devices[sn].id,
        )


def _link_parent_collector(
    device_registry: dr.DeviceRegistry,
    entry: ConfigEntry,
    devices: dict[str, dr.DeviceEntry],
    sn: str,
    parent_sn: str,
) -> None:
    """Link ``sn`` under the parent collector it reports, if registered."""
    parent = devices.get(parent_sn) or device_registry.async_get_device_by_identifier(
        (DOMAIN, parent_sn), entry.entry_id
    )
    if parent is not None:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, sn)},
            via_device_id=parent.id,
        )


def _async_register_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Register all devices and establish parent-child relationships."""
    device_registry = dr.async_get(hass)

    # Two-pass registration so a parent device always exists before Pass 2
    # links a child to it. via_device_id needs the parent's registry id, so
    # Pass 1's return values are kept and reused rather than looked up again.
    #
    # Pass 1: register every device standalone (no relationships).
    devices: dict[str, dr.DeviceEntry] = {}
    for sn, dev_data in coordinator.data.items():
        devices[sn] = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, sn)},
            name=dev_data.get("device_name") or f"Device {sn}",
            manufacturer=MANUFACTURER,
            model=dev_data.get("model"),
            sw_version=dev_data.get("sw_version"),
            hw_version=dev_data.get("hw_version"),
            serial_number=sn,
        )

    # Pass 2: establish parent→child relationships now that all devices exist.
    for sn, dev_data in coordinator.data.items():
        metrics = dev_data.get("metrics", {})

        # 1. Battery hangs off its inverter. Guard: some builds (e.g. HYXI
        #    Halo all-in-one units) report batSn == the device's own sn --
        #    there's no separate battery device to hang off it there, and
        #    linking it would make the device its own via_device, which HA
        #    rejects.
        if (bat_sn := metrics.get("batSn")) and bat_sn != sn:
            _link_battery_device(device_registry, entry, devices, sn, bat_sn)

        # 2. Device hangs off its parent collector. Guard: same
        #    self-reference risk as batSn above.
        if (parent_sn := metrics.get("parentSn")) and parent_sn != sn:
            _link_parent_collector(device_registry, entry, devices, sn, parent_sn)

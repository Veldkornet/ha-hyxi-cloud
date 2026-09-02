"""Base entity for HYXI Cloud."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import HyxiDataUpdateCoordinator


def via_device_id(
    hass: HomeAssistant, config_entry_id: str, parent_sn: str
) -> str | None:
    """Resolve ``(DOMAIN, parent_sn)`` to a device registry id for ``DeviceInfo``.

    Like ``dr.async_get_device_id_by_identifier`` but returns ``None`` instead
    of raising when the parent isn't registered -- a device can report a
    ``parentSn`` for a collector that isn't itself polled, and that just reads
    as "no via device", the same as an unresolvable link used to.
    """
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, parent_sn), config_entry_id
    )
    return device.id if device is not None else None


class HyxiEntity(CoordinatorEntity["HyxiDataUpdateCoordinator"]):
    """Base entity for HYXI Cloud."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HyxiDataUpdateCoordinator, sn: str, dev_data: dict
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

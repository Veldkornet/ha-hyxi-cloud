"""Base entity for HYXI Cloud."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from .coordinator import HyxiDataUpdateCoordinator


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

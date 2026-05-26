"""Switch platform for HYXI Cloud device control."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from hyxi_cloud_api import HyxiApiClient

from .const import (
    DOMAIN,
    detect_phase_type,
    get_raw_device_code,
    mask_sn,
    normalize_device_type,
)
from .entity import HyxiEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HYXI switch entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return

    entities: list[SwitchEntity] = []

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))

        if device_type in ("hybrid_inverter", "all_in_one"):
            phase = detect_phase_type(dev_data)

            # Frequency control (controlId 1020) — single-phase devices only
            if (
                entry.options.get("enable_battery_control", False)
                and phase == "single_phase"
            ):
                entities.append(HyxiFrequencyControlSwitch(coordinator, sn, dev_data))
        # Microinverter power on/off (controlId 3011)
        elif device_type == "micro_inverter":
            if entry.options.get("enable_battery_control", False):
                entities.append(HyxiMicroPowerSwitch(coordinator, sn, dev_data))

    if entities:
        async_add_entities(entities)


class HyxiFrequencyControlSwitch(HyxiEntity, SwitchEntity):
    """Switch entity for Frequency Control enable/disable (controlId 1020).

    State is tracked internally after successful writes as the API does not
    return the current frequency control state in polling responses.
    """

    _attr_translation_key = "frequency_control"
    _attr_icon = "mdi:sine-wave"
    _attr_is_on: bool | None = None

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the frequency control switch."""
        super().__init__(coordinator, sn, dev_data)
        self._attr_unique_id = f"hyxi_{sn}_frequency_control"

    async def async_turn_on(self, **kwargs) -> None:
        """Enable frequency control."""
        client = self.coordinator.client
        try:
            await client.set_frequency_control(self._sn, enabled=True)
            self._attr_is_on = True
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to enable frequency control for %s: %s", mask_sn(self._sn), err
            )
            raise

    async def async_turn_off(self, **kwargs) -> None:
        """Disable frequency control."""
        client = self.coordinator.client
        try:
            await client.set_frequency_control(self._sn, enabled=False)
            self._attr_is_on = False
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to disable frequency control for %s: %s", mask_sn(self._sn), err
            )
            raise


class HyxiMicroPowerSwitch(HyxiEntity, SwitchEntity):
    """Switch entity for Microinverter power on/off (controlId 3011).

    State is tracked internally after successful writes as the API does not
    return the current power state in polling responses.
    """

    _attr_translation_key = "micro_power"
    _attr_icon = "mdi:power"
    _attr_is_on: bool | None = None

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the microinverter power switch."""
        super().__init__(coordinator, sn, dev_data)
        self._attr_unique_id = f"hyxi_{sn}_micro_power"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the microinverter."""
        client = self.coordinator.client
        try:
            await client.set_micro_power_on(self._sn)
            self._attr_is_on = True
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to power on microinverter %s: %s", mask_sn(self._sn), err
            )
            raise

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the microinverter."""
        client = self.coordinator.client
        try:
            await client.set_micro_power_off(self._sn)
            self._attr_is_on = False
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to power off microinverter %s: %s", mask_sn(self._sn), err
            )
            raise

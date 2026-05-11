"""Button platform for HYXI Cloud device control.

Operating mode and peak shaving commands are exposed as buttons rather than
select entities because the HYXI API does not return the current mode in its
polling payload. Buttons are the correct HA abstraction for write-only commands
that have no readable state: pressing a button fires a command; there is no
persistent state to become stale or misleading.
"""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from hyxi_cloud_api import HyxiApiClient

from .const import (
    DOMAIN,
    MANUFACTURER,
    detect_phase_type,
    get_raw_device_code,
    normalize_device_type,
)

_LOGGER = logging.getLogger(__name__)

MODE_ICONS: dict[str, str] = {
    "idle": "mdi:sleep",
    "charge": "mdi:battery-arrow-up",
    "discharge": "mdi:battery-arrow-down",
    "self_consume": "mdi:solar-power-variant-outline",
}

PEAK_SHAVING_ICONS: dict[str, str] = {
    "close": "mdi:chart-bell-curve-cumulative",
    "charge": "mdi:battery-arrow-up",
    "discharge": "mdi:battery-arrow-down",
    "stop": "mdi:stop-circle-outline",
    "hold": "mdi:pause-circle-outline",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HYXI button entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return

    entities: list[ButtonEntity] = []

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))

        # Microinverter: Restart button (controlId 3013)
        if device_type == "micro_inverter":
            entities.append(HyxiMicroRestartButton(coordinator, sn, dev_data))
            continue

        # Operating mode + peak shaving: hybrid_inverter and all_in_one only
        if device_type not in ("hybrid_inverter", "all_in_one"):
            continue

        phase = detect_phase_type(dev_data)
        if phase == "unknown":
            _LOGGER.debug(
                "Cannot determine phase type for %s (model=%s) — "
                "skipping control entities",
                sn,
                dev_data.get("model"),
            )
            continue

        # Three-phase: operating mode buttons (controlIds 1062-1065)
        if phase == "three_phase":
            entities.extend(
                [
                    HyxiModeButton(coordinator, sn, dev_data, "idle"),
                    HyxiModeButton(coordinator, sn, dev_data, "charge"),
                    HyxiModeButton(coordinator, sn, dev_data, "discharge"),
                    HyxiModeButton(coordinator, sn, dev_data, "self_consume"),
                ]
            )

        # Single-phase: peak shaving buttons (controlId 1021)
        if phase == "single_phase":
            for option in ("close", "charge", "discharge", "stop", "hold"):
                entities.append(
                    HyxiPeakShavingButton(coordinator, sn, dev_data, option)
                )

    if entities:
        async_add_entities(entities)


class HyxiMicroRestartButton(CoordinatorEntity, ButtonEntity):
    """Button entity to restart a Microinverter (controlId 3013)."""

    _attr_has_entity_name = True
    _attr_translation_key = "micro_restart"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the microinverter restart button."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_micro_restart"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_press(self) -> None:
        """Restart the microinverter."""
        client = self.coordinator.client
        try:
            await client.restart_device(self._sn)
            _LOGGER.info("Restart command sent to microinverter %s", self._sn)
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error("Failed to restart microinverter %s: %s", self._sn, err)
            raise


class HyxiModeButton(CoordinatorEntity, ButtonEntity):
    """Button to send an operating mode command (three-phase, write-only).

    One button per mode: Idle, Charge, Discharge, Self-Consumption.
    The HYXI API does not expose the current mode in polling data, so a button
    (stateless) is the correct HA abstraction rather than a select (stateful).
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, sn: str, dev_data: dict, mode: str) -> None:
        """Initialize the mode button."""
        super().__init__(coordinator)
        self._sn = sn
        self._mode = mode
        self._attr_unique_id = f"hyxi_{sn}_mode_{mode}"
        self._attr_translation_key = f"mode_{mode}"
        self._attr_icon = MODE_ICONS.get(mode, "mdi:solar-power-variant-outline")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_press(self) -> None:
        """Send the operating mode command to the inverter."""
        client = self.coordinator.client
        try:
            if self._mode == "idle":
                await client.set_mode_idle(self._sn)
            elif self._mode == "charge":
                watts = _get_power_value(self.hass, self._sn, "charge")
                _LOGGER.debug("Setting %s to CHARGE at %dW", self._sn, watts)
                await client.set_mode_charge(self._sn, watts)
            elif self._mode == "discharge":
                watts = _get_power_value(self.hass, self._sn, "discharge")
                _LOGGER.debug("Setting %s to DISCHARGE at %dW", self._sn, watts)
                await client.set_mode_discharge(self._sn, watts)
            elif self._mode == "self_consume":
                await client.set_mode_self_consume(self._sn)
            _LOGGER.info("Mode '%s' command sent to %s", self._mode, self._sn)
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to set mode '%s' for %s: %s", self._mode, self._sn, err
            )
            raise


class HyxiPeakShavingButton(CoordinatorEntity, ButtonEntity):
    """Button to send a peak shaving command (single-phase, write-only).

    One button per action: Close, Charge, Discharge, Stop, Hold.
    The HYXI API does not expose the current peak-shaving state in polling data,
    so buttons are the correct HA abstraction for these write-only commands.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, sn: str, dev_data: dict, option: str) -> None:
        """Initialize the peak shaving button."""
        super().__init__(coordinator)
        self._sn = sn
        self._option = option
        self._attr_unique_id = f"hyxi_{sn}_peak_shaving_{option}"
        self._attr_translation_key = f"peak_shaving_{option}"
        self._attr_icon = PEAK_SHAVING_ICONS.get(
            option, "mdi:chart-bell-curve-cumulative"
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_press(self) -> None:
        """Send the peak shaving command to the inverter."""
        client = self.coordinator.client
        try:
            await client.set_peak_shaving(self._sn, self._option)
            _LOGGER.info("Peak shaving '%s' command sent to %s", self._option, self._sn)
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to send peak shaving '%s' to %s: %s",
                self._option,
                self._sn,
                err,
            )
            raise


def _get_power_value(hass: HomeAssistant, sn: str, direction: str) -> int:
    """Read the wattage from the paired number entity.

    Looks up the entity by unique_id via the entity registry, since HA-assigned
    entity_ids don't follow a predictable pattern.
    Falls back to 100W if the number entity has not been set yet.
    """
    unique_id = f"hyxi_{sn}_{direction}_power"
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("number", DOMAIN, unique_id)
    if entity_id is None:
        _LOGGER.warning(
            "Power number entity (unique_id=%s) not found in registry, using 100W default",
            unique_id,
        )
        return 100
    state = hass.states.get(entity_id)
    if state is not None and state.state not in ("unknown", "unavailable"):
        try:
            return int(float(state.state))
        except ValueError, TypeError:
            pass  # Ignore invalid state values
    _LOGGER.warning(
        "Power number entity %s not available, using 100W default", entity_id
    )
    return 100

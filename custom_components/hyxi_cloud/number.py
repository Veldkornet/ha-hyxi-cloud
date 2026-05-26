"""Number platform for HYXI Cloud device control power settings."""

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from hyxi_cloud_api import HyxiApiClient

from .const import (
    DOMAIN,
    MANUFACTURER,
    detect_phase_type,
    get_raw_device_code,
    mask_sn,
    normalize_device_type,
)

_LOGGER = logging.getLogger(__name__)

_MAX_POWER_KEYS = {
    "charge": "maxChargePower",
    "discharge": "maxDischargePower",
}

PROTECTION_NUMBER_DEFS: list[dict[str, str | int]] = [
    {
        "key": "soc_min",
        "unit": "%",
        "min": 5,
        "max": 50,
        "default": 20,
        "icon": "mdi:battery-20",
    },
    {
        "key": "soc_max",
        "unit": "%",
        "min": 50,
        "max": 100,
        "default": 90,
        "icon": "mdi:battery-90",
    },
    {
        "key": "soc_min_hysteresis_pct",
        "unit": "%",
        "min": 0,
        "max": 10,
        "default": 2,
        "icon": "mdi:battery-sync",
    },
    {
        "key": "soc_max_hysteresis_pct",
        "unit": "%",
        "min": 0,
        "max": 10,
        "default": 2,
        "icon": "mdi:battery-sync-outline",
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HYXI number entities for charge/discharge power settings."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return

    entities: list[NumberEntity] = []

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))

        if device_type in ("hybrid_inverter", "all_in_one"):
            phase = detect_phase_type(dev_data)

            if entry.options.get("enable_battery_control", False):
                # Power numbers pair with mode control (1062-1065) — three-phase only
                # Peak shaving (single-phase) uses full inverter power, no wattage setting
                if phase == "three_phase":
                    entities.append(
                        HyxiPowerNumber(coordinator, sn, dev_data, "charge")
                    )
                    entities.append(
                        HyxiPowerNumber(coordinator, sn, dev_data, "discharge")
                    )

                # SOC protection numbers for both three-phase and single-phase
                if phase in ("three_phase", "single_phase"):
                    for definition in PROTECTION_NUMBER_DEFS:
                        entities.append(
                            HyxiProtectionNumber(coordinator, sn, dev_data, definition)
                        )
        elif device_type == "micro_inverter":
            # Microinverter power limit (controlId 3012)
            if entry.options.get("enable_battery_control", False):
                entities.append(HyxiMicroPowerLimit(coordinator, sn, dev_data))

    if entities:
        async_add_entities(entities)


class HyxiPowerNumber(CoordinatorEntity, NumberEntity, RestoreEntity):
    """Number entity for setting the wattage used by charge/discharge mode commands.

    This entity stores the desired power level locally. The value is sent to
    the inverter when the user presses the Charge or Discharge mode button.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_min_value = 1
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        coordinator,
        sn: str,
        dev_data: dict,
        direction: str,
    ) -> None:
        """Initialize the power number entity."""
        super().__init__(coordinator)
        self._sn = sn
        self._direction = direction
        self._attr_unique_id = f"hyxi_{sn}_{direction}_power"
        self._attr_translation_key = f"{direction}_power"
        metrics = dev_data.get("metrics") or {}
        metric_key = _MAX_POWER_KEYS.get(direction, "")
        self._attr_native_max_value = int(_safe_int(metrics.get(metric_key), 10000))
        self._attr_native_value = 100
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = int(float(last_state.state))
            except ValueError, TypeError:
                pass  # Ignore invalid restored state

    async def async_set_native_value(self, value: float) -> None:
        """Set the power value."""
        self._attr_native_value = int(value)
        self.async_write_ha_state()


class HyxiMicroPowerLimit(CoordinatorEntity, NumberEntity, RestoreEntity):
    """Number entity for microinverter power limit (controlId 3012).

    Sets the maximum power output as a percentage of rated power.
    The value is sent to the inverter immediately on change.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "micro_power_limit"
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1.0
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_value = 100.0
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the micro power limit entity."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_micro_power_limit"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError, TypeError:
                pass  # Ignore invalid restored state

    async def async_set_native_value(self, value: float) -> None:
        """Set the power limit and send to inverter."""
        client = self.coordinator.client
        try:
            await client.set_micro_power_limit(self._sn, int(value))
            self._attr_native_value = value
            self.async_write_ha_state()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to set power limit to %d%% for %s: %s",
                int(value),
                mask_sn(self._sn),
                err,
            )
            raise


def _safe_int(val, default: int) -> int:
    """Safely convert a value to int with a fallback default."""
    if val is None:
        return default
    try:
        result = int(float(val))
        return result if result > 0 else default
    except ValueError, TypeError:
        return default


class HyxiProtectionNumber(CoordinatorEntity, NumberEntity, RestoreEntity):
    """Locally stored number for battery protection thresholds."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator,
        sn: str,
        dev_data: dict,
        definition: dict[str, str | int],
    ) -> None:
        """Initialize the protection number."""
        super().__init__(coordinator)
        self._sn = sn
        key = str(definition["key"])
        self._attr_unique_id = f"hyxi_{sn}_{key}"
        self._attr_translation_key = key
        self._attr_native_unit_of_measurement = str(definition["unit"])
        self._attr_native_min_value = int(definition["min"])
        self._attr_native_max_value = int(definition["max"])
        self._attr_native_value = int(definition["default"])
        self._attr_icon = str(definition["icon"])
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_added_to_hass(self) -> None:
        """Restore the last configured value."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = int(float(last_state.state))
            except ValueError, TypeError:
                _LOGGER.debug(
                    "Could not restore protection number hyxi_%s_%s from state %s",
                    mask_sn(self._sn),
                    self._attr_translation_key,
                    last_state.state,
                )

    async def async_set_native_value(self, value: float) -> None:
        """Set the protection threshold value."""
        self._attr_native_value = int(value)
        self.async_write_ha_state()
        if (
            controller := getattr(self.coordinator, "protection_controllers", {}).get(
                self._sn
            )
        ) is not None:
            self.hass.async_create_task(controller.async_evaluate())

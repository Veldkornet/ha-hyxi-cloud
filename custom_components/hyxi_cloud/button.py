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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from hyxi_cloud_api import VPP_ACTIVE_MODES, HyxiApiClient

from .binary_sensor import ACTIVE_ALARM_STATES
from .const import (
    CONF_ENABLE_PUSH,
    CONF_OVERRIDE_VPP,
    DOMAIN,
    MANUFACTURER,
    detect_phase_type,
    get_raw_device_code,
    is_battery_control_enabled,
    mask_sn,
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


def _is_vpp_active(coordinator, sn: str) -> bool:
    """Return True if a VPP program is currently controlling this device.

    When True, manual control buttons must become unavailable to prevent
    user commands from conflicting with the active VPP dispatch.
    """
    if coordinator.config_entry.options.get(CONF_OVERRIDE_VPP, False):
        return False
    metrics = (coordinator.data.get(sn) or {}).get("metrics", {})
    return str(metrics.get("vppMode")) in VPP_ACTIVE_MODES


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
        # Clear Active Alarms button is available for every device SN
        entities.append(HyxiClearAlarmsButton(coordinator, sn, dev_data))

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
                mask_sn(sn),
                dev_data.get("model"),
            )
            continue

        # Three-phase: operating mode buttons (controlIds 1062-1065)
        if is_battery_control_enabled(entry, coordinator) and phase == "three_phase":
            entities.extend(
                [
                    HyxiModeButton(coordinator, sn, dev_data, "idle"),
                    HyxiModeButton(coordinator, sn, dev_data, "charge"),
                    HyxiModeButton(coordinator, sn, dev_data, "discharge"),
                    HyxiModeButton(coordinator, sn, dev_data, "self_consume"),
                ]
            )

        # Single-phase: peak shaving buttons (controlId 1021)
        if is_battery_control_enabled(entry, coordinator) and phase == "single_phase":
            for option in ("close", "charge", "discharge", "stop", "hold"):
                entities.append(
                    HyxiPeakShavingButton(coordinator, sn, dev_data, option)
                )

    # Expose the Renew Push Subscription button if push is enabled
    if entry.options.get(CONF_ENABLE_PUSH, False) is True:
        entities.append(HyxiRenewSubscriptionButton(coordinator, entry))

    if entities:
        async_add_entities(entities)


class HyxiClearAlarmsButton(CoordinatorEntity, ButtonEntity):
    """Button to clear active alarms for a device."""

    _attr_has_entity_name = True
    _attr_translation_key = "clear_alarms"
    _attr_icon = "mdi:bell-check-outline"

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the clear alarms button."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_clear_alarms"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_press(self) -> None:
        """Press the button to clear active alarms."""
        client = self.coordinator.client
        alarms = (self.coordinator.data.get(self._sn) or {}).get("alarms") or []

        active_ids = []
        for alarm in alarms:
            alarm_state = alarm.get("alarmState")
            if alarm_state is None:
                alarm_state = alarm.get("alarmstate")
            if alarm_state in ACTIVE_ALARM_STATES:
                if alarm.get("endTime") or alarm.get("endtime"):
                    continue
                alarm_id = (
                    alarm.get("id") or alarm.get("alarmId") or alarm.get("alarmid")
                )
                if alarm_id is not None:
                    try:
                        active_ids.append(int(alarm_id))
                    except ValueError, TypeError:
                        _LOGGER.debug(
                            "Skipping alarm with non-integer id %r for device %s",
                            alarm_id,
                            mask_sn(self._sn),
                        )

        if not active_ids:
            _LOGGER.info("No active alarms to clear for device %s", mask_sn(self._sn))
            return

        try:
            await client.alter_alarm(active_ids)
            _LOGGER.info(
                "Cleared active alarms %s for device %s", active_ids, mask_sn(self._sn)
            )
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to clear active alarms for device %s: %s",
                mask_sn(self._sn),
                err,
            )
            raise


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
            _LOGGER.info("Restart command sent to microinverter %s", mask_sn(self._sn))
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to restart microinverter %s: %s", mask_sn(self._sn), err
            )
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
                _block_manual_charge_if_needed(self.coordinator, self._sn)
                watts = _get_power_value(self.hass, self._sn, "charge")
                _LOGGER.debug("Setting %s to CHARGE at %dW", mask_sn(self._sn), watts)
                await client.set_mode_charge(self._sn, watts)
            elif self._mode == "discharge":
                _block_manual_discharge_if_needed(self.coordinator, self._sn)
                watts = _get_power_value(self.hass, self._sn, "discharge")
                _LOGGER.debug(
                    "Setting %s to DISCHARGE at %dW", mask_sn(self._sn), watts
                )
                await client.set_mode_discharge(self._sn, watts)
            elif self._mode == "self_consume":
                await client.set_mode_self_consume(self._sn)
            _note_manual_mode(self.coordinator, self._sn, self._mode)
            _LOGGER.info("Mode '%s' command sent to %s", self._mode, mask_sn(self._sn))
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to set mode '%s' for %s: %s", self._mode, mask_sn(self._sn), err
            )
            raise

    @property
    def available(self) -> bool:
        """Unavailable when a VPP program is actively controlling this device."""
        if _is_vpp_active(self.coordinator, self._sn):
            return False
        return super().available


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
            _block_manual_peak_shaving_if_needed(
                self.coordinator, self._sn, self._option
            )
            await client.set_peak_shaving(self._sn, self._option)
            _note_manual_mode(self.coordinator, self._sn, self._option)
            _LOGGER.info(
                "Peak shaving '%s' command sent to %s", self._option, mask_sn(self._sn)
            )
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to send peak shaving '%s' to %s: %s",
                self._option,
                mask_sn(self._sn),
                err,
            )
            raise

    @property
    def available(self) -> bool:
        """Unavailable when a VPP program is actively controlling this device."""
        if _is_vpp_active(self.coordinator, self._sn):
            return False
        return super().available


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
        # unique_id contains unmasked sn, so we should mask it here for logs or just avoid logging unmasked unique_id
        masked_unique_id = f"hyxi_{mask_sn(sn)}_{direction}_power"
        _LOGGER.warning(
            "Power number entity (unique_id=%s) not found in registry, using 100W default",
            masked_unique_id,
        )
        return 100
    state = hass.states.get(entity_id)
    if state is not None and state.state not in ("unknown", "unavailable"):
        try:
            return int(float(state.state))
        except ValueError, TypeError:
            _LOGGER.debug(
                "Power entity %s has non-numeric state %r, using 100W default",
                entity_id,
                state.state,
            )
    _LOGGER.warning(
        "Power number entity %s not available, using 100W default", entity_id
    )
    return 100


def _note_manual_mode(coordinator, sn: str, mode: str) -> None:
    """Track the last user-sent inverter mode for battery protection telemetry."""
    if controller := _get_protection_controller(coordinator, sn):
        controller.note_manual_mode(mode)


def _block_manual_discharge_if_needed(coordinator, sn: str) -> None:
    """Reject manual discharge when SOC protection says discharge is unsafe."""
    if (
        controller := _get_protection_controller(coordinator, sn)
    ) is not None and controller.should_block_manual_discharge():
        raise HomeAssistantError(
            "Discharge blocked because battery SOC is at or below SOC Minimum"
        )


def _block_manual_charge_if_needed(coordinator, sn: str) -> None:
    """Reject manual charge when SOC protection says charging is unsafe."""
    if (
        controller := _get_protection_controller(coordinator, sn)
    ) is not None and controller.should_block_manual_charge():
        raise HomeAssistantError(
            "Charge blocked because battery SOC is at or above SOC Maximum"
        )


def _block_manual_peak_shaving_if_needed(coordinator, sn: str, option: str) -> None:
    """Reject unsafe peak-shaving actions when SOC protection is active."""
    controller = _get_protection_controller(coordinator, sn)
    if controller is None:
        return

    if option == "discharge" and controller.should_block_manual_discharge():
        raise HomeAssistantError(
            "Peak shaving discharge blocked because battery SOC is at or below SOC Minimum"
        )
    if option == "charge" and controller.should_block_manual_charge():
        raise HomeAssistantError(
            "Peak shaving charge blocked because battery SOC is at or above SOC Maximum"
        )


def _get_protection_controller(coordinator, sn: str):
    """Return the battery protection controller for a device."""
    return getattr(coordinator, "protection_controllers", {}).get(sn)


class HyxiRenewSubscriptionButton(ButtonEntity):
    """Button to manually renew/force HYXI Real-Time Push subscription."""

    _attr_has_entity_name = True
    _attr_translation_key = "renew_realtime_subscription"
    _attr_icon = "mdi:webhook"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the renew subscription button."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_renew_realtime_subscription"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HYXI Cloud Service",
            "manufacturer": MANUFACTURER,
            "model": "Cloud API Bridge",
        }

    async def async_press(self) -> None:
        """Force renew the push subscription callback."""
        from . import _async_setup_push_subscription, _async_teardown_push_subscription

        _LOGGER.info("Manually triggered HYXI push subscription renewal")
        try:
            # Tear down old first
            await _async_teardown_push_subscription(
                self.hass, self.coordinator, self._entry
            )
            # Re-setup
            await _async_setup_push_subscription(
                self.hass, self._entry, self.coordinator
            )

            # Notify coordinator entities of change
            self.coordinator.async_update_listeners()
        except Exception as err:
            _LOGGER.error("Failed to renew HYXI push subscription: %s", err)
            raise HomeAssistantError(f"Subscription renewal failed: {err}") from err

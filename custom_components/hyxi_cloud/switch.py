"""Switch platform for HYXI Cloud device control."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from hyxi_cloud_api import VPP_ACTIVE_MODES, HyxiApiClient

from .const import (
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    DOMAIN,
    detect_phase_type,
    get_raw_device_code,
    is_battery_control_enabled,
    mask_sn,
    normalize_device_type,
)
from .entity import HyxiEntity

_LOGGER = logging.getLogger(__name__)


def _is_vpp_active(coordinator, sn: str) -> bool:
    """Return True if a VPP program is currently controlling this device."""
    metrics = (coordinator.data.get(sn) or {}).get("metrics", {})
    return str(metrics.get("vppMode")) in VPP_ACTIVE_MODES


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
                is_battery_control_enabled(entry, coordinator)
                and phase == "single_phase"
            ):
                entities.append(HyxiFrequencyControlSwitch(coordinator, sn, dev_data))
        elif device_type == "micro_inverter":
            if is_battery_control_enabled(entry, coordinator):
                entities.append(HyxiMicroPowerSwitch(coordinator, sn, dev_data))

    # EM-only switches — only when EM is enabled for this inverter
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)
    if entry.options.get(CONF_EM_ENABLED) and em_sn and em_sn in coordinator.data:
        # Grid charge toggle on inverter device
        entities.append(
            EMToggleSwitch(
                coordinator, em_sn, EMToggleDef("grid_charge_allowed"), em_device=False
            )
        )
        # EM engine toggles on EM virtual device
        entities.append(
            EMToggleSwitch(coordinator, em_sn, EMToggleDef("enabled"), em_device=True)
        )
        entities.append(
            EMToggleSwitch(
                coordinator, em_sn, EMToggleDef("night_mode"), em_device=True
            )
        )
        entities.append(
            EMToggleSwitch(
                coordinator,
                em_sn,
                EMToggleDef("high_load_battery_assist"),
                em_device=True,
            )
        )

        # Export limiting — single-phase only (uses peak shaving controlId 1021)
        em_dev_data = coordinator.data.get(em_sn, {})
        em_phase = detect_phase_type(em_dev_data)
        if em_phase == "single_phase":
            entities.append(
                EMToggleSwitch(
                    coordinator,
                    em_sn,
                    EMToggleDef("export_limiting"),
                    em_device=True,
                )
            )

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

    @property
    def available(self) -> bool:
        """Unavailable when a VPP program is actively controlling this device."""
        if _is_vpp_active(self.coordinator, self._sn):
            return False
        return super().available


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


@dataclass
class EMToggleDef:
    """Definition for an EM toggle switch."""

    key: str
    default_on: bool = False


class EMToggleSwitch(SwitchEntity, RestoreEntity):
    """Toggle switch for Energy Manager parameters.

    Stores state locally (RestoreEntity). The engine reads it each tick.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_is_on: bool | None = None

    _ICONS: ClassVar[dict[str, str]] = {
        "grid_charge_allowed": "mdi:transmission-tower-import",
        "enabled": "mdi:robot",
        "night_mode": "mdi:weather-night",
        "high_load_battery_assist": "mdi:flash-alert-outline",
        "export_limiting": "mdi:transmission-tower-off",
    }

    def __init__(
        self,
        coordinator,
        sn: str,
        toggle_def: EMToggleDef,
        em_device: bool = False,
    ) -> None:
        """Initialize the EM toggle switch."""
        self._sn = sn
        key = toggle_def.key
        self._attr_unique_id = f"hyxi_{sn}_em_{key}"
        self._attr_translation_key = f"em_{key}"
        self._attr_icon = self._ICONS.get(key, "mdi:toggle-switch")
        self._attr_is_on = toggle_def.default_on

        if em_device:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, f"{sn}_energy_manager")},
            }
        else:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, sn)},
            }

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off."""
        self._attr_is_on = False
        self.async_write_ha_state()

"""Switch platform for HYXI Cloud device control."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from hyxi_cloud_api import HyxiApiClient

from .const import (
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    DOMAIN,
    MICRO_ESS_CONTROL_SUPPORTED,
    detect_phase_type,
    get_raw_device_code,
    is_battery_control_enabled,
    is_modbus_entry,
    mask_sn,
    normalize_device_type,
)
from .entity import HyxiEntity, SettingsSyncMixin

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
        entities.extend(_build_device_switches(entry, coordinator, sn, dev_data))

    entities.extend(_build_em_switches(entry, coordinator))

    if entities:
        async_add_entities(entities)


def _build_device_switches(
    entry: ConfigEntry,
    coordinator,
    sn: str,
    dev_data: dict,
) -> list[SwitchEntity]:
    """Build the switch entities for one device SN."""
    device_type = normalize_device_type(get_raw_device_code(dev_data))

    # Modbus: the settings-block booleans -- anti-starvation protection and
    # the VPP/scheduling dispatch enable. No cloud equivalent and no phase
    # dependency, so handled before the cloud-only branches below.
    if is_modbus_entry(entry) and device_type in (
        "hybrid_inverter",
        "all_in_one",
        "micro_ess",
    ):
        return _modbus_control_switches(entry, coordinator, sn, dev_data, device_type)

    return _cloud_phase_switches(entry, coordinator, sn, dev_data, device_type)


def _modbus_control_switches(
    entry: ConfigEntry,
    coordinator,
    sn: str,
    dev_data: dict,
    device_type: str,
) -> list[SwitchEntity]:
    """Modbus-only settings-block switches, if battery control is enabled."""
    if not is_battery_control_enabled(entry):
        return []
    # HALO and Hybrid call different anti-starvation methods because the
    # register's polarity is inverted between the two documents -- see
    # client_hybrid.py's set_anti_starvation_protection docstring.
    anti_starvation_method = (
        "set_anti_starvation"
        if device_type == "micro_ess"
        else "set_anti_starvation_protection"
    )
    return [
        HyxiAntiStarvationSwitch(coordinator, sn, dev_data, anti_starvation_method),
        HyxiDispatchSwitch(coordinator, sn, dev_data),
    ]


def _cloud_phase_switches(
    entry: ConfigEntry,
    coordinator,
    sn: str,
    dev_data: dict,
    device_type: str,
) -> list[SwitchEntity]:
    """Cloud-path switches, gated by device type and electrical phase."""
    entities: list[SwitchEntity] = []

    if device_type in ("hybrid_inverter", "all_in_one"):
        phase = detect_phase_type(dev_data)

        # Frequency control (controlId 1020) — single-phase devices only
        if is_battery_control_enabled(entry) and phase == "single_phase":
            entities.append(HyxiFrequencyControlSwitch(coordinator, sn, dev_data))
    elif device_type == "micro_inverter":
        if is_battery_control_enabled(entry):
            entities.append(HyxiMicroPowerSwitch(coordinator, sn, dev_data))
    elif (
        device_type == "micro_ess"
        and MICRO_ESS_CONTROL_SUPPORTED
        and is_battery_control_enabled(entry)
    ):
        entities.append(HyxiMicroEssPowerSwitch(coordinator, sn, dev_data))

    return entities


def _build_em_switches(entry: ConfigEntry, coordinator) -> list[SwitchEntity]:
    """Build the EM-only toggle switches, if EM is enabled for this inverter."""
    entities: list[SwitchEntity] = []
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)
    if not (entry.options.get(CONF_EM_ENABLED) and em_sn and em_sn in coordinator.data):
        return entities

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
        EMToggleSwitch(coordinator, em_sn, EMToggleDef("night_mode"), em_device=True)
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

    return entities


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
        _LOGGER.debug("Switch: enabling frequency control for %s", mask_sn(self._sn))
        try:
            await client.set_frequency_control(self._sn, enabled=True)
            self._attr_is_on = True
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.exception(
                "Failed to enable frequency control for %s: %s", mask_sn(self._sn), err
            )
            raise

    async def async_turn_off(self, **kwargs) -> None:
        """Disable frequency control."""
        client = self.coordinator.client
        _LOGGER.debug("Switch: disabling frequency control for %s", mask_sn(self._sn))
        try:
            await client.set_frequency_control(self._sn, enabled=False)
            self._attr_is_on = False
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.exception(
                "Failed to disable frequency control for %s: %s", mask_sn(self._sn), err
            )
            raise

    @property
    def available(self) -> bool:
        """Unavailable when battery control is not enabled."""
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
        _LOGGER.debug("Switch: turning on microinverter %s", mask_sn(self._sn))
        try:
            await client.set_micro_power(self._sn, power_on=True)
            self._attr_is_on = True
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.exception(
                "Failed to turn on microinverter %s: %s", mask_sn(self._sn), err
            )
            raise HomeAssistantError(f"Failed to turn on microinverter: {err}") from err

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the microinverter."""
        client = self.coordinator.client
        _LOGGER.debug("Switch: turning off microinverter %s", mask_sn(self._sn))
        try:
            await client.set_micro_power(self._sn, power_on=False)
            self._attr_is_on = False
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.exception(
                "Failed to turn off microinverter %s: %s", mask_sn(self._sn), err
            )
            raise HomeAssistantError(
                f"Failed to turn off microinverter: {err}"
            ) from err


class HyxiAntiStarvationSwitch(SettingsSyncMixin, HyxiEntity, SwitchEntity):
    """Switch entity for battery anti-starvation protection (Modbus only).

    is_on starts unknown (None) and is seeded, then kept in sync, from the
    client's settings block -- read at startup and every
    SETTINGS_REFRESH_SECONDS after (see client.py's async_read_settings).
    Adopting a value on each coordinator update goes through
    SettingsSyncMixin, which this only supplies _apply_settings_metrics to
    -- see its docstring for the two races a plain per-update sync would
    hit.

    Takes the client method name to call rather than hard-coding one: HALO
    and Hybrid both present "enabled" with the same meaning here, but call
    different client methods, since the register's actual polarity is
    inverted between the two documents (see client_hybrid.py's
    set_anti_starvation_protection). Hiding that in the client, not here,
    keeps this entity from needing to know which family it's on -- the same
    reason anti_starvation_enabled in metrics is already resolved to plain
    "enabled" semantics rather than the raw register value.
    """

    _attr_translation_key = "anti_starvation"
    _attr_icon = "mdi:battery-heart-variant"
    _attr_is_on: bool | None = None

    def __init__(
        self, coordinator, sn: str, dev_data: dict, client_method: str
    ) -> None:
        """Initialize the anti-starvation switch."""
        super().__init__(coordinator, sn, dev_data)
        self._attr_unique_id = f"hyxi_{sn}_anti_starvation"
        self._client_method = client_method
        self._sync_from_settings(dev_data)

    def _apply_settings_metrics(self, metrics: dict) -> None:
        """See SettingsSyncMixin -- adopt this switch's own value."""
        if "anti_starvation_enabled" in metrics:
            self._attr_is_on = metrics["anti_starvation_enabled"]

    async def _async_set(self, enabled: bool) -> None:
        client = self.coordinator.client
        method = getattr(client, self._client_method)
        _LOGGER.debug(
            "Switch: setting anti-starvation protection to %s for %s",
            enabled,
            mask_sn(self._sn),
        )
        try:
            await method(enabled)
            self._note_write()
            self._attr_is_on = enabled
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.exception(
                "Failed to set anti-starvation protection to %s for %s: %s",
                enabled,
                mask_sn(self._sn),
                err,
            )
            raise

    async def async_turn_on(self, **kwargs) -> None:
        """Enable anti-starvation protection."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable anti-starvation protection."""
        await self._async_set(False)

    @property
    def available(self) -> bool:
        """Unavailable when battery control is not enabled."""
        return super().available


class HyxiDispatchSwitch(SettingsSyncMixin, HyxiEntity, SwitchEntity):
    """VPP / scheduling dispatch enable (Modbus only).

    On means the integration is driving the battery through the VPP block
    (HALO, register 4146) or the scheduling register (Hybrid, 3000); off
    hands control back to the inverter's own configured work mode. Every
    idle / charge / discharge write turns it on -- this switch is the
    deliberate way back off.

    HALO self-consume (VPP mode 3) is a real dispatch sub-mode and leaves
    dispatch on. The Hybrid scheduling block has no self-use setpoint, so
    its self-consume can only reach the inverter's native self-use by
    disabling scheduling -- there it lands in the same "off" state as this
    switch, for a different reason.

    is_on is seeded and kept in sync from the settings block via
    SettingsSyncMixin, the same as HyxiAntiStarvationSwitch.
    """

    _attr_translation_key = "dispatch"
    _attr_icon = "mdi:remote"
    _attr_is_on: bool | None = None

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the dispatch switch."""
        super().__init__(coordinator, sn, dev_data)
        self._attr_unique_id = f"hyxi_{sn}_dispatch"
        self._sync_from_settings(dev_data)

    def _apply_settings_metrics(self, metrics: dict) -> None:
        """See SettingsSyncMixin -- adopt this switch's own value."""
        if "dispatch_enabled" in metrics:
            self._attr_is_on = metrics["dispatch_enabled"]

    async def _async_set(self, enabled: bool) -> None:
        _LOGGER.debug(
            "Switch: setting dispatch to %s for %s", enabled, mask_sn(self._sn)
        )
        try:
            await self.coordinator.client.set_dispatch_enabled(enabled)
            self._note_write()
            self._attr_is_on = enabled
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.exception(
                "Failed to set dispatch to %s for %s: %s",
                enabled,
                mask_sn(self._sn),
                err,
            )
            raise

    async def async_turn_on(self, **kwargs) -> None:
        """Re-arm dispatch."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Hand control back to the inverter."""
        await self._async_set(False)


class HyxiMicroEssPowerSwitch(HyxiEntity, SwitchEntity):
    """Switch entity for Micro ESS power on/off (controlId 1011).

    State is tracked internally after successful writes as the API does not
    return the current power state in polling responses.
    """

    _attr_translation_key = "micro_ess_power"
    _attr_icon = "mdi:power"
    _attr_is_on: bool | None = None

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the Micro ESS power switch."""
        super().__init__(coordinator, sn, dev_data)
        self._attr_unique_id = f"hyxi_{sn}_micro_ess_power"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the Micro ESS."""
        client = self.coordinator.client
        _LOGGER.debug("Switch: turning on Micro ESS %s", mask_sn(self._sn))
        try:
            await client.set_micro_ess_power(self._sn, power_on=True)
            self._attr_is_on = True
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.exception(
                "Failed to turn on Micro ESS %s: %s", mask_sn(self._sn), err
            )
            raise HomeAssistantError(f"Failed to turn on Micro ESS: {err}") from err

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the Micro ESS."""
        client = self.coordinator.client
        _LOGGER.debug("Switch: turning off Micro ESS %s", mask_sn(self._sn))
        try:
            await client.set_micro_ess_power(self._sn, power_on=False)
            self._attr_is_on = False
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.exception(
                "Failed to turn off Micro ESS %s: %s", mask_sn(self._sn), err
            )
            raise HomeAssistantError(f"Failed to turn off Micro ESS: {err}") from err


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

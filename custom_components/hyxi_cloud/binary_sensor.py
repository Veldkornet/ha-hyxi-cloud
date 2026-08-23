"""Binary sensor platform for HYXI Cloud."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlparse

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

# NOTE: these values are inferred from the HYXI phone app's APK and have
# never been confirmed against a device. HYXI's Micro Storage RS485 document
# gives a different enumeration for the same field. Do not reconcile the two
# by editing either -- see docs/modbus-provenance.md, rule 1.
from hyxi_cloud_api import VPP_ACTIVE_MODES

from .const import (
    BASE_URL_DEFAULT,
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    DOMAIN,
    MANUFACTURER,
    get_raw_device_code,
    mask_sn,
    normalize_device_type,
)

if TYPE_CHECKING:
    from .coordinator import HyxiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

ACTIVE_ALARM_STATES = {"0", "1", "2", 0, 1, 2}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [HyxiConnectivitySensor(coordinator, entry)]

    for device_sn, dev_data in coordinator.data.items():
        entities.append(HyxiDeviceAlarmSensor(coordinator, entry, device_sn))

        dev_type = normalize_device_type(get_raw_device_code(dev_data))
        if dev_type in ("hybrid_inverter", "all_in_one", "micro_ess"):
            entities.append(HyxiWorkModeSensor(coordinator, entry, device_sn, dev_data))

    # Energy Manager binary sensors (EM-only)
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)
    if entry.options.get(CONF_EM_ENABLED) and em_sn and em_sn in coordinator.data:
        em_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{em_sn}_energy_manager")},
            name="Energy Manager",
            manufacturer=MANUFACTURER,
            model="Energy Manager",
            via_device=(DOMAIN, em_sn),
        )
        entities.append(
            EMBinarySensor(coordinator, em_sn, "night_mode_active", em_device_info)
        )
        entities.append(
            EMBinarySensor(coordinator, em_sn, "high_load_detected", em_device_info)
        )

    async_add_entities(entities)


class HyxiConnectivitySensor(
    CoordinatorEntity["HyxiDataUpdateCoordinator"], BinarySensorEntity
):
    """Representation of a HYXI Cloud connectivity sensor."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_translation_key = "connectivity"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HyxiDataUpdateCoordinator, entry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connectivity"
        self._cloud_endpoint = (
            urlparse(entry.data.get("base_url") or BASE_URL_DEFAULT).netloc
            or urlparse(BASE_URL_DEFAULT).netloc
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HYXI Cloud Service",
            "manufacturer": MANUFACTURER,
            "configuration_url": "https://www.hyxicloud.com",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the cloud is reachable and data is flowing."""
        # 🚀 Native HA tracking for Coordinator success/failure!
        return self.coordinator.last_update_success

    def _calculate_freshness(self, last_success) -> tuple[str, str | None]:
        """Calculate data freshness label and return ISO string."""
        if not last_success:
            return "Unknown", None

        # Handle both datetime objects and legacy ISO strings
        if isinstance(last_success, str):
            last_success = dt_util.parse_datetime(last_success)

        if not last_success:
            return "Unknown", None

        last_success_str = last_success.isoformat()
        diff = dt_util.utcnow() - last_success
        minutes = int(diff.total_seconds() / 60)

        if minutes < 1:
            return "Current (Just now)", last_success_str
        if minutes < 6:
            return f"Fresh ({minutes}m ago)", last_success_str
        return f"Stale ({minutes}m ago)", last_success_str

    def _calculate_connection_quality(self, attempts: int) -> str:
        """Calculate the connection quality status."""
        if not self.is_on:
            return "Offline"
        if attempts > 1:
            return f"Degraded ({attempts} retries)"
        return "Stable"

    @property
    def extra_state_attributes(self):
        """Return diagnostic attributes including freshness metrics."""
        metadata = getattr(self.coordinator, "hyxi_metadata", {})
        attempts = metadata.get("last_attempts", 0)
        last_success = metadata.get("last_success")

        freshness, last_success_str = self._calculate_freshness(last_success)
        quality = self._calculate_connection_quality(attempts)

        return {
            "last_attempts": attempts,
            "connection_quality": quality,
            "last_successful_connection": last_success_str,
            "data_freshness": freshness,
            "cloud_endpoint": self._cloud_endpoint,
            "last_error": metadata.get("last_error") or "None",
            "cache_active": bool(metadata.get("cache_active", False)),
            "api_status": metadata.get("api_status") or "Starting",
        }

    @property
    def available(self) -> bool:
        """Always stay available so the user can see the 'Disconnected' state."""
        return True


class HyxiDeviceAlarmSensor(
    CoordinatorEntity["HyxiDataUpdateCoordinator"], BinarySensorEntity
):
    """Representation of a HYXI Cloud device active alarm sensor."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_has_entity_name = True
    _attr_translation_key = "device_alarm"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, sn):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self.sn = sn
        self._attr_unique_id = f"{entry.entry_id}_{sn}_device_alarm"
        self._alarms = []
        self._active_alarms_count = 0

        device_data = coordinator.data.get(sn) or {}
        metrics = device_data.get("metrics", {})
        parent_sn = metrics.get("parentSn")

        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": device_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": device_data.get("model"),
            "sw_version": device_data.get("sw_version"),
            "hw_version": device_data.get("hw_version"),
        }

        if parent_sn:
            self._attr_device_info["via_device"] = (DOMAIN, parent_sn)
        self._update_internal_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_internal_state()
        super()._handle_coordinator_update()

    def _update_internal_state(self) -> None:
        """Process alarm states once per update."""
        self._alarms = (self.coordinator.data.get(self.sn) or {}).get("alarms") or []

        active_states = ACTIVE_ALARM_STATES
        old_count = self._active_alarms_count
        self._active_alarms_count = sum(
            1
            for a in self._alarms
            if (
                a.get("alarmState") in active_states
                or a.get("alarmstate") in active_states
            )
            and not (a.get("endTime") or a.get("endtime"))
        )
        if self._active_alarms_count != old_count:
            _LOGGER.debug(
                "Device alarm %s: active_alarms %d -> %d",
                mask_sn(self.sn),
                old_count,
                self._active_alarms_count,
            )

    @property
    def is_on(self) -> bool:
        """Return True if any active alarms exist."""
        return self._active_alarms_count > 0

    @property
    def extra_state_attributes(self):
        """Return raw alarm list."""
        return {
            "active_alarms_count": self._active_alarms_count,
            "raw_alarms_payload": self._alarms,
        }


class HyxiWorkModeSensor(
    CoordinatorEntity["HyxiDataUpdateCoordinator"], BinarySensorEntity
):
    """Binary sensor indicating whether the device's reported work mode is
    currently one of the values that means an active VPP dispatch.

    ON  = workMode is a confirmed active-dispatch value (VPP charge/discharge).
    OFF = any other work mode, including HYXI's own "VPP enrolled / standby"
    value and every ordinary (non-VPP) mode.

    Named after the field it actually reads (workMode) rather than "VPP
    Dispatch" -- the cloud API has no dedicated VPP field, and there is no
    VPP programme name/supplier/manufacturer detail available to show,
    only this one number (see hyxi_cloud_api.VPP_ACTIVE_MODES, which
    documents which workMode values mean an active dispatch). Purely
    informational: nothing in this integration currently gates control
    entities on this state, so it cannot suppress a conflicting manual
    command by itself -- only report that one may be in progress. Use it
    in automations to detect VPP activity and suppress any conflicting
    home automation rules.
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_has_entity_name = True
    _attr_translation_key = "work_mode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HyxiDataUpdateCoordinator,
        entry: ConfigEntry,
        sn: str,
        dev_data: dict[str, Any],
    ) -> None:
        """Initialize the work mode sensor."""
        super().__init__(coordinator)
        self.sn = sn
        self._attr_unique_id = f"{entry.entry_id}_{sn}_work_mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
        }
        self._last_work_mode = self._normalized_work_mode

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator, logging mode transitions."""
        new_mode = self._normalized_work_mode
        if new_mode != self._last_work_mode:
            _LOGGER.debug(
                "Work mode %s: %s -> %s",
                mask_sn(self.sn),
                self._last_work_mode,
                new_mode,
            )
            self._last_work_mode = new_mode
        super()._handle_coordinator_update()

    @property
    def _metrics(self) -> dict:
        return (self.coordinator.data.get(self.sn) or {}).get("metrics", {})

    @property
    def _normalized_work_mode(self) -> str | None:
        """workMode as a string, or None if the metric is missing.

        Used only for transition detection/logging so that a coordinator
        update reporting the same mode as a different Python type (e.g. int
        13 vs str "13") doesn't get logged as a spurious transition.
        """
        mode = self._metrics.get("workMode")
        return None if mode is None else str(mode)

    @property
    def is_on(self) -> bool:
        """Return True when an active VPP mode is in progress."""
        return str(self._metrics.get("workMode")) in VPP_ACTIVE_MODES

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw workMode value this sensor's state is derived from.

        No programme name/supplier/manufacturer detail here -- the cloud API
        has no field for any of that; showing invented placeholders for data
        that was never actually available would be worse than showing
        nothing.
        """
        return {"work_mode": self._metrics.get("workMode")}


class EMBinarySensor(BinarySensorEntity):
    """Binary sensor backed by the Energy Manager engine."""

    _attr_has_entity_name = True

    _ICONS: ClassVar[dict[str, str]] = {
        "night_mode_active": "mdi:weather-night",
        "high_load_detected": "mdi:flash-alert",
    }

    _STATE_FUNCS: ClassVar[dict[str, Callable[[Any], bool]]] = {
        "night_mode_active": lambda engine: engine._is_night(),
        "high_load_detected": lambda engine: (
            engine._get_home_load() > engine._get_param("high_load_threshold")
        ),
    }

    def __init__(
        self,
        coordinator,
        sn: str,
        key: str,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the EM binary sensor."""
        self._coordinator = coordinator
        self._sn = sn
        self._key = key
        self._attr_unique_id = f"hyxi_{sn}_em_{key}"
        self._attr_translation_key = f"em_{key}"
        self._attr_device_info = device_info
        self._attr_icon = self._ICONS.get(key)
        self._state_func = self._STATE_FUNCS.get(key)

    async def async_added_to_hass(self) -> None:
        """Register for engine updates."""
        engine = self._coordinator.engine
        if engine:
            engine.register_update_callback(self._engine_updated)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from engine updates."""
        engine = self._coordinator.engine
        if engine:
            engine.unregister_update_callback(self._engine_updated)

    @callback
    def _engine_updated(self) -> None:
        """Handle engine state change."""
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return the current value from the engine."""
        engine = self._coordinator.engine
        if not engine or not self._state_func:
            return None
        return self._state_func(engine)

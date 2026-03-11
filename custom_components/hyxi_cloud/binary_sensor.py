"""Binary sensor platform for HYXI Cloud."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [HyxiConnectivitySensor(coordinator, entry)]

    for device_sn in coordinator.data:
        entities.append(HyxiDeviceAlarmSensor(coordinator, entry, device_sn))

    async_add_entities(entities)


class HyxiConnectivitySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a HYXI Cloud connectivity sensor."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_translation_key = "connectivity"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connectivity"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HYXI Cloud Service",
            "manufacturer": "HYXIPower",
            "configuration_url": "https://www.hyxicloud.com",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the cloud is reachable and data is flowing."""
        # 🚀 Native HA tracking for Coordinator success/failure!
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self):
        """Return diagnostic attributes including freshness metrics."""
        # 🛠️ Read directly from the class attribute we created in __init__.py
        metadata = getattr(self.coordinator, "hyxi_metadata", {})
        attempts = metadata.get("last_attempts", 0)
        last_success_str = metadata.get("last_success")

        # 🕰️ Calculate Freshness logic
        freshness = "Unknown"
        if last_success_str:
            last_success = dt_util.parse_datetime(last_success_str)
            if last_success:
                diff = dt_util.utcnow() - last_success
                minutes = int(diff.total_seconds() / 60)

                if minutes < 1:
                    freshness = "Current (Just now)"
                elif minutes < 6:
                    freshness = f"Fresh ({minutes}m ago)"
                else:
                    freshness = f"Stale ({minutes}m ago)"

        # 📶 Logic for Connection Quality status
        if not self.is_on:
            quality = "Offline"
        elif attempts > 1:
            quality = f"Degraded ({attempts} retries)"
        else:
            quality = "Stable"

        attrs = {
            "last_attempts": attempts,
            "connection_quality": quality,
            "last_successful_connection": last_success_str,
            "data_freshness": freshness,
            "cloud_endpoint": "open.hyxicloud.com",
            "last_update": last_success_str,
        }

        attrs["last_error"] = metadata.get("last_error") or "None"

        return attrs

    @property
    def available(self) -> bool:
        """Always stay available so the user can see the 'Disconnected' state."""
        return True


class HyxiDeviceAlarmSensor(CoordinatorEntity, BinarySensorEntity):
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

        device_data = coordinator.data.get(sn, {})
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": device_data.get("device_name", f"HYXI {sn}"),
            "manufacturer": "HYXIPower",
            "model": device_data.get("model", "Unknown"),
            "sw_version": device_data.get("sw_version"),
            "hw_version": device_data.get("hw_version"),
        }
        self._update_internal_state()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_internal_state()
        super()._handle_coordinator_update()

    def _update_internal_state(self) -> None:
        """Process alarm states once per update."""
        self._alarms = self.coordinator.data.get(self.sn, {}).get("alarms", [])

        count = 0
        for a in self._alarms:
            state = a.get("alarmState")
            if state in ("0", "1", 0, 1):
                count += 1

        self._active_alarms_count = count

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

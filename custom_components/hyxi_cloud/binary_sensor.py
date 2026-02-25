"""Binary sensor platform for HYXi Cloud."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
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
    async_add_entities([HyxiConnectivitySensor(coordinator, entry)])


class HyxiConnectivitySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a HYXi Cloud connectivity sensor."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_translation_key = "connectivity"

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connectivity"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HYXi Cloud Service",
            "manufacturer": "HYXiPower",
            "configuration_url": "https://www.hyxicloud.com",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the cloud is reachable and data is flowing."""
        if not self.coordinator.data:
            return False

        # 🛠️ Path updated to look in _metadata
        metadata = self.coordinator.data.get("_metadata", {})
        return metadata.get("cloud_online", False)

    @property
    def extra_state_attributes(self):
        """Return diagnostic attributes including freshness metrics."""
        if not self.coordinator.data:
            return {}

        # 🛠️ Paths updated to look in _metadata
        metadata = self.coordinator.data.get("_metadata", {})
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

        return {
            "last_attempts": attempts,
            "connection_quality": quality,
            "last_successful_connection": last_success_str,
            "data_freshness": freshness,
            "cloud_endpoint": "open.hyxicloud.com",
        }

    @property
    def available(self) -> bool:
        """Always stay available so the user can see the 'Disconnected' state."""
        return True

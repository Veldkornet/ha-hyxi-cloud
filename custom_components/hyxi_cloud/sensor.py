from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import entity_registry as er
from .const import DOMAIN
import logging

_LOGGER = logging.getLogger(__name__)

# THE FULL LIST - Cleaned keys to match your AppDaemon style
SENSOR_TYPES = [
    # Power Sensors
    SensorEntityDescription(key="batSoc", name="Battery SOC", native_unit_of_measurement="%", device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=0),
    SensorEntityDescription(key="pbat", name="Battery Power", native_unit_of_measurement="W", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="ppv", name="Solar Power", native_unit_of_measurement="W", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="home_load", name="Home Load", native_unit_of_measurement="W", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, icon="mdi:home-lightning-bolt"),
    SensorEntityDescription(key="grid_import", name="Grid Import", native_unit_of_measurement="W", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, icon="mdi:transmission-tower-import"),
    SensorEntityDescription(key="grid_export", name="Grid Export", native_unit_of_measurement="W", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, icon="mdi:transmission-tower-export"),
    SensorEntityDescription(key="bat_charging", name="Battery Charging", native_unit_of_measurement="W", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, icon="mdi:battery-arrow-up"),
    SensorEntityDescription(key="bat_discharging", name="Battery Discharging", native_unit_of_measurement="W", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, icon="mdi:battery-arrow-down"),
    
    # Energy Sensors
    SensorEntityDescription(key="totalE", name="Lifetime Yield", native_unit_of_measurement="kWh", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING),
    SensorEntityDescription(key="bat_charge_total", name="Total Battery Charge", native_unit_of_measurement="kWh", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING),
    SensorEntityDescription(key="bat_discharge_total", name="Total Battery Discharge", native_unit_of_measurement="kWh", device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING),
    
    # Diagnostics - Added _ for the entity_id mapping
    SensorEntityDescription(key="batSoh", name="Battery SOH", native_unit_of_measurement="%", state_class=SensorStateClass.MEASUREMENT, icon="mdi:heart-pulse",suggested_display_precision=1),
    SensorEntityDescription(key="tinv", name="Inverter Temperature", native_unit_of_measurement="°C", device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="last_seen", name="Last Sync", icon="mdi:clock-check-outline"),
]

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data: return

    entities = []
    for sn in coordinator.data:
        for description in SENSOR_TYPES:
            entities.append(HyxiSensor(coordinator, sn, description))
    async_add_entities(entities)

class HyxiSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = False

    def __init__(self, coordinator, sn, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._sn = sn
        
        dev_data = coordinator.data[sn]
        self._plant_slug = dev_data.get("plant_slug", "default")
        
        # 1. UNIQUE ID
        self._attr_unique_id = f"{sn}-{description.key}"
        
        # 2. FRIENDLY NAME
        self._attr_name = f"HYXi {dev_data['plant_name']} {description.name}"

        # 3. DEVICE INFO
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data["device_name"],
            "manufacturer": "HYXi Power",
            "model": "Hybrid Inverter",
        }

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        
        # FORCE the entity_id in the registry
        registry = er.async_get(self.hass)
        old_entity_id = self.entity_id
        new_entity_id = f"sensor.hyxi_{self._plant_slug}_{self.entity_description.key.lower()}"
        
        if old_entity_id != new_entity_id:
            _LOGGER.debug("Forcing rename from %s to %s", old_entity_id, new_entity_id)
            registry.async_update_entity(self.entity_id, new_entity_id=new_entity_id)

    @property
    def native_value(self):
        metrics = self.coordinator.data.get(self._sn, {}).get("metrics", {})
        return metrics.get(self.entity_description.key)

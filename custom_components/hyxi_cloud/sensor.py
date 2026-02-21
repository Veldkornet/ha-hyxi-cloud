from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from datetime import datetime, timezone
import logging

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Full list of all sensors you've defined (batSn removed!)
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
    
    # Diagnostics
    SensorEntityDescription(key="batSoh", name="Battery SOH", native_unit_of_measurement="%", state_class=SensorStateClass.MEASUREMENT, icon="mdi:heart-pulse", suggested_display_precision=0),
    SensorEntityDescription(key="tinv", name="Inverter Temperature", native_unit_of_measurement="°C", device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="collectTime", name="Last Data Update", device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="last_seen", name="Last Cloud Sync", device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC),
]

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data: return

    entities = []
    for sn, dev_data in coordinator.data.items():
        # Check model to decide which sensors to attach
        is_collector = dev_data.get("model") == "Data Collector"
        
        for description in SENSOR_TYPES:
            # Logic: If it's a Collector, only give it 'last_seen' to keep it in the registry.
            # Otherwise, give the Inverter everything.
            if is_collector:
                if description.key == "last_seen":
                    entities.append(HyxiSensor(coordinator, sn, description))
            else:
                entities.append(HyxiSensor(coordinator, sn, description))
                
    async_add_entities(entities)

class HyxiSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True 

    def __init__(self, coordinator, sn, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._sn = sn # This is the Inverter/Collector SN
        
        dev_data = coordinator.data.get(sn, {})
        metrics = dev_data.get("metrics", {})
        bat_sn = metrics.get("batSn")
        
        # 1. Define which sensors belong to the physical battery
        BATTERY_SENSORS = [
            "batSoc", "pbat", "bat_charging", "bat_discharging", 
            "bat_charge_total", "bat_discharge_total", "batSoh"
        ]
        
        # 2. Logic to determine if this entity belongs to the Inverter or a Battery
        if description.key in BATTERY_SENSORS and bat_sn:
            self._actual_sn = bat_sn
            # Battery Device Setup
            self._attr_device_info = {
                "identifiers": {(DOMAIN, bat_sn)},
                "name": f"Battery {bat_sn}",
                "manufacturer": "HYXi Power",
                "model": "Energy Storage System",
                "serial_number": bat_sn,
                "via_device": (DOMAIN, sn) # This links the Battery to the Inverter
            }
        else:
            self._actual_sn = sn
            # Inverter / Collector Device Setup
            self._attr_device_info = {
                "identifiers": {(DOMAIN, sn)},
                "name": dev_data.get("device_name", f"Device {sn}"),
                "manufacturer": "HYXi Power",
                "model": dev_data.get("model"),
                "sw_version": dev_data.get("sw_version"),
                "hw_version": dev_data.get("hw_version"),
                "serial_number": sn,
            }
        
        # Unique ID must use the Battery SN if it's a battery sensor to allow multiple batteries
        self._attr_unique_id = f"hyxi_{self._actual_sn}_{description.key}"
        self._attr_name = description.name
        self._attr_entity_registry_enabled_default = getattr(description, "entity_registry_enabled_default", True)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        registry = er.async_get(self.hass)
        # Use _actual_sn so the entity ID also matches the specific battery
        new_entity_id = f"sensor.hyxi_{self._actual_sn}_{self.entity_description.key.lower()}"
        
        if self.entity_id != new_entity_id:
            try:
                registry.async_update_entity(self.entity_id, new_entity_id=new_entity_id)
            except Exception: pass

    @property
    def native_value(self):
        metrics = self.coordinator.data.get(self._sn, {}).get("metrics", {})
        value = metrics.get(self.entity_description.key)
        if value is None or value == "": return None

        if self.entity_description.key in ["batSoc", "batSoh"]:
            try: return int(round(float(value)))
            except: return None

        if self.entity_description.key == "collectTime":
            try: return datetime.fromtimestamp(int(value), tz=timezone.utc)
            except: return None

        if self.entity_description.key == "last_seen":
            dt = dt_util.parse_datetime(str(value))
            if dt: return dt if dt.tzinfo else dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            return None

        return value
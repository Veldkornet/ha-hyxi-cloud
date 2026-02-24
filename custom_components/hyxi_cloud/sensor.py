import logging
from datetime import UTC
from datetime import datetime

from homeassistant.components.sensor import EntityCategory
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Full list of all sensors you've defined
SENSOR_TYPES = [
    # Power Sensors
    SensorEntityDescription(
        key="batSoc",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="pbat",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="ppv",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="home_load",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SensorEntityDescription(
        key="grid_import",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-import",
    ),
    SensorEntityDescription(
        key="grid_export",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-export",
    ),
    SensorEntityDescription(
        key="bat_charging",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-up",
    ),
    SensorEntityDescription(
        key="bat_discharging",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-down",
    ),
    # Energy Sensors
    SensorEntityDescription(
        key="totalE",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="bat_charge_total",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="bat_discharge_total",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    # Diagnostics
    SensorEntityDescription(
        key="batSoh",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:heart-pulse",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="tinv",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="collectTime",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up HYXi sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return

    entities = []

    # Define which sensors apply to which types
    INVERTER_SENSORS = ["ppv", "totalE", "tinv"]
    BATTERY_SENSORS = [
        "batSoc",
        "pbat",
        "batSoh",
        "bat_charge_total",
        "bat_discharge_total",
        "bat_charging",
        "bat_discharging",
    ]
    METER_SENSORS = ["grid_import", "grid_export", "home_load"]
    HEARTBEAT_SENSOR = ["last_seen"]
    DATA_TIME_SENSOR = ["collectTime"]

    # 1. Hardware Loop: Add sensors for every physical device found
    for sn, dev_data in coordinator.data.items():
        device_type = dev_data.get("device_type_code", "")

        for description in SENSOR_TYPES:
            key = description.key
            should_add = False

            if key in HEARTBEAT_SENSOR:
                should_add = True
            elif key in DATA_TIME_SENSOR and device_type not in [
                "COLLECTOR",
                "DMU",
                "OPTIMIZER",
            ]:
                should_add = True
            elif "HYBRID" in device_type or "ALL_IN_ONE" in device_type:
                if (
                    key in INVERTER_SENSORS
                    or key in BATTERY_SENSORS
                    or key in METER_SENSORS
                ):
                    should_add = True
            elif "INVERTER" in device_type:
                if key in INVERTER_SENSORS:
                    should_add = True
            elif "BATTERY" in device_type or "EMS" in device_type:
                if key in BATTERY_SENSORS:
                    should_add = True
            elif "METER" in device_type:
                if key in METER_SENSORS:
                    should_add = True

            if should_add:
                entities.append(HyxiSensor(coordinator, sn, description))

    # 2. Integration Loop: Add the Service/Bridge sensor exactly ONCE
    entities.append(HyxiLastUpdateSensor(coordinator, entry))

    async_add_entities(entities)


class HyxiSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Physical HYXi Sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, sn, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._sn = sn

        dev_data = coordinator.data.get(sn, {})
        metrics = dev_data.get("metrics", {})
        bat_sn = metrics.get("batSn")

        BATTERY_SENSORS = [
            "batSoc",
            "pbat",
            "bat_charging",
            "bat_discharging",
            "bat_charge_total",
            "bat_discharge_total",
            "batSoh",
        ]

        if description.key in BATTERY_SENSORS and bat_sn:
            self._actual_sn = bat_sn
            self._attr_device_info = {
                "identifiers": {(DOMAIN, bat_sn)},
                "name": f"Battery {bat_sn}",
                "manufacturer": "HYXi Power",
                "model": "Energy Storage System",
                "serial_number": bat_sn,
                "via_device": (DOMAIN, sn),
            }
        else:
            self._actual_sn = sn
            self._attr_device_info = {
                "identifiers": {(DOMAIN, sn)},
                "name": dev_data.get("device_name", f"Device {sn}"),
                "manufacturer": "HYXi Power",
                "model": dev_data.get("model"),
                "sw_version": dev_data.get("sw_version"),
                "hw_version": dev_data.get("hw_version"),
                "serial_number": sn,
            }

        self._attr_unique_id = f"hyxi_{self._actual_sn}_{description.key}"
        self._attr_translation_key = description.key.lower()

    @property
    def native_value(self):
        metrics = self.coordinator.data.get(self._sn, {}).get("metrics", {})
        value = metrics.get(self.entity_description.key)
        if value is None or value == "":
            return None

        if self.entity_description.key in ["batSoc", "batSoh"]:
            try:
                return int(round(float(value)))
            except (ValueError, TypeError):
                return None

        if self.entity_description.key == "collectTime":
            try:
                return datetime.fromtimestamp(int(value), tz=UTC)
            except (ValueError, TypeError, OSError):
                return None

        if self.entity_description.key == "last_seen":
            return dt_util.parse_datetime(str(value))

        return value


class HyxiLastUpdateSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor for the Integration/Cloud Bridge status."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "integration_last_updated"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_integration_last_updated"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HYXi Cloud Service",
            "manufacturer": "HYXi Power",
            "model": "Cloud API Bridge",
        }

    @property
    def available(self) -> bool:
        """Return if the sensor is available."""
        # This uses the standard boolean provided by the coordinator
        return self.coordinator.last_update_success

    @property
    def native_value(self):
        """Return the last successful update time."""
        if self.coordinator.last_update_success:
            # We use the Home Assistant utility to get current UTC time
            # Since the coordinator just succeeded, 'now' is our last update time
            return dt_util.utcnow()
        return None

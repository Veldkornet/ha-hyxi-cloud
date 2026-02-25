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

SENSOR_TYPES = [
    # Power Sensors
    SensorEntityDescription(
        key="batSoc",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:battery",
    ),
    SensorEntityDescription(
        key="pbat",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
    ),
    SensorEntityDescription(
        key="ppv",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
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
        icon="mdi:counter",
    ),
    SensorEntityDescription(
        key="bat_charge_total",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-plus-variant",
    ),
    SensorEntityDescription(
        key="bat_discharge_total",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-minus-variant",
    ),
    # Diagnostics
    SensorEntityDescription(
        key="batSoh",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:heart-pulse",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="tinv",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Data Integrity
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
VSYS_SENSORS = [
    SensorEntityDescription(
        key="avg_soc",
        name="Battery State of Charge Average",
        icon="mdi:battery",
        native_unit_of_measurement="%",
        suggested_display_precision=0,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="avg_soh",
        name="Battery State of Health Average",
        icon="mdi:heart-pulse",
        native_unit_of_measurement="%",
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="total_pbat",
        name="Battery Power",
        icon="mdi:flash",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bat_charging",
        name="Battery Charging",
        icon="mdi:battery-arrow-up",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bat_discharging",
        name="Battery Discharging",
        icon="mdi:battery-arrow-down",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bat_charge_total",
        name="Total Battery Charge",
        icon="mdi:battery-plus-variant",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="bat_discharge_total",
        name="Total Battery Discharge",
        icon="mdi:battery-minus-variant",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up HYXi sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        _LOGGER.warning("HYXi Setup: No data available in coordinator during setup")
        return

    # To fix the 'via_device' warning, we register parents before children
    parent_entities = []
    child_entities = []

    # Filter constants
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

    # 1. Hardware Loop
    for sn, dev_data in coordinator.data.items():
        # FIX: Skip the 'cloud_online' metadata flag to avoid AttributeError
        if sn == "cloud_online":
            continue

        device_type = str(dev_data.get("device_type_code", "")).upper()
        metrics = dev_data.get("metrics", {})

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
                # To fix 'via_device', check if this is a linked battery child
                if key in BATTERY_SENSORS and metrics.get("batSn"):
                    child_entities.append(HyxiSensor(coordinator, sn, description))
                else:
                    parent_entities.append(HyxiSensor(coordinator, sn, description))

    # 2. Integration Health (Parent Level)
    parent_entities.append(HyxiLastUpdateSensor(coordinator, entry))

    # 3. Aggregate System View
    battery_sns = []
    for sn, dev in coordinator.data.items():
        # FIX: Skip 'cloud_online' here as well
        if sn == "cloud_online":
            continue

        dtype = str(dev.get("device_type_code", "")).upper()
        if any(x in dtype for x in ["BATTERY", "EMS", "HYBRID", "ALL_IN_ONE"]):
            battery_sns.append(sn)

    # RULE 1: Must have more than 1 battery
    if len(battery_sns) > 1:
        # RULE 2: Must be enabled in the configuration menu (Default False as requested)
        enable_virtual = entry.options.get("enable_virtual_battery", False)

        if enable_virtual:
            _LOGGER.debug("HYXi Aggregator: Creating 'Battery System' entities...")
            for description in VSYS_SENSORS:
                parent_entities.append(
                    HyxiBatterySystemSensor(coordinator, entry, description)
                )

    # FINAL REGISTRATION: Register Inverters (parents) first, then linked Batteries (children)
    if parent_entities:
        async_add_entities(parent_entities)

    if child_entities:
        async_add_entities(child_entities)


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

        # Battery Keys for logic
        BAT_KEYS = [
            "batSoc",
            "pbat",
            "bat_charging",
            "bat_discharging",
            "bat_charge_total",
            "bat_discharge_total",
            "batSoh",
        ]

        if description.key in BAT_KEYS and bat_sn:
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
    """Diagnostic sensor for the Integration health."""

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
        return self.coordinator.last_update_success

    @property
    def native_value(self):
        if self.coordinator.last_update_success:
            return dt_util.utcnow()
        return None


class HyxiBatterySystemSensor(CoordinatorEntity, SensorEntity):
    """Virtual sensor representing the combined battery storage."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._key = description.key

        self._attr_unique_id = f"hyxi_vsys_{entry.entry_id}_{description.key}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"hyxi_system_{entry.entry_id}")},
            "name": "HYXi Battery System",
            "manufacturer": "HYXi Power",
            "model": "Aggregated Storage",
        }

    @property
    def native_value(self):
        summary = self.coordinator.get_battery_summary()
        if not summary:
            return None
        value = summary.get(self._key)

        if value is not None and isinstance(value, (int, float)):
            return round(float(value), 1)
        return value

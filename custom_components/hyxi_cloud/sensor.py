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
        icon="mdi:clock-check-outline",
    ),
    SensorEntityDescription(
        key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:cloud-check-outline",
    ),
    # New Device Info Sensors
    SensorEntityDescription(
        key="batCap",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-high",
    ),
    SensorEntityDescription(
        key="maxChargePower",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="maxDischargePower",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="signalIntensity",
        native_unit_of_measurement="dBm",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="signalVal",
        native_unit_of_measurement="%",
        icon="mdi:wifi",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="wifiVer",
        icon="mdi:memory",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="comMode",
        icon="mdi:lan",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="acP",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    SensorEntityDescription(
        key="vac",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
    ),
    SensorEntityDescription(
        key="vpv",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
    ),
    SensorEntityDescription(
        key="eToday",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power-variant",
    ),
]
VSYS_SENSORS = [
    SensorEntityDescription(
        key="avg_soc",
        native_unit_of_measurement="%",
        suggested_display_precision=0,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="avg_soh",
        icon="mdi:heart-pulse",
        native_unit_of_measurement="%",
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="total_pbat",
        icon="mdi:flash",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bat_charging",
        icon="mdi:battery-arrow-up",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bat_discharging",
        icon="mdi:battery-arrow-down",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="bat_charge_total",
        icon="mdi:battery-plus-variant",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="bat_discharge_total",
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

    # Registration order to fix 'via_device'
    parent_entities = []
    child_entities = []

    # Filter constants
    BATTERY_SENSORS = [
        "batSoc",
        "pbat",
        "batSoh",
        "bat_charge_total",
        "bat_discharge_total",
        "bat_charging",
        "bat_discharging",
    ]
    COLLECTOR_SENSORS = ["signalIntensity", "signalVal", "wifiVer", "comMode"]
    HEARTBEAT_SENSOR = ["last_seen"]

    # 1. Hardware Loop
    for sn, dev_data in coordinator.data.items():
        device_type = str(dev_data.get("device_type_code", "")).upper()
        metrics = dev_data.get("metrics", {})

        _LOGGER.debug(
            "HYXi Processing Device %s (Type: %s). Metrics keys: %s",
            sn,
            device_type,
            list(metrics.keys()),
        )

        for description in SENSOR_TYPES:
            key = description.key
            should_add = False

            if key in HEARTBEAT_SENSOR:
                should_add = True
            elif key in COLLECTOR_SENSORS:
                if "COLLECTOR" in device_type or "DMU" in device_type:
                    should_add = True
            elif key in metrics and metrics[key] is not None:
                if str(metrics[key]) != "":
                    should_add = True

            if should_add:
                if key in BATTERY_SENSORS and metrics.get("batSn"):
                    child_entities.append(HyxiSensor(coordinator, sn, description))
                else:
                    parent_entities.append(HyxiSensor(coordinator, sn, description))

    # 2. Integration Health
    parent_entities.append(HyxiLastUpdateSensor(coordinator, entry))

    # 3. Aggregate System View
    battery_sns = []
    for sn, dev in coordinator.data.items():
        dtype = str(dev.get("device_type_code", "")).upper()
        if any(x in dtype for x in ["BATTERY", "EMS", "HYBRID", "ALL_IN_ONE"]):
            battery_sns.append(sn)

    if len(battery_sns) > 1:
        enable_virtual = entry.options.get("enable_virtual_battery", False)
        if enable_virtual:
            _LOGGER.debug("HYXi Aggregator: Creating 'Battery System' entities...")
            for description in VSYS_SENSORS:
                parent_entities.append(
                    HyxiBatterySystemSensor(coordinator, entry, description)
                )

    # FINAL REGISTRATION: Register Inverters/Service first, then Children
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
        self._last_valid_value = None

        dev_data = coordinator.data.get(sn, {})
        metrics = dev_data.get("metrics", {})
        bat_sn = metrics.get("batSn")

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
        self.entity_id = f"sensor.hyxi_{self._actual_sn}_{description.key.lower()}"

    @property
    def native_value(self):
        """Returns the sensor value with correct data typing for HA."""
        metrics = self.coordinator.data.get(self._sn, {}).get("metrics", {})
        value = metrics.get(self.entity_description.key)

        if value is None or value == "":
            return None

        check_key = self.entity_description.key.lower()

        if check_key in ["batsoc", "batsoh", "signalval"]:
            try:
                return int(round(float(value), 0))
            except (ValueError, TypeError):
                return None

        if self.entity_description.key == "collectTime":
            try:
                val_int = int(value)
                if val_int > 9999999999:
                    val_int = val_int / 1000
                return datetime.fromtimestamp(val_int, tz=UTC)
            except (ValueError, TypeError, OSError):
                return None

        if self.entity_description.key == "last_seen":
            return dt_util.parse_datetime(str(value))

        if self.entity_description.native_unit_of_measurement is not None:
            try:
                num_value = round(float(value), 2)

                # Jitter Filter
                if (
                    self.entity_description.state_class
                    == SensorStateClass.TOTAL_INCREASING
                ):
                    if self._last_valid_value is not None:
                        if 0 < num_value < self._last_valid_value:
                            _LOGGER.debug(
                                "HYXi Jitter filtered for %s: Blocked drop from %s to %s",
                                self.entity_description.key,
                                self._last_valid_value,
                                num_value,
                            )
                            return self._last_valid_value

                self._last_valid_value = num_value
                return num_value
            except (ValueError, TypeError):
                return value

        return value


class HyxiLastUpdateSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor for the Integration health."""

    _attr_has_entity_name = True
    _attr_name = "Integration Last Updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
        self._last_valid_value = None

        self._attr_unique_id = f"hyxi_vsys_{entry.entry_id}_{description.key}"
        self._attr_translation_key = description.key.lower()
        self.entity_id = f"sensor.hyxi_vsys_{entry.entry_id}_{description.key.lower()}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"hyxi_system_{entry.entry_id}")},
            "name": "HYXi Battery System",
            "manufacturer": "HYXi Power",
            "model": "Aggregated Storage",
        }

    @property
    def native_value(self):
        """Value with floating point protection and proper typing."""
        summary = self.coordinator.get_battery_summary()
        if not summary:
            return None
        value = summary.get(self._key)

        if value is None or value == "":
            return None

        if "soc" in self._key.lower() or "soh" in self._key.lower():
            try:
                return int(round(float(value), 0))
            except (ValueError, TypeError):
                return None

        if self.entity_description.native_unit_of_measurement is not None:
            try:
                num_value = round(float(value), 2)

                # Jitter Filter
                if (
                    self.entity_description.state_class
                    == SensorStateClass.TOTAL_INCREASING
                ):
                    if self._last_valid_value is not None:
                        if 0 < num_value < self._last_valid_value:
                            _LOGGER.debug(
                                "HYXi Jitter filtered for %s: Blocked drop from %s to %s",
                                self._key,
                                self._last_valid_value,
                                num_value,
                            )
                            return self._last_valid_value

                self._last_valid_value = num_value
                return num_value
            except (ValueError, TypeError):
                return value

        return value

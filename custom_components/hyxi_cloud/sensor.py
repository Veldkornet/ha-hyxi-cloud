"""HYXI Cloud Sensor platform."""

import logging
from datetime import UTC
from datetime import datetime

from homeassistant.components.sensor import EntityCategory
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Constants for optimization
INT_SENSOR_KEYS = {"batsoc", "batsoh", "signalval"}

SENSOR_TYPES = [
    # Phase Powers
    SensorEntityDescription(
        key="ph1Loadp",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SensorEntityDescription(
        key="ph2Loadp",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SensorEntityDescription(
        key="ph3Loadp",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    # PV String Sensors
    SensorEntityDescription(
        key="pv1v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
    ),
    SensorEntityDescription(
        key="pv2v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
    ),
    SensorEntityDescription(
        key="pv1i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
    ),
    SensorEntityDescription(
        key="pv2i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
    ),
    SensorEntityDescription(
        key="pv1p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    SensorEntityDescription(
        key="pv2p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    # Battery Electricals
    SensorEntityDescription(
        key="batV",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-battery",
    ),
    SensorEntityDescription(
        key="batI",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
    ),
    # Internal Spec Sensors
    SensorEntityDescription(
        key="vbus",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="f",
        native_unit_of_measurement="Hz",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="acE",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:flash",
    ),
    # Status Codes
    SensorEntityDescription(
        key="deviceState",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["1", "2", "3", "10"],
        icon="mdi:information",
    ),
    # Hardware Capabilities
    SensorEntityDescription(
        key="ratedPower",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:lightning-bolt",
    ),
    SensorEntityDescription(
        key="ratedVoltage",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:lightning-bolt",
    ),
    SensorEntityDescription(
        key="childNum",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:lan-connect",
    ),
    # Phase 1 New Metrics
    SensorEntityDescription(
        key="ph1v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SensorEntityDescription(
        key="ph1i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SensorEntityDescription(
        key="ph1p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    # Phase 2 New Metrics
    SensorEntityDescription(
        key="ph2v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SensorEntityDescription(
        key="ph2i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SensorEntityDescription(
        key="ph2p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    # Phase 3 New Metrics
    SensorEntityDescription(
        key="ph3v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SensorEntityDescription(
        key="ph3i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    SensorEntityDescription(
        key="ph3p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
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


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up HYXI sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        _LOGGER.warning("HYXI Setup: No data available in coordinator during setup")
        return

    device_registry = dr.async_get(hass)
    entities = []

    # Pre-register devices to fix 'via_device' order dependency
    for sn, dev_data in coordinator.data.items():
        # Pre-register parent device
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, sn)},
            name=dev_data.get("device_name", f"Device {sn}"),
            manufacturer="HYXI Power",
            model=dev_data.get("model"),
            sw_version=dev_data.get("sw_version"),
            hw_version=dev_data.get("hw_version"),
            serial_number=sn,
        )

        # Pre-register child battery device if it exists
        metrics = dev_data.get("metrics", {})
        bat_sn = metrics.get("batSn")
        if bat_sn:
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, bat_sn)},
                name=f"Battery {bat_sn}",
                manufacturer="HYXI Power",
                model="Energy Storage System",
                serial_number=bat_sn,
                via_device=(DOMAIN, sn),
            )

    # Filter constants
    battery_sensors = [
        "batSoc",
        "pbat",
        "batSoh",
        "bat_charge_total",
        "bat_discharge_total",
        "bat_charging",
        "bat_discharging",
        "batV",
        "batI",
    ]
    collector_sensors = ["signalIntensity", "signalVal", "wifiVer", "comMode"]
    heartbeat_sensor = ["last_seen"]

    # 1. Hardware Loop
    for sn, dev_data in coordinator.data.items():
        device_type = str(dev_data.get("device_type_code", "")).upper()
        metrics = dev_data.get("metrics", {})

        _LOGGER.debug(
            "HYXI Processing Device %s (Type: %s). Metrics keys: %s",
            sn,
            device_type,
            list(metrics.keys()),
        )

        for description in SENSOR_TYPES:
            key = description.key
            should_add = False

            if key in heartbeat_sensor:
                should_add = True
            elif key in collector_sensors:
                if "COLLECTOR" in device_type or "DMU" in device_type:
                    should_add = True
            elif key in metrics and metrics[key] is not None:
                if str(metrics[key]) != "":
                    should_add = True

            if should_add:
                if key in battery_sensors and (
                    "COLLECTOR" in device_type or "DMU" in device_type
                ):
                    continue
                entities.append(HyxiSensor(coordinator, sn, description))

    # 2. Integration Health
    entities.append(HyxiLastUpdateSensor(coordinator, entry))

    # FINAL REGISTRATION
    if entities:
        async_add_entities(entities)


class HyxiBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for HYXI sensors with shared logic."""

    def __init__(self, coordinator):
        """Initialize the base sensor."""
        super().__init__(coordinator)
        self._last_valid_value = None
        self._last_logged_glitch = None

    def _process_numeric_value(self, value):
        """Common numeric processing for sensors."""
        if value is None or value == "":
            return None

        if self.entity_description.native_unit_of_measurement is None:
            return value

        try:
            num_value = round(float(value), 2)

            if self.entity_description.state_class in (
                SensorStateClass.TOTAL_INCREASING,
                "total_increasing",
            ):
                if self._last_valid_value is None and self.hass is not None:
                    old_state = self.hass.states.get(self.entity_id)
                    if old_state and old_state.state not in (
                        None,
                        "unknown",
                        "unavailable",
                    ):
                        try:
                            self._last_valid_value = float(old_state.state)
                        except (ValueError, TypeError):
                            _LOGGER.debug(
                                "HYXI Initialization: Could not parse previous state '%s' for %s",
                                old_state.state,
                                self.entity_id,
                            )

                if self._last_valid_value is not None:
                    # Anti-Dip Check
                    if num_value < self._last_valid_value:
                        # A drop is ONLY a valid reset if the new value is practically zero (e.g., < 0.1)
                        # AND the drop is significant (meaning it's not just a tiny dip).
                        is_valid_reset = (0.0 <= num_value <= 0.1) and (
                            (self._last_valid_value - num_value)
                            > (self._last_valid_value * 0.5)
                        )

                        if not is_valid_reset:
                            if self._last_logged_glitch != num_value:
                                _LOGGER.debug(
                                    "HYXI Glitch Filter: Prevented %s drop (%s -> %s)",
                                    self.entity_description.key,
                                    self._last_valid_value,
                                    num_value,
                                )
                                self._last_logged_glitch = num_value
                            return self._last_valid_value

                    # Anti-Spike Check
                    elif (num_value - self._last_valid_value) > 100.0:
                        if self._last_logged_glitch != num_value:
                            _LOGGER.debug(
                                "HYXI High-Spike Filter: Ignoring impossible jump on %s from %s to %s",
                                self.entity_description.key,
                                self._last_valid_value,
                                num_value,
                            )
                            self._last_logged_glitch = num_value
                        return self._last_valid_value

            self._last_logged_glitch = None
            self._last_valid_value = num_value
            return num_value
        except (ValueError, TypeError):
            return value


class HyxiSensor(HyxiBaseSensor):
    """Representation of a Physical HYXI Sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, sn, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._sn = sn

        dev_data = coordinator.data.get(sn, {})
        metrics = dev_data.get("metrics", {})
        bat_sn = metrics.get("batSn")

        bat_keys = [
            "batSoc",
            "pbat",
            "bat_charging",
            "bat_discharging",
            "bat_charge_total",
            "bat_discharge_total",
            "batSoh",
            "batV",
            "batI",
        ]

        if description.key in bat_keys and bat_sn:
            self._actual_sn = bat_sn
            self._attr_device_info = {
                "identifiers": {(DOMAIN, bat_sn)},
                "name": f"Battery {bat_sn}",
                "manufacturer": "HYXI Power",
                "model": "Energy Storage System",
                "serial_number": bat_sn,
                "via_device": (DOMAIN, sn),
            }
        else:
            self._actual_sn = sn
            self._attr_device_info = {
                "identifiers": {(DOMAIN, sn)},
                "name": dev_data.get("device_name", f"Device {sn}"),
                "manufacturer": "HYXI Power",
                "model": dev_data.get("model"),
                "sw_version": dev_data.get("sw_version"),
                "hw_version": dev_data.get("hw_version"),
                "serial_number": sn,
            }

        self._attr_unique_id = f"hyxi_{self._actual_sn}_{description.key}"
        self._attr_translation_key = description.key.lower()
        self.entity_id = f"sensor.hyxi_{self._actual_sn}_{description.key.lower()}"

    def _log_glitch_once(self, num_value: float, message: str, *args) -> None:
        """Helper to log glitch prevention only once per glitch value."""
        if self._last_logged_glitch != num_value:
            _LOGGER.debug(message, *args)
            self._last_logged_glitch = num_value

    @property
    def native_value(self):
        """Returns the sensor value with correct data typing and anti-dip protection."""
        metrics = self.coordinator.data.get(self._sn, {}).get("metrics", {})
        value = metrics.get(self.entity_description.key)

        if value is None or value == "":
            return None

        if self.entity_description.key.lower() in INT_SENSOR_KEYS:
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

        return self._process_numeric_value(value)


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
            "name": "HYXI Cloud Service",
            "manufacturer": "HYXI Power",
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

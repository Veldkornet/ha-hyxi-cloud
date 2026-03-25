"""HYXI Cloud Sensor platform."""

import logging
from datetime import UTC
from datetime import datetime

from homeassistant.components.sensor import EntityCategory
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .const import MANUFACTURER

_LOGGER = logging.getLogger(__name__)


def mask_sn(sn: str) -> str:
    """Mask a serial number for logs, replacing middle chars with X.

    Matches the _mask_id format used in the API library.
    """
    if not sn:
        return "****"
    sn_str = str(sn)
    if len(sn_str) < 8:
        return "****"
    middle_len = len(sn_str) - 6
    return f"{sn_str[:3]}{'X' * middle_len}{sn_str[-3:]}"


# Constants for optimization
INT_SENSOR_KEYS = {"batsoc", "batsoh", "signalval"}

BATTERY_SENSORS = {
    "batSoc",
    "pbat",
    "batSoh",
    "bat_charge_total",
    "bat_discharge_total",
    "bat_charging",
    "bat_discharging",
    "batV",
    "batI",
}


# Helper to map device codes to translation keys for HA sensor states
DEVICE_TYPE_KEYS = {
    "1": "hybrid_inverter",
    "2": "grid_connected_inverter",
    "3": "collector",
    "15": "micro_ess",
    "16": "micro_ess",
    "106": "hybrid_inverter",
    "607": "collector",
    "HYBRID_INVERTER": "hybrid_inverter",
    "STRING_INVERTER": "grid_connected_inverter",
    "MICRO_INVERTER": "grid_connected_inverter",
    "EMS": "micro_ess",
    "DMU": "collector",
    "COLLECTOR": "collector",
}


def normalize_device_type(code: str | int | float) -> str:
    """Normalize a device type code/string to a translation key.

    Ensures that values match the keys in strings.json (lowercase, no spaces).
    """
    if code is None or code == "":
        return "unknown"

    code_str = str(code).upper().strip()

    # 1. Check numeric/direct mapping (handle float strings like "15.0")
    lookup_key = code_str
    if "." in code_str:
        try:
            lookup_key = str(int(float(code_str)))
        except (ValueError, TypeError):
            # If float conversion fails (e.g. string labels), just use original code_str
            pass

    if lookup_key in DEVICE_TYPE_KEYS:
        return DEVICE_TYPE_KEYS[lookup_key]

    # 2. String mapping (if API returned a name instead of code)
    if "COLLECTOR" in code_str or "DMU" in code_str:
        return "collector"
    if "INVERTER" in code_str:
        if "GRID" in code_str:
            return "grid_connected_inverter"
        return "hybrid_inverter"
    if "ESS" in code_str or "HALO" in code_str:
        return "micro_ess"

    return "unknown"


COLLECTOR_SENSORS = {"signalIntensity", "signalVal", "wifiVer", "comMode"}
HEARTBEAT_SENSORS = {"last_seen"}

BASE_KEYS_COLLECTOR = HEARTBEAT_SENSORS | COLLECTOR_SENSORS
BASE_KEYS_OTHER = HEARTBEAT_SENSORS

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
    SensorEntityDescription(
        key="maxChargePower",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-arrow-up",
    ),
    SensorEntityDescription(
        key="maxDischargePower",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-arrow-down",
    ),
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
    SensorEntityDescription(
        key="duisoc",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-high",
    ),
    SensorEntityDescription(
        key="cuvolt",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
    ),
    SensorEntityDescription(
        key="cucurr",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
    ),
    SensorEntityDescription(
        key="cupower",
        native_unit_of_measurement="kW",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
    ),
    SensorEntityDescription(
        key="cusoh",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
    ),
    SensorEntityDescription(
        key="cuavgcelltemp",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
    ),
    SensorEntityDescription(
        key="duichargetoday",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-plus",
    ),
    SensorEntityDescription(
        key="duiunchargetoday",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-minus",
    ),
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
    SensorEntityDescription(
        key="packNum",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:layers-triple",
    ),
    SensorEntityDescription(
        key="batCap",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check",
    ),
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
        key="device_type",
        translation_key="device_type",
        icon="mdi:information-outline",
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

SENSOR_TYPES_BY_KEY = {desc.key: desc for desc in SENSOR_TYPES}


def _get_raw_device_code(dev_data: dict) -> str:
    """Extract the raw device type code from device data payload."""
    return (
        dev_data.get("device_type_code")
        or dev_data.get("deviceType")
        or dev_data.get("devType")
        or ""
    )


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up HYXI sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        _LOGGER.warning("HYXI Setup: No data available in coordinator during setup")
        return

    entities = []

    # 1. Hardware Loop
    for sn, dev_data in coordinator.data.items():
        # Check all possible API keys for device type
        raw_code = _get_raw_device_code(dev_data)
        device_type = normalize_device_type(raw_code)
        metrics = dev_data.get("metrics", {})

        _LOGGER.debug(
            "HYXI Processing Device %s (Normalized Type: %s). Metrics keys: %s",
            mask_sn(sn),
            device_type,
            list(metrics.keys()),
        )

        is_collector_or_dmu = device_type == "collector"

        if is_collector_or_dmu:
            keys_to_add = BASE_KEYS_COLLECTOR.copy()
        else:
            keys_to_add = BASE_KEYS_OTHER.copy()

        # Add static device type sensor
        keys_to_add.add("device_type")

        for k, v in metrics.items():
            if v is not None and v != "":
                keys_to_add.add(k)

        valid_keys = keys_to_add.intersection(SENSOR_TYPES_BY_KEY)
        if is_collector_or_dmu:
            valid_keys.difference_update(BATTERY_SENSORS)

        for key in valid_keys:
            description = SENSOR_TYPES_BY_KEY[key]
            entities.append(HyxiSensor(coordinator, sn, description))

    # 2. Integration Health
    entities.append(HyxiLastUpdateSensor(coordinator, entry))

    # FINAL REGISTRATION
    if entities:
        async_add_entities(entities)


class HyxiBaseSensor(CoordinatorEntity, SensorEntity, RestoreEntity):
    """Base class for HYXI sensors with shared logic."""

    def __init__(self, coordinator):
        """Initialize the base sensor."""
        super().__init__(coordinator)
        self._last_valid_value = None
        self._last_logged_glitch = None

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        if self.entity_description.state_class in (
            SensorStateClass.TOTAL_INCREASING,
            "total_increasing",
        ):
            if (last_state := await self.async_get_last_state()) is not None:
                try:
                    self._last_valid_value = float(last_state.state)
                except (
                    ValueError,
                    TypeError,
                ):
                    _LOGGER.debug(
                        "HYXI Restore: Could not parse restored state '%s' for %s",
                        last_state.state,
                        mask_sn(self._actual_sn)
                        if hasattr(self, "_actual_sn")
                        else self.entity_id,
                    )

    def _log_glitch_once(self, num_value: float, message: str, *args) -> None:
        """Helper to log glitch prevention only once per glitch value."""
        if self._last_logged_glitch != num_value:
            _LOGGER.debug(message, *args)
            self._last_logged_glitch = num_value

    def _check_anti_dip(self, num_value: float) -> float | None:
        """Check for and prevent invalid value drops."""
        if num_value >= self._last_valid_value:
            return None

        # A drop is ONLY a valid reset if the new value is practically zero (e.g., < 0.1)
        # AND the drop is significant (meaning it's not just a tiny dip).
        is_valid_reset = (0.0 <= num_value <= 0.1) and (
            (self._last_valid_value - num_value) > (self._last_valid_value * 0.5)
        )

        if not is_valid_reset:
            self._log_glitch_once(
                num_value,
                "HYXI Glitch Filter: Prevented %s drop (%s -> %s)",
                self.entity_description.key,
                self._last_valid_value,
                num_value,
            )
            return self._last_valid_value

        return None

    def _check_anti_spike(self, num_value: float) -> float | None:
        """Check for and prevent impossible value jumps."""
        if (num_value - self._last_valid_value) > 100.0:
            self._log_glitch_once(
                num_value,
                "HYXI High-Spike Filter: Ignoring impossible jump on %s from %s to %s",
                self.entity_description.key,
                self._last_valid_value,
                num_value,
            )
            return self._last_valid_value

        return None

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
                if self._last_valid_value is not None:
                    dip_result = self._check_anti_dip(num_value)
                    if dip_result is not None:
                        return dip_result

                    spike_result = self._check_anti_spike(num_value)
                    if spike_result is not None:
                        return spike_result

            self._last_logged_glitch = None
            self._last_valid_value = num_value
            return num_value
        except (
            ValueError,
            TypeError,
        ):
            return value


class HyxiSensor(HyxiBaseSensor):
    """Representation of a Physical HYXI Sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, sn, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._sn = sn

        # Determing actual SN (e.g. Battery SN for battery sensors)
        dev_data = coordinator.data.get(sn, {})
        metrics = dev_data.get("metrics", {})
        bat_sn = metrics.get("batSn")

        if description.key in BATTERY_SENSORS and bat_sn:
            self._actual_sn = bat_sn
        else:
            self._actual_sn = sn

        self._attr_unique_id = f"hyxi_{self._actual_sn}_{description.key}"
        self._attr_translation_key = description.key.lower()
        self.entity_id = f"sensor.hyxi_{self._actual_sn}_{description.key.lower()}"

    @property
    def device_info(self):
        """Return dynamic device information to ensure versions update in UI."""
        dev_data = self.coordinator.data.get(self._sn, {})
        metrics = dev_data.get("metrics", {})
        bat_sn = metrics.get("batSn")

        if self.entity_description.key in BATTERY_SENSORS and bat_sn:
            return {
                "identifiers": {(DOMAIN, bat_sn)},
                "name": f"Battery {bat_sn}",
                "manufacturer": MANUFACTURER,
                "model": "Energy Storage System",
                "serial_number": bat_sn,
                "via_device": (DOMAIN, self._sn),
            }

        # For Stick/Inverter, provide dynamic software versions
        sw_version = dev_data.get("sw_version")
        hw_version = dev_data.get("hw_version")

        # Combine versions for Datalogger if wifiver is present
        device_type = normalize_device_type(_get_raw_device_code(dev_data))
        if device_type == "collector":
            wifi_ver = metrics.get("wifiVer")
            if wifi_ver:
                sw_version = f"{sw_version} (App) / {wifi_ver} (WiFi)"

        return {
            "identifiers": {(DOMAIN, self._sn)},
            "name": dev_data.get("device_name") or f"Device {self._sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "sw_version": sw_version,
            "hw_version": hw_version,
            "serial_number": self._sn,
        }

    def _parse_device_type(self, dev_data, value):
        return normalize_device_type(_get_raw_device_code(dev_data))

    def _parse_int_sensor(self, dev_data, value):
        if value is None or value == "":
            return None
        try:
            return int(round(float(value), 0))
        except (
            ValueError,
            TypeError,
        ):
            return None

    def _parse_collect_time(self, dev_data, value):
        if value is None or value == "":
            return None
        try:
            val_int = int(value)
            if val_int > 9999999999:
                val_int = val_int / 1000
            return datetime.fromtimestamp(val_int, tz=UTC)
        except (
            ValueError,
            TypeError,
            OSError,
            OverflowError,
        ):
            return None

    def _parse_last_seen(self, dev_data, value):
        if value is None or value == "":
            return None
        return dt_util.parse_datetime(str(value))

    def _parse_default(self, dev_data, value):
        if value is None or value == "":
            return None
        return self._process_numeric_value(value)

    @property
    def native_value(self):
        """Returns the sensor value with correct data typing and anti-dip protection."""
        dev_data = self.coordinator.data.get(self._sn, {})
        metrics = dev_data.get("metrics", {})
        value = metrics.get(self.entity_description.key)

        if self.entity_description.key == "device_type":
            return self._parse_device_type(dev_data, value)
        if self.entity_description.key.lower() in INT_SENSOR_KEYS:
            return self._parse_int_sensor(dev_data, value)
        if self.entity_description.key == "collectTime":
            return self._parse_collect_time(dev_data, value)
        if self.entity_description.key == "last_seen":
            return self._parse_last_seen(dev_data, value)

        return self._parse_default(dev_data, value)


class HyxiLastUpdateSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor for the Integration health."""

    _attr_has_entity_name = True
    _attr_translation_key = "integration_last_updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_integration_last_updated"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HYXI Cloud Service",
            "manufacturer": MANUFACTURER,
            "model": "Cloud API Bridge",
        }

    @property
    def native_value(self):
        return self.coordinator.hyxi_metadata.get("last_success")

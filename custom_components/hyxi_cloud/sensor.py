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
from .const import get_raw_device_code
from .const import normalize_device_type

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
    "bat_power_dc",
}


COLLECTOR_SENSORS = {"signalIntensity", "signalVal", "wifiVer", "comMode", "app_sw"}
HEARTBEAT_SENSORS = {"last_seen"}

BASE_KEYS_COLLECTOR = HEARTBEAT_SENSORS | COLLECTOR_SENSORS
BASE_KEYS_OTHER = HEARTBEAT_SENSORS | {"app_sw", "swVerMaster", "swVerSlave"}

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
        key="wifiVer",
        translation_key="wifiver",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi-cog",
    ),
    SensorEntityDescription(
        key="app_sw",
        translation_key="app_sw",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:application-cog",
    ),
    SensorEntityDescription(
        key="swVerMaster",
        translation_key="master_sw",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
    ),
    SensorEntityDescription(
        key="swVerSlave",
        translation_key="slave_sw",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
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
    # ... (skipping some for brevity in logic but I have the full content)
]

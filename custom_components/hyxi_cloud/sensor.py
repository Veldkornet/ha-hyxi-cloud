"""HYXI Cloud Sensor platform."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from homeassistant.components.sensor import (
    EntityCategory,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    CONF_MODBUS_FRAMER,
    CONF_MODBUS_TYPE,
    CONF_PUSH_RATE,
    DEFAULT_MODBUS_FRAMER,
    DOMAIN,
    MANUFACTURER,
    MODBUS_TYPE_SERIAL,
    NULL_VALUES,
    detect_phase_type,
    get_raw_device_code,
    get_software_version,
    is_battery_control_enabled,
    is_control_capable_device_type,
    is_modbus_entry,
    is_null_value,
    is_zero_value,
    mask_sn,
    normalize_device_type,
)

if TYPE_CHECKING:
    from .coordinator import HyxiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


# pylint: disable=too-many-lines
# Constants for optimization
INT_SENSOR_KEYS = {
    "batsoc",
    "batsoh",
    "signalval",
    "pvnum",
    "packNum",
    "q",
    "invsts",
    "faultsts",
    "gridsts",
    "devicegridconn",
    "deviceswitchstatus",
    "ratedfrequency",
}

BATTERY_SENSORS = {
    "batSoc",
    "pbat",
    "batP",
    "batSoh",
    "bat_charge_total",
    "bat_discharge_total",
    "bat_charging",
    "bat_discharging",
    "batV",
    "batI",
    "batVch",
    "batVcl",
    "batTch",
    "batTcl",
    "batTmp",
    "batIcm",
    "batIdm",
    "batCharge",
    "batDisCharge",
    "totalEchg",
    "totalEdchg",
    "bmsState",
    "batOperatingStatus",
    "batAlarm1",
    "batAlarm2",
    "batAlarm3",
    "batCapacityAh",
    "batNominalCapacity",
    "llcBusVoltage",
    "batChargeV",
    "batChargeI",
    "batChargeP",
    "batDischargeV",
    "batDischargeI",
    "batDischargeP",
}


COLLECTOR_SENSORS = {"signalIntensity", "signalVal", "wifiVer", "comMode", "app_sw"}
HEARTBEAT_SENSORS = {"last_seen"}

# Keys the cloud API has never produced for any device -- confirmed both
# by their absence from hyxi_cloud_api's own source and by a real cloud
# entry showing every one of them stuck on "unknown". The Modbus register
# maps are the only source for these, and pre-registering them for a
# cloud entry the same way genuinely webhook-only metrics are
# pre-registered just means a sensor stuck on "unknown" forever, the same
# problem this was meant to solve, not avoid. Excluded from
# pre-registration below; the "process dynamically available valid
# metrics keys" loop already adds these for Modbus/HALO entries, since
# that client reads them on every poll.
CLOUD_NEVER_PRODUCES = {
    "acE",
    "acP",
    "batIcm",
    "batIdm",
    "bmsState",
    "batCapacityAh",
    "batAlarm1",
    "batAlarm2",
    "batAlarm3",
    "batChargeV",
    "batChargeI",
    "batChargeP",
    "batDischargeV",
    "batDischargeI",
    "batDischargeP",
    "batNominalCapacity",
    "batOperatingStatus",
    "llcBusVoltage",
}

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
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="ph2Loadp",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="ph3Loadp",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="ph1Loadv",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="ph2Loadv",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="ph3Loadv",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
    ),
    # PV String Sensors
    SensorEntityDescription(
        key="pv1v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="pv2v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="pv1i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="pv2i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="pv1p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="pv2p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="pv3v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="pv4v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="pv3i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="pv4i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="pv3p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="pv4p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        suggested_display_precision=0,
    ),
    # Battery Electricals
    SensorEntityDescription(
        key="batV",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-battery",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="batI",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    # Internal Spec Sensors
    SensorEntityDescription(
        key="vbus",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
    ),
    # Hardware Capabilities
    SensorEntityDescription(
        key="f",
        native_unit_of_measurement="Hz",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="acE",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:flash",
        suggested_display_precision=2,
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
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="ratedVoltage",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:lightning-bolt",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="ratedFrequency",
        native_unit_of_measurement="Hz",
        device_class=SensorDeviceClass.FREQUENCY,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="wifiVer",
        translation_key="wifiver",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi-cog",
    ),
    # Maintenance Sensors
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
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="maxDischargePower",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-arrow-down",
        suggested_display_precision=0,
    ),
    # Phase Powers Detailed
    SensorEntityDescription(
        key="ph1v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="ph1i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="ph1p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="ph2v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="ph2i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="ph2p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="ph3v",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="ph3i",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="ph3p",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        suggested_display_precision=0,
    ),
    # ESS / Battery Management (ESS specific)
    SensorEntityDescription(
        key="duisoc",
        native_unit_of_measurement="%",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-high",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="cuvolt",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="cucurr",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="cupower",
        native_unit_of_measurement="kW",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="cusoh",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="cuavgcelltemp",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="duichargetoday",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-plus",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="duiunchargetoday",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-minus",
        suggested_display_precision=2,
    ),
    # Hybrid Inverter Core Sensors
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
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="batP",
        translation_key="batp",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="ppv",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="home_load",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="grid_import",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-import",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="grid_export",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-export",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="bat_charging",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-up",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="bat_discharging",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-down",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="totalE",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="totalEb",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="totalEc",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="totalEnt",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-import",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="totalEpt",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-export",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="q",
        native_unit_of_measurement="var",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash-outline",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="bat_charge_total",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-plus-variant",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="bat_discharge_total",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-minus-variant",
        suggested_display_precision=2,
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
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="temp",
        translation_key="internal_temperature",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        suggested_display_precision=1,
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
        suggested_display_precision=2,
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
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="signalVal",
        native_unit_of_measurement="%",
        icon="mdi:wifi",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
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
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="vac",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="vpv",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="eToday",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power-variant",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="eTodayIn",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-import",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="efpv",
        translation_key="efpv",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power-variant",
        suggested_display_precision=2,
    ),
    # Micro ESS / New Telemetry Sensors
    SensorEntityDescription(
        key="invSts",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        # 0-4 have confirmed labels below. 6 is undocumented but observed
        # on real hardware -- listed with no state translation so it
        # displays as the raw number rather than "unknown", without
        # guessing what it means. Extend the same way if another
        # undocumented value shows up.
        options=["0", "1", "2", "3", "4", "6"],
        icon="mdi:information",
    ),
    SensorEntityDescription(
        key="faultSts",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["0", "1"],
        icon="mdi:alert",
    ),
    SensorEntityDescription(
        key="gridSts",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["0", "1"],
        icon="mdi:transmission-tower",
    ),
    SensorEntityDescription(
        key="deviceGridConn",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["0", "1"],
        icon="mdi:connection",
    ),
    SensorEntityDescription(
        key="deviceSwitchStatus",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["0", "1"],
        icon="mdi:power-switch",
    ),
    SensorEntityDescription(
        key="meterOnline",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["0", "1"],
        icon="mdi:electric-switch",
    ),
    SensorEntityDescription(
        key="gridMode",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["0", "1"],
        icon="mdi:transmission-tower",
    ),
    SensorEntityDescription(
        key="runCommand",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["0", "1"],
        icon="mdi:power",
    ),
    SensorEntityDescription(
        key="currentOperatingMode",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        # 1-7 have confirmed labels below. 13, 14, 15 and 16 are undocumented
        # but observed on real hardware under different conditions -- see
        # invSts above for why they're listed with no translation rather
        # than guessed or hidden.
        # pylint: disable-next=fixme
        # TODO: the vendor's 1-7 table looks more incomplete than a single
        # stray value would suggest. Keep adding newly observed values
        # here as they show up, and add a translated label once any of
        # them gets a confirmed meaning.
        options=["1", "2", "3", "4", "5", "6", "7", "13", "14", "15", "16"],
        icon="mdi:state-machine",
    ),
    # Raw diagnostic values with no documented unit or value table --
    # passed through rather than guessed. See docs/modbus-provenance.md.
    SensorEntityDescription(
        key="insulationResistance",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:omega",
    ),
    SensorEntityDescription(
        key="leakageCurrent",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash-alert",
    ),
    SensorEntityDescription(
        key="selfTestStatus",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:progress-check",
    ),
    SensorEntityDescription(
        key="pvPower",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="pvNum",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:solar-panel",
    ),
    SensorEntityDescription(
        key="acSideTemper",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="dcSideTemper",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="ambientTemper",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="boostTemper",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="dspTemper",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="gridF",
        native_unit_of_measurement="Hz",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="gridP",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="gridQ",
        native_unit_of_measurement="var",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="gridPfd",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:angle-acute",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="gridAp",
        native_unit_of_measurement="VA",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="offGridF",
        native_unit_of_measurement="Hz",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="offGridP",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:power-plug-off",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="offGridV",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="offGridI",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="offGridQ",
        native_unit_of_measurement="var",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:power-plug-off",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="offGridPfd",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:angle-acute",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="offGridAp",
        native_unit_of_measurement="VA",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:power-plug-off",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="batVch",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-high",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="batVcl",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-low",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="batTch",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:thermometer-high",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="batTcl",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:thermometer-low",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="batTmp",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:thermometer",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="batIcm",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="batIdm",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    # BMS/battery detail -- one or the other family reports each of these.
    SensorEntityDescription(
        key="bmsState",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-sync",
    ),
    SensorEntityDescription(
        key="batOperatingStatus",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["0", "1", "2", "3"],
        icon="mdi:battery-sync",
    ),
    # Raw BMS alarm words, undecoded -- see HaloFaults for why.
    SensorEntityDescription(
        key="batAlarm1",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:alert-circle-outline",
    ),
    SensorEntityDescription(
        key="batAlarm2",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:alert-circle-outline",
    ),
    SensorEntityDescription(
        key="batAlarm3",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:alert-circle-outline",
    ),
    SensorEntityDescription(
        key="batCapacityAh",
        native_unit_of_measurement="Ah",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-outline",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="batNominalCapacity",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-outline",
    ),
    SensorEntityDescription(
        key="llcBusVoltage",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:car-battery",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="batChargeV",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-arrow-up",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="batChargeI",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="batChargeP",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-arrow-up",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="batDischargeV",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-arrow-down",
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="batDischargeI",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="batDischargeP",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-arrow-down",
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="batCharge",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-plus",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="batDisCharge",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-minus",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="totalEchg",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-plus-variant",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="totalEdchg",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-minus-variant",
        suggested_display_precision=2,
    ),
]

SENSOR_TYPES_BY_KEY = {desc.key: desc for desc in SENSOR_TYPES}


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up HYXI sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        _LOGGER.warning("HYXI Setup: No data available in coordinator during setup")
        return

    entities: list[SensorEntity] = []

    # 1. Hardware Loop
    for sn, dev_data in coordinator.data.items():
        # Check all possible API keys for device type
        raw_code = get_raw_device_code(dev_data)
        device_type = normalize_device_type(raw_code)
        metrics = dev_data.get("metrics") or {}

        if _LOGGER.isEnabledFor(logging.DEBUG):
            logged_metrics = {
                k: (
                    mask_sn(str(v))
                    if k
                    in {
                        "deviceSn",
                        "parentSn",
                        "batSn",
                        "emsSn",
                        "alias",
                        "plantId",
                        "gprsImei",
                        "plantAddress",
                        "plantName",
                        "deviceName",
                        "alarmName",
                        "token",
                        "access_token",
                        "refresh_token",
                        "password",
                    }
                    and v is not None
                    else v
                )
                for k, v in metrics.items()
            }

            _LOGGER.debug(
                "HYXI Processing Device %s (Normalized Type: %s). Metrics: %s",
                mask_sn(sn),
                device_type,
                logged_metrics,
            )

        is_collector_or_dmu = device_type == "collector"

        base_keys = BASE_KEYS_COLLECTOR if is_collector_or_dmu else BASE_KEYS_OTHER
        local_battery_sensors = BATTERY_SENSORS

        # Pre-calculate base keys to process + specific static keys
        keys_to_add = set(base_keys)
        if is_modbus_entry(entry):
            # last_seen is a cloud heartbeat timestamp; neither Modbus
            # client ever populates it, so it would just sit frozen at
            # whatever a prior cloud entry for this same device last wrote.
            keys_to_add.discard("last_seen")
        keys_to_add.add("device_type")

        # Process dynamically available valid metrics keys
        for key, v in metrics.items():
            if v is not None and not (
                isinstance(v, str) and v.strip().lower() in NULL_VALUES
            ):
                keys_to_add.add(key)

        # Pre-register standard sensors to ensure webhook-only metrics are
        # successfully registered. Modbus has no webhook path -- a key
        # this poll didn't produce will never arrive later the way a cloud
        # metric can via push, so pre-registering it just means a sensor
        # that's permanently "unknown" instead of never created. Skipped
        # entirely for Modbus; the "process dynamically available valid
        # metrics keys" loop above already adds every key a Modbus client
        # actually reads, without needing this at all.
        if not is_collector_or_dmu and not is_modbus_entry(entry):
            # Common inverter sensors (always applicable)
            keys_to_add.update(
                {
                    "ph1Loadp",
                    "ph1v",
                    "ph1i",
                    "ph1p",
                    "pv1v",
                    "pv1i",
                    "pv1p",
                    "pv2v",
                    "pv2i",
                    "pv2p",
                    "home_load",
                    "grid_import",
                    "grid_export",
                    "ppv",
                    "totalE",
                    "totalEnt",
                    "totalEpt",
                    "totalEchg",
                    "acP",
                    "acE",
                    "gridP",
                    "gridF",
                    "invSts",
                    "gridSts",
                }
                - CLOUD_NEVER_PRODUCES
            )

            # Check phase type for Phase 2 & 3 sensors
            phase_type = detect_phase_type(dev_data)
            if phase_type == "three_phase":
                keys_to_add.update(
                    {
                        "ph2Loadp",
                        "ph2v",
                        "ph2i",
                        "ph2p",
                        "ph3Loadp",
                        "ph3v",
                        "ph3i",
                        "ph3p",
                    }
                )

            # Check if device type supports battery
            if device_type in ("hybrid_inverter", "all_in_one"):
                keys_to_add.update(local_battery_sensors - CLOUD_NEVER_PRODUCES)
                keys_to_add.update(
                    {
                        "bat_charging",
                        "bat_discharging",
                        "bat_power_dc",
                        "bat_charge_total",
                        "bat_discharge_total",
                    }
                )

        # O(1) removals instead of repeated conditionals
        if is_collector_or_dmu:
            keys_to_add.difference_update(local_battery_sensors)

        for key in keys_to_add:
            if description := SENSOR_TYPES_BY_KEY.get(key):
                entities.append(HyxiSensor(coordinator, sn, description))
    # 2. Integration Health
    entities.append(HyxiLastUpdateSensor(coordinator, entry))
    # Push is a HYXI cloud webhook subscription; a point-to-point RS485
    # link has no equivalent, so a Modbus entry never shows this sensor.
    if not is_modbus_entry(entry):
        entities.append(HyxiSubscriptionStatusSensor(coordinator, entry))
    else:
        # Connection type/framing is a Modbus-only distinction -- a cloud
        # entry has no physical link or wire framing of its own to report.
        entities.append(HyxiModbusConnectionTypeSensor(coordinator, entry))

    # 2b. Microinverter Aggregate Sensors
    has_micro_inverter = any(
        normalize_device_type(get_raw_device_code(dev_data)) == "micro_inverter"
        for dev_data in coordinator.data.values()
    )
    if has_micro_inverter:
        entities.append(
            HyxiMicroinverterSumSensor(
                coordinator,
                entry,
                metric_key="acP",
                description=SensorEntityDescription(
                    key="micro_ac_power_total",
                    translation_key="micro_ac_power_total",
                    native_unit_of_measurement="W",
                    device_class=SensorDeviceClass.POWER,
                    state_class=SensorStateClass.MEASUREMENT,
                    icon="mdi:solar-power",
                    suggested_display_precision=0,
                ),
            )
        )
        entities.append(
            HyxiMicroinverterSumSensor(
                coordinator,
                entry,
                metric_key="efpv",
                description=SensorEntityDescription(
                    key="micro_daily_yield_total",
                    translation_key="micro_daily_yield_total",
                    native_unit_of_measurement="kWh",
                    device_class=SensorDeviceClass.ENERGY,
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    icon="mdi:solar-power-variant",
                    suggested_display_precision=2,
                ),
            )
        )

    # 3. Battery protection telemetry
    if is_battery_control_enabled(entry, coordinator):
        for sn, dev_data in coordinator.data.items():
            device_type = normalize_device_type(get_raw_device_code(dev_data))
            if not is_control_capable_device_type(entry, device_type):
                continue
            # Local Modbus always resolves to the mode-control surface,
            # independent of phase -- HALO has no phase 2/3 registers at
            # all and would otherwise never pass the phase check below.
            # Cloud entries keep the original phase-based gate. Mirrors
            # _async_setup_battery_protection in __init__.py exactly,
            # which starts the controller that reads this entity's
            # restored state back on startup (protection.py,
            # async_start) -- a HALO Modbus device that gets a
            # controller but not this sensor loses last_sent_mode across
            # every restart, with nothing to restore it from.
            if not is_modbus_entry(entry):
                phase = detect_phase_type(dev_data)
                if phase not in ("three_phase", "single_phase"):
                    continue
            entities.append(HyxiLastSentModeSensor(coordinator, sn))

    # 4. Energy Manager sensors (EM-only)
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)
    if entry.options.get(CONF_EM_ENABLED) and em_sn and em_sn in coordinator.data:
        em_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{em_sn}_energy_manager")},
            name="Energy Manager",
            manufacturer=MANUFACTURER,
            model="Energy Manager",
            via_device=(DOMAIN, em_sn),
        )
        entities.append(
            EMSensor(
                coordinator, em_sn, EMSensorDef("current_decision", em_device_info)
            )
        )
        entities.append(
            EMSensor(coordinator, em_sn, EMSensorDef("last_action", em_device_info))
        )
        entities.append(
            EMSensor(
                coordinator,
                em_sn,
                EMSensorDef("status", em_device_info, icon="mdi:state-machine"),
            )
        )
        entities.append(
            EMSensor(
                coordinator,
                em_sn,
                EMSensorDef(
                    "battery_energy_available",
                    em_device_info,
                    unit="Wh",
                    device_class=SensorDeviceClass.ENERGY,
                ),
            )
        )
        entities.append(
            EMSensor(
                coordinator,
                em_sn,
                EMSensorDef(
                    "hours_until_sunrise",
                    em_device_info,
                    unit="h",
                    icon="mdi:weather-sunset-up",
                ),
            )
        )
        entities.append(
            EMSensor(
                coordinator,
                em_sn,
                EMSensorDef(
                    "hours_until_sunset",
                    em_device_info,
                    unit="h",
                    icon="mdi:weather-sunset-down",
                ),
            )
        )
        entities.append(
            EMSensor(
                coordinator,
                em_sn,
                EMSensorDef(
                    "p1_average",
                    em_device_info,
                    unit="W",
                    device_class=SensorDeviceClass.POWER,
                    state_class=SensorStateClass.MEASUREMENT,
                ),
            )
        )

    # FINAL REGISTRATION
    if entities:
        async_add_entities(entities)


class HyxiBaseSensor(
    CoordinatorEntity["HyxiDataUpdateCoordinator"], SensorEntity, RestoreEntity
):
    """Base class for HYXI sensors with shared logic."""

    def __init__(self, coordinator):
        """Initialize the base sensor."""
        super().__init__(coordinator)
        self._last_valid_value: float | None = None
        self._last_valid_time: datetime | None = None
        self._last_logged_glitch: float | str | None = None

    def _update_native_value(self):
        """Update the cached native value. Should be overridden by subclasses."""

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
                    self._last_valid_time = None
                    self._update_native_value()
                except ValueError, TypeError:
                    _LOGGER.debug(
                        "HYXI Restore: Could not parse restored state '%s' for %s",
                        last_state.state,
                        mask_sn(self._actual_sn)
                        if hasattr(self, "_actual_sn")
                        else self.entity_id,
                    )

    def _log_glitch_once(
        self,
        value: float | str,
        message: str,
        *args,
        level: int = logging.DEBUG,
    ) -> None:
        """Helper to log a message only once per distinct value."""
        if self._last_logged_glitch != value:
            _LOGGER.log(level, message, *args)
            self._last_logged_glitch = value

    def _check_anti_dip(self, num_value: float) -> float | None:
        """Check for and prevent invalid value drops."""
        if self._last_valid_value is None or num_value >= self._last_valid_value:
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
        if self._last_valid_value is None:
            return None

        # Allow threshold scaling based on time elapsed since the last update
        time_elapsed_hours = 168.0  # Default to 1 week if last update time is unknown
        if self._last_valid_time is not None:
            now = dt_util.utcnow()
            time_elapsed_hours = max(
                0.0, (now - self._last_valid_time).total_seconds() / 3600.0
            )

        max_allowed_jump = round(100.0 + (50.0 * time_elapsed_hours), 2)

        if (num_value - self._last_valid_value) > max_allowed_jump:
            self._log_glitch_once(
                num_value,
                "HYXI High-Spike Filter: Ignoring impossible jump on %s from %s to %s (max allowed: %s)",
                self.entity_description.key,
                self._last_valid_value,
                num_value,
                max_allowed_jump,
            )
            return self._last_valid_value

        return None

    def _process_numeric_value(self, value):
        """Common numeric processing for sensors."""
        if is_null_value(value):
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
            self._last_valid_value = num_value
            self._last_valid_time = dt_util.utcnow()
            return num_value
        except ValueError, TypeError:
            _LOGGER.debug(
                "Could not parse numeric value for %s: %r",
                self.entity_description.key,
                value,
            )
            return value


class _SameQuantityFallback(NamedTuple):
    """One same-quantity substitution: primary key -> fallback key.

    Each entry substitutes one raw metric for another that carries the
    *same* physical quantity, for device types where the primary key is
    known not to be populated -- no scaling factor, no unverified
    constant (unlike the acP/genP/gridP adjustment removed from
    native_value(); see the comment above _parse_device_type for why).
    """

    fallback_key: str
    device_types: tuple[str, ...]
    treat_zero_as_null: bool = False


class HyxiSensor(HyxiBaseSensor):
    """Representation of a Physical HYXI Sensor."""

    _attr_has_entity_name = True
    _PARSERS: ClassVar[dict[str, str]] = {
        "device_type": "_parse_device_type",
        "app_sw": "_parse_app_sw",
        "swvermaster": "_parse_sw_ver",
        "swverslave": "_parse_sw_ver",
        "collecttime": "_parse_collect_time",
        "last_seen": "_parse_last_seen",
    }
    _SAME_QUANTITY_FALLBACKS: ClassVar[dict[str, _SameQuantityFallback]] = {
        # acE -> efpv: PR #312. Field reports (incl. HYX-M2000-SW) showed
        # MICRO_INVERTER devices report acE as 0.0 and carry the real
        # daily-energy figure in efpv (Daily PV Yield) instead.
        "acE": _SameQuantityFallback(
            "efpv",
            ("grid_connected_inverter", "micro_inverter"),
            treat_zero_as_null=True,
        ),
        # gridF -> f: PR #556. Some grid-connected/micro-inverter models
        # send grid frequency under "f" rather than "gridF", leaving the
        # pre-registered gridF sensor stuck at "unknown".
        "gridF": _SameQuantityFallback(
            "f", ("grid_connected_inverter", "micro_inverter")
        ),
        # batTmp -> batTch: PR #556. When a hybrid/all-in-one device omits
        # batTmp, batTch (max cell temperature) is used as a safe,
        # conservative stand-in for battery-protection purposes.
        "batTmp": _SameQuantityFallback("batTch", ("hybrid_inverter", "all_in_one")),
    }

    def __init__(self, coordinator: Any, sn: str, description: Any) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._sn = sn

        # Cache dev_data and metrics to avoid repeated lookups
        self._dev_data = coordinator.data.get(sn) or {}
        self._metrics = self._dev_data.get("metrics") or {}

        raw_code = get_raw_device_code(self._dev_data)
        self._device_type = normalize_device_type(raw_code)

        # Determine actual SN (e.g. Battery SN for battery sensors)
        bat_sn = self._metrics.get("batSn")

        if description.key in BATTERY_SENSORS and bat_sn:
            self._actual_sn = bat_sn
        else:
            self._actual_sn = sn

        key_lower = description.key.lower()
        self._attr_unique_id = f"hyxi_{self._actual_sn}_{description.key}"
        self._attr_translation_key = description.translation_key or key_lower
        self.entity_id = f"sensor.hyxi_{self._actual_sn}_{key_lower}"

        if key_lower in INT_SENSOR_KEYS:
            self._parser_func = self._parse_int_sensor
        elif parser_name := self._PARSERS.get(key_lower):
            self._parser_func = getattr(self, parser_name)
        else:
            self._parser_func = self._parse_default

        self._update_native_value()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._dev_data = self.coordinator.data.get(self._sn) or {}
        self._metrics = self._dev_data.get("metrics") or {}
        self._update_native_value()
        super()._handle_coordinator_update()

    @property
    def device_info(self):
        """Return dynamic device information to ensure versions update in UI."""
        dev_data = self._dev_data
        metrics = self._metrics
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

        # Determine if we need to apply any state-mapping for specific types
        sw_version = dev_data.get("_sw_version_cached") or get_software_version(
            dev_data
        )
        hw_version = dev_data.get("hw_version")

        info = {
            "identifiers": {(DOMAIN, self._sn)},
            "name": dev_data.get("device_name") or f"Device {self._sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "sw_version": sw_version,
            "hw_version": hw_version,
            "serial_number": self._sn,
        }

        # Handle Parent Collector relationship
        parent_sn = metrics.get("parentSn")
        if parent_sn:
            info["via_device"] = (DOMAIN, parent_sn)

        return info

    # native_value used to be overridden here to adjust acP/genP/gridP by
    # subtracting a raw "acl" metric and applying fixed multipliers
    # (0.96 / 2.0). That adjustment was removed: across every real device
    # dump checked (live hybrid-inverter poll + push payloads covering 168
    # raw keys, plus hyxi-cloud-api's bundled hybrid/micro-inverter example
    # data), HYXI never sends an "acl" field, and no SensorEntityDescription
    # for "genP" exists, so that branch could never even instantiate a
    # sensor. The multipliers had no cited source (vendor doc, issue, or
    # commit rationale) -- they landed in an unrelated lint/tooling PR
    # (#309) -- and were silently under-reporting acP by 4% with nothing
    # behind the correction. If a real per-device "acl"-style
    # self-consumption correction is ever confirmed for some model, add it
    # back with a citation (issue link or vendor doc) rather than a bare
    # constant -- and inherited native_value (SensorEntity's, via
    # HyxiBaseSensor) is enough on its own until then.

    def _parse_device_type(self, dev_data, value):
        return normalize_device_type(get_raw_device_code(dev_data))

    def _parse_int_sensor(self, dev_data, value):
        if is_null_value(value):
            return None
        try:
            return int(round(float(value), 0))
        except ValueError, TypeError, OverflowError:
            return self._process_numeric_value(value)

    def _parse_collect_time(self, dev_data, value):
        if is_null_value(value):
            return None
        try:
            val_int = int(value)
            if val_int > 9999999999:
                val_int = val_int // 1000
            return datetime.fromtimestamp(val_int, tz=UTC)
        except ValueError, TypeError, OSError, OverflowError:
            return None

    def _parse_last_seen(self, dev_data, value):
        if is_null_value(value):
            return None
        return dt_util.parse_datetime(str(value))

    def _parse_app_sw(self, dev_data, value):
        return dev_data.get("sw_version")

    def _parse_sw_ver(self, dev_data, value):
        return value

    def _parse_default(self, dev_data, value):
        if is_null_value(value):
            return None
        return self._process_numeric_value(value)

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        coordinator: HyxiDataUpdateCoordinator = self.coordinator
        return coordinator.hyxi_metadata

    def _update_native_value(self):
        """Update the cached native value."""
        dev_data = self._dev_data
        metrics = self._metrics
        key = self.entity_description.key
        value = metrics.get(key)

        device_type = getattr(self, "_device_type", None)

        # See _SAME_QUANTITY_FALLBACKS for what maps to what, and why.
        fallback = self._SAME_QUANTITY_FALLBACKS.get(key)
        if fallback and device_type in fallback.device_types:
            is_missing = value is None or is_null_value(value)
            if not is_missing and fallback.treat_zero_as_null:
                is_missing = is_zero_value(value)
            if is_missing:
                value = metrics.get(fallback.fallback_key)

        parsed_val = self._parser_func(dev_data, value)
        if (
            parsed_val is not None
            and self.entity_description.device_class == SensorDeviceClass.ENUM
        ):
            str_val = str(parsed_val)
            options = self.entity_description.options
            # Mirrors the truthiness check HA's own SensorEntity.state uses
            # (an empty options list means "no enum validation", same as
            # None) rather than an `is not None` check that would reject
            # every value for that hypothetical case.
            if options and str_val not in options:
                # HA's SensorEntity.state raises if the value isn't in the
                # declared options. That raise is caught per-listener by
                # DataUpdateCoordinator.async_update_listeners() (logged,
                # not re-raised), so it doesn't take other sensors down --
                # but this one silently freezes at its last valid state and
                # logs a full traceback on every refresh for as long as the
                # bad value persists. Report unknown instead.
                self._log_glitch_once(
                    str_val,
                    "HYXI: %s reported enum value %r outside declared "
                    "options %s; reporting as unknown instead of a state "
                    "HA would reject",
                    self.entity_description.key,
                    str_val,
                    options,
                    level=logging.WARNING,
                )
                self._attr_native_value = None
            else:
                # Clear the dedup marker on a valid reading so a bad value
                # that recurs later (even the same one) warns again instead
                # of staying silenced by the first occurrence.
                self._last_logged_glitch = None
                self._attr_native_value = str_val
        else:
            self._attr_native_value = parsed_val


class HyxiLastUpdateSensor(
    CoordinatorEntity["HyxiDataUpdateCoordinator"], SensorEntity
):
    """Diagnostic sensor for the Integration health."""

    _attr_has_entity_name = True
    _attr_translation_key = "integration_last_updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_integration_last_updated"
        modbus = is_modbus_entry(entry)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HYXI Modbus Service" if modbus else "HYXI Cloud Service",
            "manufacturer": MANUFACTURER,
            "model": "Local Modbus Bridge" if modbus else "Cloud API Bridge",
        }
        self._update_native_value()

    def _update_native_value(self):
        """Update the cached native value."""
        self._attr_native_value = self.coordinator.hyxi_metadata.get("last_success")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_native_value()
        super()._handle_coordinator_update()


class HyxiModbusConnectionTypeSensor(
    CoordinatorEntity["HyxiDataUpdateCoordinator"], SensorEntity
):
    """Which physical link and wire framing a Modbus entry actually uses.

    Host/port or serial device alone doesn't say whether a TCP gateway is
    in passthrough or native Modbus-TCP mode -- that's exactly the thing
    setup auto-detects instead of asking (see
    config_flow._probe_and_detect_modbus_tcp). Set once from the entry's
    stored config, not from polled data -- this doesn't change without a
    reconfigure, which recreates every entity anyway.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "modbus_connection_type"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        # Display text lives in the "state" translation for this key, not
        # here -- these are stable machine values an automation can key
        # off, independent of however the shown text is later reworded.
        self._attr_options = ["tcp_rtu", "tcp_socket", "serial"]
        self._attr_unique_id = f"{entry.entry_id}_modbus_connection_type"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HYXI Modbus Service",
            "manufacturer": MANUFACTURER,
            "model": "Local Modbus Bridge",
        }
        if entry.data.get(CONF_MODBUS_TYPE) == MODBUS_TYPE_SERIAL:
            self._attr_native_value = "serial"
        elif entry.data.get(CONF_MODBUS_FRAMER, DEFAULT_MODBUS_FRAMER) == "socket":
            self._attr_native_value = "tcp_socket"
        else:
            self._attr_native_value = "tcp_rtu"


class HyxiSubscriptionStatusSensor(
    CoordinatorEntity["HyxiDataUpdateCoordinator"], SensorEntity
):
    """Diagnostic sensor for real-time push subscription status (data + alarm)."""

    _attr_has_entity_name = True
    _attr_translation_key = "realtime_subscription_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_options = ["active", "inactive", "error", "partial"]
        self._attr_unique_id = f"{entry.entry_id}_realtime_subscription_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HYXI Cloud Service",
            "manufacturer": MANUFACTURER,
            "model": "Cloud API Bridge",
        }
        self._update_value()

    def _update_value(self):
        """Derive a combined status from both subscription channels."""
        data_status = self.coordinator.push_status or "inactive"
        alarm_status = (
            getattr(self.coordinator, "alarm_push_status", "inactive") or "inactive"
        )

        if data_status == "error" or alarm_status == "error":
            combined = "error"
        elif data_status == "active" and alarm_status == "active":
            combined = "active"
        elif data_status == "active" or alarm_status == "active":
            combined = "partial"  # one of two subscriptions active
        else:
            combined = "inactive"

        old = getattr(self, "_attr_native_value", None)
        if combined != old:
            _LOGGER.debug(
                "Subscription status: %s -> %s (data=%s, alarm=%s)",
                old,
                combined,
                data_status,
                alarm_status,
            )
        self._attr_native_value = combined

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return state attributes for both push subscription channels."""
        coord = self.coordinator
        alarm_last = getattr(coord, "alarm_last_push_received", None)
        return {
            # --- Real-time data push ---
            "data_push": {
                "status": coord.push_status or "inactive",
                "subscribe_code": coord.subscribe_code,
                "callback_url": coord.push_url,
                "post_rate": coord.entry.options.get(CONF_PUSH_RATE),
                "last_push_received": coord.last_push_received.isoformat()
                if coord.last_push_received
                else None,
                "error": coord.push_error,
            },
            # --- Alarm push ---
            "alarm_push": {
                "status": getattr(coord, "alarm_push_status", "inactive") or "inactive",
                "subscribe_code": getattr(coord, "alarm_subscribe_code", None),
                "callback_url": getattr(coord, "alarm_push_url", None),
                "last_push_received": alarm_last.isoformat() if alarm_last else None,
                "error": getattr(coord, "alarm_push_error", None),
            },
            "known_subscription_codes": getattr(coord, "known_subscription_codes", []),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_value()
        super()._handle_coordinator_update()


class HyxiMicroinverterSumSensor(
    CoordinatorEntity["HyxiDataUpdateCoordinator"], SensorEntity
):
    """Aggregate a single metric (AC power, daily yield, etc.) across all microinverters."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        entry,
        metric_key: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the aggregate sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._metric_key = metric_key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_microinverters_summary")},
            "name": "Microinverters Summary",
            "manufacturer": MANUFACTURER,
            "model": "Aggregated Microinverter Metrics",
        }
        self._logged_no_data = False
        self._update_native_value()

    def _update_native_value(self) -> None:
        """Recompute the sum of the tracked metric across all microinverter devices."""
        debug_enabled = _LOGGER.isEnabledFor(logging.DEBUG)
        total = 0.0
        found_any = False
        raw_values: dict[str, Any] = {}
        for sn, dev_data in self.coordinator.data.items():
            if normalize_device_type(get_raw_device_code(dev_data)) != "micro_inverter":
                continue
            value = (dev_data.get("metrics") or {}).get(self._metric_key)
            if debug_enabled:
                raw_values[mask_sn(sn)] = value
            if value is None or is_null_value(value):
                continue
            try:
                total += float(value)
                found_any = True
            except ValueError, TypeError:
                continue
        self._attr_native_value = round(total, 2) if found_any else None
        self._log_no_usable_value(found_any, raw_values, debug_enabled)

    def _log_no_usable_value(
        self, found_any: bool, raw_values: dict[str, Any], debug_enabled: bool
    ) -> None:
        """Debug-log once (not every poll) when no device yielded a usable value.

        The dedup latch is only ever set inside the `debug_enabled` branch,
        right after actually logging. If DEBUG is off, we skip without
        touching the latch, so turning DEBUG on later while still stuck
        logs immediately instead of finding the latch already tripped from
        a period when nothing could have been logged.
        """
        if found_any:
            self._logged_no_data = False
            return
        if not debug_enabled:
            return
        if not self._logged_no_data:
            _LOGGER.debug(
                "%s: no usable '%s' value across %d micro_inverter device(s); "
                "raw values were: %s",
                self.entity_description.key,
                self._metric_key,
                len(raw_values),
                raw_values,
            )
            self._logged_no_data = True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_native_value()
        super()._handle_coordinator_update()


class HyxiLastSentModeSensor(
    CoordinatorEntity["HyxiDataUpdateCoordinator"], SensorEntity, RestoreEntity
):
    """Sensor exposing the last tracked mode command for a protected inverter."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_sent_mode"

    def __init__(self, coordinator, sn: str) -> None:
        """Initialize the last sent mode sensor."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_last_sent_mode"

    async def async_added_to_hass(self) -> None:
        """Restore the last tracked mode and replay it after restart."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return

        mode = last_state.state
        if mode in ("unknown", "unavailable", ""):
            return

        if controller := _get_protection_controller(self.coordinator, self._sn):
            controller.restore_last_sent_mode(mode)

    @property
    def device_info(self):
        """Return the linked inverter device info."""
        dev_data = self.coordinator.data.get(self._sn) or {}
        return {
            "identifiers": {(DOMAIN, self._sn)},
            "name": dev_data.get("device_name") or f"Device {self._sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": self._sn,
        }

    @property
    def native_value(self) -> str | None:
        """Return the last tracked mode command."""
        controller = _get_protection_controller(self.coordinator, self._sn)
        if controller is None:
            return None
        return controller.last_sent_mode


def _get_protection_controller(coordinator, sn: str):
    """Return the battery protection controller for a device."""
    return getattr(coordinator, "protection_controllers", {}).get(sn)


@dataclass
class EMSensorDef:
    """Definition for an EM sensor entity."""

    key: str
    device_info: DeviceInfo
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    icon: str | None = None


class EMSensor(SensorEntity):
    """Sensor entity backed by the Energy Manager engine.

    Updates are pushed by the engine via a registered callback, not by the
    coordinator poll cycle.
    """

    _attr_has_entity_name = True

    _VALUE_GETTERS: ClassVar[dict[str, str]] = {
        "current_decision": "decision",
        "last_action": "last_action",
        "status": "status",
        "battery_energy_available": "battery_energy_available_wh",
        "hours_until_sunrise": "_hours_until_sunrise",
        "hours_until_sunset": "_hours_until_sunset",
        "p1_average": "p1_avg",
    }

    def __init__(
        self,
        coordinator,
        sn: str,
        sensor_def: EMSensorDef,
    ) -> None:
        """Initialize the EM sensor."""
        self._coordinator = coordinator
        self._sn = sn
        self._key = sensor_def.key
        self._attr_unique_id = f"hyxi_{sn}_em_{sensor_def.key}"
        self._attr_translation_key = f"em_{sensor_def.key}"
        self._attr_device_info = sensor_def.device_info
        self._attr_native_unit_of_measurement = sensor_def.unit
        self._attr_device_class = sensor_def.device_class
        self._attr_state_class = sensor_def.state_class
        if sensor_def.icon:
            self._attr_icon = sensor_def.icon

    async def async_added_to_hass(self) -> None:
        """Register for engine updates."""
        engine = self._coordinator.engine
        if engine:
            engine.register_update_callback(self._engine_updated)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from engine updates."""
        engine = self._coordinator.engine
        if engine:
            engine.unregister_update_callback(self._engine_updated)

    @callback
    def _engine_updated(self) -> None:
        """Handle engine state change."""
        self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the current value from the engine."""
        engine = self._coordinator.engine
        if not engine:
            return None
        getter_name = self._VALUE_GETTERS.get(self._key)
        if not getter_name:
            return None
        attr = getattr(engine, getter_name, None)
        if callable(attr):
            value = attr()
        else:
            value = attr
        if isinstance(value, float):
            return round(value, 1)
        return value

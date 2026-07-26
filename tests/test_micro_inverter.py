"""Tests for MICRO_INVERTER specific logic and sensors."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest


# 1. THE BULLETPROOF MOCK (Copied from test_sensor_logic.py strategy)
class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    # Allow CoordinatorEntity[HyxiDataUpdateCoordinator] subscripting in class bases
    __class_getitem__ = classmethod(lambda cls, item: cls)

    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator

    def _handle_coordinator_update(self) -> None:
        pass


class FakeSensorEntity(FakeBase):
    @property
    def native_value(self):
        return getattr(self, "_attr_native_value", None)


class FakeRestoreEntity(FakeBase):
    async def async_added_to_hass(self):
        pass


# Mock homeassistant environment BEFORE importing integration code
mock_ha = sys.modules.get("homeassistant")
if mock_ha is None:
    mock_ha = MagicMock()
    mock_ha.callback = lambda func: func
    sys.modules["homeassistant"] = mock_ha

if "homeassistant.components" not in sys.modules:
    sys.modules["homeassistant.components"] = MagicMock()
if "homeassistant.core" not in sys.modules:
    sys.modules["homeassistant.core"] = mock_ha
if "homeassistant.const" not in sys.modules:
    sys.modules["homeassistant.const"] = mock_ha
if "homeassistant.util" not in sys.modules:
    sys.modules["homeassistant.util"] = mock_ha

if "homeassistant.components.sensor" not in sys.modules:
    sys.modules["homeassistant.components.sensor"] = MagicMock()
sensor_mock: Any = sys.modules["homeassistant.components.sensor"]
sensor_mock.SensorEntity = FakeSensorEntity

if "homeassistant.helpers.update_coordinator" not in sys.modules:
    sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
coord_mock: Any = sys.modules["homeassistant.helpers.update_coordinator"]
coord_mock.CoordinatorEntity = FakeCoordinatorEntity

if "homeassistant.helpers.restore_state" not in sys.modules:
    sys.modules["homeassistant.helpers.restore_state"] = MagicMock()
restore_mock: Any = sys.modules["homeassistant.helpers.restore_state"]
restore_mock.RestoreEntity = FakeRestoreEntity


# Now import the modules
import custom_components.hyxi_cloud.const as const_mod
import custom_components.hyxi_cloud.sensor as sensor_mod

# Wire up real const functions
sensor_mod.normalize_device_type = const_mod.normalize_device_type
sensor_mod.get_raw_device_code = const_mod.get_raw_device_code


@pytest.fixture
def micro_inverter_coordinator():
    """Fixture for a coordinator with MICRO_INVERTER data."""
    coordinator = MagicMock()
    # Data provided by the user in the request
    coordinator.data = {
        "SN_MICRO": {
            "device_type_code": "MICRO_INVERTER",
            "model": "HYX-M2000-SW",
            "device_name": "Test Micro",
            "metrics": {
                "collectTime": 1775767350,
                "temp": 34.2,
                "acE": 0.0,
                "ph1p": 18.0,
                "ph1i": 0.08,
                "ph1v": 212.6,
                "totalE": 499.35,
                "f": 59.95,
                "efpv": 4.51,
                "pv1v": 41.0,
                "pv1i": 0.12,
                "pv2v": 37.8,
                "pv2i": 0.16,
                "pv3v": 40.6,
                "pv3i": 0.08,
                "pv4v": 38.8,
                "pv4i": 0.14,
                "acP": 18.0,
                "eToday": 4.51,
                "ppv": 20.1,
                "deviceState": 1,
            },
        }
    }
    return coordinator


@pytest.fixture
def multi_micro_inverter_coordinator():
    """Fixture with multiple MICRO_INVERTER devices plus a non-microinverter device."""
    coordinator = MagicMock()
    coordinator.data = {
        "SN_MICRO_1": {
            "device_type_code": "MICRO_INVERTER",
            "metrics": {"acP": 18.0, "eToday": 4.51},
        },
        "SN_MICRO_2": {
            "device_type_code": "MICRO_INVERTER",
            "metrics": {"acP": 22.5, "eToday": 3.29},
        },
        # Null/placeholder metric values must be skipped, not treated as 0
        "SN_MICRO_3": {
            "device_type_code": "MICRO_INVERTER",
            "metrics": {"acP": "--", "eToday": None},
        },
        # A hybrid inverter in the same plant must not be counted in the sum
        "SN_HYBRID": {
            "device_type_code": "HYBRID_INVERTER",
            "metrics": {"acP": 5000.0, "eToday": 12.0},
        },
    }
    return coordinator


def test_microinverter_sum_sensor_aggregates_across_devices(
    multi_micro_inverter_coordinator,
):
    """Verify the aggregate sensor sums the metric across microinverters only,
    skipping non-microinverter devices and null/placeholder readings."""
    entry = MagicMock()
    entry.entry_id = "entry123"

    description = MagicMock()
    description.key = "micro_ac_power_total"

    sensor = sensor_mod.HyxiMicroinverterSumSensor(
        multi_micro_inverter_coordinator, entry, "acP", description
    )

    assert sensor.native_value == 40.5  # 18.0 + 22.5, SN_MICRO_3/SN_HYBRID excluded


def test_microinverter_sum_sensor_daily_yield(multi_micro_inverter_coordinator):
    """Verify aggregation also works for the daily-yield metric key."""
    entry = MagicMock()
    entry.entry_id = "entry123"

    description = MagicMock()
    description.key = "micro_daily_yield_total"

    sensor = sensor_mod.HyxiMicroinverterSumSensor(
        multi_micro_inverter_coordinator, entry, "eToday", description
    )

    assert sensor.native_value == 7.8  # 4.51 + 3.29, rounded


def test_microinverter_sum_sensor_no_microinverters_is_none():
    """If no microinverter devices are present, native_value must be None,
    not 0 -- 0 would misleadingly imply a real (idle) reading."""
    coordinator = MagicMock()
    coordinator.data = {
        "SN_HYBRID": {
            "device_type_code": "HYBRID_INVERTER",
            "metrics": {"acP": 5000.0},
        }
    }
    entry = MagicMock()
    entry.entry_id = "entry123"

    description = MagicMock()
    description.key = "micro_ac_power_total"

    sensor = sensor_mod.HyxiMicroinverterSumSensor(
        coordinator, entry, "acP", description
    )

    assert sensor.native_value is None


def test_microinverter_sum_sensor_updates_on_coordinator_refresh(
    multi_micro_inverter_coordinator,
):
    """Verify the aggregate is recomputed when the coordinator pushes new data."""
    entry = MagicMock()
    entry.entry_id = "entry123"

    description = MagicMock()
    description.key = "micro_ac_power_total"

    sensor = sensor_mod.HyxiMicroinverterSumSensor(
        multi_micro_inverter_coordinator, entry, "acP", description
    )
    assert sensor.native_value == 40.5

    multi_micro_inverter_coordinator.data["SN_MICRO_1"]["metrics"]["acP"] = 100.0
    sensor._handle_coordinator_update()

    assert sensor.native_value == 122.5  # 100.0 + 22.5


def test_ace_fallback_to_efpv(micro_inverter_coordinator):
    """Verify that acE falls back to efpv for MICRO_INVERTER when acE is 0.0."""
    description = MagicMock()
    description.key = "acE"
    description.translation_key = "ace"
    description.native_unit_of_measurement = "kWh"
    description.state_class = "total_increasing"

    sensor = sensor_mod.HyxiSensor(micro_inverter_coordinator, "SN_MICRO", description)

    # Standard acE is 0.0 in metrics, but sensor should report 4.51 (efpv)
    assert sensor.native_value == 4.51


def test_new_micro_inverter_sensors(micro_inverter_coordinator):
    """Verify that new sensors (temp, efpv, pv3, pv4) are correctly parsed."""

    # Test 'temp' -> Inverter Temperature
    desc_temp = MagicMock()
    desc_temp.key = "temp"
    desc_temp.translation_key = "inverter_temperature"
    desc_temp.native_unit_of_measurement = "°C"

    sensor_temp = sensor_mod.HyxiSensor(
        micro_inverter_coordinator, "SN_MICRO", desc_temp
    )
    assert sensor_temp.native_value == 34.2

    # Test 'efpv'
    desc_efpv = MagicMock()
    desc_efpv.key = "efpv"
    desc_efpv.translation_key = "efpv"
    desc_efpv.native_unit_of_measurement = "kWh"
    desc_efpv.state_class = "total_increasing"

    sensor_efpv = sensor_mod.HyxiSensor(
        micro_inverter_coordinator, "SN_MICRO", desc_efpv
    )
    assert sensor_efpv.native_value == 4.51

    # Test PV3 Voltage
    desc_pv3v = MagicMock()
    desc_pv3v.key = "pv3v"
    desc_pv3v.native_unit_of_measurement = "V"

    sensor_pv3v = sensor_mod.HyxiSensor(
        micro_inverter_coordinator, "SN_MICRO", desc_pv3v
    )
    assert sensor_pv3v.native_value == 40.6

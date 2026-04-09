"""Tests for MICRO_INVERTER specific logic and sensors."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel
import sys
from unittest.mock import MagicMock

import pytest


# 1. THE BULLETPROOF MOCK (Copied from test_sensor_logic.py strategy)
class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


class FakeSensorEntity(FakeBase):
    @property
    def native_value(self):
        return getattr(self, "_attr_native_value", None)


class FakeRestoreEntity(FakeBase):
    async def async_added_to_hass(self):
        pass


# Mock homeassistant environment BEFORE importing integration code
mock_ha = MagicMock()
mock_ha.callback = lambda func: func
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha
sys.modules["homeassistant.util"] = mock_ha

mock_sensor = MagicMock()
mock_sensor.SensorEntity = FakeSensorEntity
sys.modules["homeassistant.components.sensor"] = mock_sensor

mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator

mock_restore = MagicMock()
mock_restore.RestoreEntity = FakeRestoreEntity
sys.modules["homeassistant.helpers.restore_state"] = mock_restore

# Now import the modules
import custom_components.hyxi_cloud.const as const_mod  # noqa: E402
import custom_components.hyxi_cloud.sensor as sensor_mod  # noqa: E402

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
                "ppv": 20.1,
                "deviceState": 1,
            },
        }
    }
    return coordinator


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

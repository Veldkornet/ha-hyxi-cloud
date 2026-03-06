import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest


# 1. THE BULLETPROOF MOCK
class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


class FakeSensorEntity(FakeBase):
    pass


mock_ha = MagicMock()
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
mock_sensor = MagicMock()
mock_sensor.SensorEntity = FakeSensorEntity
mock_sensor.SensorStateClass = MagicMock()
mock_sensor.SensorDeviceClass = MagicMock()
sys.modules["homeassistant.components.sensor"] = mock_sensor
mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator
sys.modules["homeassistant.util"] = mock_ha

from custom_components.hyxi_cloud.sensor import HyxiSensor  # noqa: E402


@pytest.fixture
def base_sensor():
    """Fixture to create a standard energy sensor for testing."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"totalE": 2742.0}}}
    description = MagicMock()
    description.key = "totalE"
    description.native_unit_of_measurement = "kWh"
    description.state_class = "total_increasing"

    sensor = HyxiSensor(coordinator, "SN123", description)
    sensor.hass = None
    return sensor, coordinator


def test_anti_dip_recovery(base_sensor):
    """Verify the exact scenario in your graph: 2742 -> 2738 -> 2747."""
    sensor, coordinator = base_sensor

    # Baseline
    assert sensor.native_value == 2742.0

    # 📉 The Dip (Should be blocked)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2738.0
    assert sensor.native_value == 2742.0

    # 📈 The Recovery (Should be allowed as it's a valid increase from baseline)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2747.0
    assert sensor.native_value == 2747.0


def test_null_data_handling(base_sensor):
    """Ensure the sensor returns None instead of crashing on empty API data."""
    sensor, coordinator = base_sensor
    coordinator.data["SN123"]["metrics"]["totalE"] = None
    assert sensor.native_value is None

    coordinator.data["SN123"]["metrics"]["totalE"] = ""
    assert sensor.native_value is None


def test_timestamp_scaling(base_sensor):
    """Verify 10-digit (sec) and 13-digit (ms) timestamps both work."""
    sensor, _ = base_sensor
    sensor.entity_description.key = "collectTime"
    sensor.entity_description.native_unit_of_measurement = (
        None  # Timestamps don't have units
    )

    # 10 Digits
    sensor.coordinator.data["SN123"]["metrics"]["collectTime"] = 1741248000
    assert isinstance(sensor.native_value, datetime)

    # 13 Digits
    sensor.coordinator.data["SN123"]["metrics"]["collectTime"] = 1741248000000
    assert isinstance(sensor.native_value, datetime)


def test_rounding_protection(base_sensor):
    """Ensure floating point noise (2.73199999) is rounded correctly."""
    sensor, coordinator = base_sensor
    coordinator.data["SN123"]["metrics"]["totalE"] = 2742.123456
    assert sensor.native_value == 2742.12


def test_late_night_correction(base_sensor):
    """Verify that a jump after a long flat period (night) is accepted."""
    sensor, coordinator = base_sensor

    # 10:00 PM - Value is 2742.0
    coordinator.data["SN123"]["metrics"]["totalE"] = 2742.0
    assert sensor.native_value == 2742.0

    # 02:00 AM - Cloud 'finds' 1.5kWh missed from earlier in the day
    # Even though it's night, this is a valid increase < 100kWh.
    coordinator.data["SN123"]["metrics"]["totalE"] = 2743.5
    val = sensor.native_value

    print(f"[Night Correction] Jumped from 2742.0 to {val} kWh")
    assert val == 2743.5  # Should be ALLOWED

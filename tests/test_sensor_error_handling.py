import sys
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
sys.modules["homeassistant.components.sensor"] = mock_ha
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.update_coordinator"] = mock_ha
sys.modules["homeassistant.util"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha

# Mock hyxi_cloud_api
sys.modules["hyxi_cloud_api"] = MagicMock()

mock_sensor = MagicMock()
mock_sensor.SensorEntity = FakeSensorEntity
mock_sensor.SensorStateClass = MagicMock()
mock_sensor.SensorDeviceClass = MagicMock()
sys.modules["homeassistant.components.sensor"] = mock_sensor

mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator

from custom_components.hyxi_cloud.sensor import HyxiSensor  # noqa: E402


@pytest.fixture
def error_sensor():
    """Fixture to create a battery sensor for testing error handling."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"batSoc": "100"}}}
    description = MagicMock()
    description.key = "batSoc"
    description.native_unit_of_measurement = "%"
    description.state_class = "measurement"

    sensor = HyxiSensor(coordinator, "SN123", description)
    sensor.hass = None
    return sensor, coordinator


def test_sensor_int_conversion_error(error_sensor):
    """Test that invalid numeric strings or objects return None for batSoc, batSoh, signalVal."""
    sensor, coordinator = error_sensor

    # Test keys: batsoc, batsoh, signalval (case insensitive in sensor.py)
    for key in ["batSoc", "batSoh", "signalVal"]:
        sensor.entity_description.key = key

        # Test valid string
        coordinator.data["SN123"]["metrics"][key] = "85.5"
        assert sensor.native_value == 86

        # Test invalid string
        coordinator.data["SN123"]["metrics"][key] = "invalid_string"
        assert sensor.native_value is None

        # Test non-numeric object
        coordinator.data["SN123"]["metrics"][key] = {"unexpected": "data"}
        assert sensor.native_value is None

        # Test None value (handled by earlier check but good to verify)
        coordinator.data["SN123"]["metrics"][key] = None
        assert sensor.native_value is None

        # Test empty string (handled by earlier check)
        coordinator.data["SN123"]["metrics"][key] = ""
        assert sensor.native_value is None

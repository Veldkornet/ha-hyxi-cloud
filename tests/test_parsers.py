"""Tests for Hyxi Cloud sensor parsers."""

import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from custom_components.hyxi_cloud.sensor import HyxiSensor

# 1. THE BULLETPROOF MOCK (similar to test_sensor_logic.py)
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


# Mock Home Assistant environment
mock_ha = MagicMock()
mock_ha.__path__ = []
mock_ha.callback = lambda func: func
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha

# Mock sensor component
mock_sensor = MagicMock()
mock_sensor.SensorEntity = FakeSensorEntity
sys.modules["homeassistant.components.sensor"] = mock_sensor

# Mock helpers
mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity
mock_restore = MagicMock()
mock_restore.RestoreEntity = FakeRestoreEntity

sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.restore_state"] = mock_restore
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator
sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
sys.modules["homeassistant.util"] = mock_ha
sys.modules["aiohttp"] = MagicMock()

# Mock hyxi_cloud_api
mock_api = MagicMock()
mock_api.__version__ = "1.0.4"
sys.modules["hyxi_cloud_api"] = mock_api


@pytest.fixture
def mock_sensor():
    """Create a mock HyxiSensor instance for testing parsers."""
    coordinator = MagicMock()
    description = MagicMock()
    description.key = "test_sensor"
    description.translation_key = "test_sensor"
    description.state_class = "measurement"
    description.native_unit_of_measurement = "units"

    with patch(
        "custom_components.hyxi_cloud.sensor.HyxiSensor.__init__", return_value=None
    ):
        sensor = HyxiSensor(coordinator, "SN123", description)
        sensor.coordinator = coordinator
        sensor.entity_description = description
        sensor._actual_sn = "SN123"
        return sensor


def test_parse_int_sensor_valid(mock_sensor):
    """Test _parse_int_sensor with valid numeric inputs."""
    # Integer as string
    assert mock_sensor._parse_int_sensor({}, "100") == 100
    # Float as string (should round)
    assert mock_sensor._parse_int_sensor({}, "85.6") == 86
    assert mock_sensor._parse_int_sensor({}, "85.4") == 85
    # Actual float
    assert mock_sensor._parse_int_sensor({}, 42.7) == 43
    # Actual int
    assert mock_sensor._parse_int_sensor({}, 10) == 10


def test_parse_int_sensor_null_equivalents(mock_sensor):
    """Test _parse_int_sensor with various null-equivalent values."""
    null_values = [None, "", "null", "none", "na", "--", "  NULL  ", "None"]
    for val in null_values:
        assert mock_sensor._parse_int_sensor({}, val) is None, f"Failed for {val}"


def test_parse_int_sensor_error_fallback(mock_sensor):
    """Test _parse_int_sensor fallback to _process_numeric_value on error."""
    # Invalid string
    # _process_numeric_value for non-total_increasing sensor returns the value as is if float() fails
    assert mock_sensor._parse_int_sensor({}, "invalid") == "invalid"

    # Invalid type
    obj = {"data": 123}
    assert mock_sensor._parse_int_sensor({}, obj) == obj


def test_parse_collect_time_valid(mock_sensor):
    """Test _parse_collect_time with valid timestamps."""
    # 10-digit timestamp (seconds)
    ts_sec = 1741248000
    expected_dt = datetime.fromtimestamp(ts_sec, tz=UTC)
    assert mock_sensor._parse_collect_time({}, ts_sec) == expected_dt
    assert mock_sensor._parse_collect_time({}, str(ts_sec)) == expected_dt

    # 13-digit timestamp (milliseconds)
    ts_ms = 1741248000000
    assert mock_sensor._parse_collect_time({}, ts_ms) == expected_dt
    assert mock_sensor._parse_collect_time({}, str(ts_ms)) == expected_dt


def test_parse_collect_time_null_equivalents(mock_sensor):
    """Test _parse_collect_time with various null-equivalent values."""
    null_values = [None, "", "null", "none", "na", "--", "  NULL  ", "None"]
    for val in null_values:
        assert mock_sensor._parse_collect_time({}, val) is None, f"Failed for {val}"


def test_parse_collect_time_errors(mock_sensor):
    """Test _parse_collect_time error handling."""
    # Invalid string
    assert mock_sensor._parse_collect_time({}, "not_a_timestamp") is None

    # Invalid type
    assert mock_sensor._parse_collect_time({}, {"time": 123}) is None

    # Overflow value
    assert mock_sensor._parse_collect_time({}, 10**25) is None

    # Extreme value that might pass the 10-digit check but still fail fromtimestamp
    with patch("custom_components.hyxi_cloud.sensor.datetime") as mock_dt:
        mock_dt.fromtimestamp.side_effect = OverflowError()
        assert mock_sensor._parse_collect_time({}, 1234567890) is None

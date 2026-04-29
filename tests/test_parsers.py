"""Tests for Hyxi Cloud sensor parsers."""

import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


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
mock_sensor_comp = MagicMock()
mock_sensor_comp.SensorEntity = FakeSensorEntity
sys.modules["homeassistant.components.sensor"] = mock_sensor_comp

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
sys.modules["homeassistant.util.dt"] = mock_ha
sys.modules["aiohttp"] = MagicMock()

# Mock hyxi_cloud_api
mock_api = MagicMock()
mock_api.__version__ = "1.0.4"
sys.modules["hyxi_cloud_api"] = mock_api


@pytest.fixture
def mock_sensor():
    """Create a mock HyxiSensor instance for testing parsers."""
    from custom_components.hyxi_cloud.sensor import HyxiSensor

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


def test_parse_device_type_valid(mock_sensor):
    """Test _parse_device_type with valid keys and codes."""
    # device_type_code
    assert (
        mock_sensor._parse_device_type({"device_type_code": "1"}, "any")
        == "hybrid_inverter"
    )
    assert (
        mock_sensor._parse_device_type({"device_type_code": "2"}, "any")
        == "grid_connected_inverter"
    )
    assert (
        mock_sensor._parse_device_type({"device_type_code": "3"}, "any") == "collector"
    )
    assert (
        mock_sensor._parse_device_type({"device_type_code": "15"}, "any") == "micro_ess"
    )

    # deviceType
    assert (
        mock_sensor._parse_device_type({"deviceType": "106"}, "any")
        == "hybrid_inverter"
    )

    # devType
    assert mock_sensor._parse_device_type({"devType": "607"}, "any") == "collector"

    # deviceCode
    assert (
        mock_sensor._parse_device_type({"deviceCode": "HYBRID_INVERTER"}, "any")
        == "hybrid_inverter"
    )

    # Check precedence (device_type_code > deviceType > devType > deviceCode)
    assert (
        mock_sensor._parse_device_type(
            {
                "device_type_code": "2",
                "deviceType": "1",
                "devType": "3",
                "deviceCode": "15",
            },
            "any",
        )
        == "grid_connected_inverter"
    )


def test_parse_device_type_unknown(mock_sensor):
    """Test _parse_device_type with unknown or missing data."""
    # Invalid code
    assert (
        mock_sensor._parse_device_type({"device_type_code": "999"}, "any") == "unknown"
    )

    # Missing keys
    assert mock_sensor._parse_device_type({"some_other_key": "1"}, "any") == "unknown"

    # Empty dict
    assert mock_sensor._parse_device_type({}, "any") == "unknown"

    # Value is ignored
    assert (
        mock_sensor._parse_device_type({"device_type_code": "1"}, None)
        == "hybrid_inverter"
    )
    assert (
        mock_sensor._parse_device_type({"device_type_code": "1"}, "hybrid_inverter")
        == "hybrid_inverter"
    )

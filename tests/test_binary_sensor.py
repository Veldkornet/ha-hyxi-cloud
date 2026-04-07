"""Tests for the binary sensor platform."""

# pylint: disable=redefined-outer-name,import-outside-toplevel,unused-import,wrong-import-order,wrong-import-position
import importlib
import sys
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import MagicMock

import pytest


# 1. SETUP BULLETPROOF MOCKS
class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, **kwargs):
        self.coordinator = coordinator
        self._attr_extra_state_attributes = {}

    def _handle_coordinator_update(self) -> None:
        pass


class FakeBinarySensorEntity(FakeBase):
    pass


mock_ha = MagicMock()
mock_ha.__path__ = []
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
mock_ha.CoordinatorEntity = FakeCoordinatorEntity
mock_ha.BinarySensorEntity = FakeBinarySensorEntity
sys.modules["homeassistant.components.binary_sensor"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.update_coordinator"] = mock_ha

mock_util = MagicMock()
mock_util.__spec__ = None
sys.modules["homeassistant.util"] = mock_util

# We need a real-ish dt_util for parsing to work in the component
mock_dt = MagicMock()
mock_dt.__spec__ = None
sys.modules["homeassistant.util.dt"] = mock_dt
import homeassistant.util.dt as dt_util  # noqa: E402

mock_dt = MagicMock()
mock_dt.UTC = UTC
mock_dt.parse_datetime = dt_util.parse_datetime
# Fixed return value for utcnow to be consistent
mock_dt.utcnow.return_value = datetime(2026, 3, 11, 12, 0, 0, tzinfo=UTC)
sys.modules["homeassistant.util.dt"] = mock_dt
mock_ha.util.dt = mock_dt  # Ensure both paths work

# Now import and reload the component to ensure it uses the mock
import custom_components.hyxi_cloud.binary_sensor as bs_mod  # noqa: E402

importlib.reload(bs_mod)

from custom_components.hyxi_cloud.const import DOMAIN  # noqa: E402


@pytest.fixture
def mock_coordinator():
    coord = MagicMock()
    coord.on_unload = MagicMock()
    coord.last_update_success = True
    coord.last_exception = None
    coord.data = {"SN123": {"device_name": "Test Device", "alarms": []}}
    fixed_now = datetime(
        2026,
        3,
        11,
        12,
        0,
        0,
        tzinfo=UTC,
    )
    coord.hyxi_metadata = {
        "last_attempts": 1,
        "last_success": fixed_now,  # datetime object, not ISO string
        "last_error": None,
    }
    return coord


@pytest.fixture
def mock_entry():
    entry = MagicMock()
    entry.entry_id = "test_entry"
    return entry


@pytest.mark.asyncio
async def test_async_setup_entry(mock_coordinator, mock_entry):
    """Test setting up binary sensors."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry.entry_id: mock_coordinator}}
    async_add_entities = MagicMock()

    await bs_mod.async_setup_entry(hass, mock_entry, async_add_entities)

    assert async_add_entities.called
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 2
    assert isinstance(entities[0], bs_mod.HyxiConnectivitySensor)
    assert isinstance(entities[1], bs_mod.HyxiDeviceAlarmSensor)


def test_connectivity_sensor_diagnostics(mock_coordinator, mock_entry):
    """Test connectivity sensor error and availability attributes."""
    sensor = bs_mod.HyxiConnectivitySensor(mock_coordinator, mock_entry)

    # 1. Test success state
    attrs = sensor.extra_state_attributes
    # last_successful_connection should be a formatted datetime string from the mock
    assert attrs["last_successful_connection"] is not None
    assert isinstance(attrs["last_successful_connection"], str)
    assert attrs["last_error"] == "None"
    assert "last_update" not in attrs  # Removed duplicate key
    assert "last_exception" not in attrs  # Should be gone now

    # 2. Test error persistence
    mock_coordinator.hyxi_metadata["last_error"] = "Failed to pulse"
    attrs = sensor.extra_state_attributes
    assert attrs["last_error"] == "Failed to pulse"

    # Connection Quality
    mock_coordinator.hyxi_metadata["last_attempts"] = 1
    attrs = sensor.extra_state_attributes
    assert attrs["connection_quality"] == "Stable"

    assert sensor.available is True


def test_device_alarm_sensor(mock_coordinator, mock_entry):
    """Test device alarm sensor logic."""
    mock_coordinator.data["SN123"]["alarms"] = [
        {"alarmState": "1"},
        {"alarmState": 0},
    ]

    sensor = bs_mod.HyxiDeviceAlarmSensor(mock_coordinator, mock_entry, "SN123")

    assert sensor.is_on is True
    assert sensor.extra_state_attributes["active_alarms_count"] == 2

    # Test update via coordinator handle
    mock_coordinator.data["SN123"]["alarms"] = []
    sensor._handle_coordinator_update()
    assert sensor.is_on is False


@pytest.mark.parametrize(
    "last_success_offset,expected_label",
    [
        (15, "Current (Just now)"),  # < 1m
        (180, "Fresh (3m ago)"),  # 3m ago
        (600, "Stale (10m ago)"),  # 10m ago
    ],
)
def test_connectivity_sensor_freshness_labels(
    mock_coordinator, mock_entry, last_success_offset, expected_label
):
    """Test data freshness labels in different scenarios."""
    sensor = bs_mod.HyxiConnectivitySensor(mock_coordinator, mock_entry)
    now_val = datetime(2026, 3, 11, 12, 0, 0, tzinfo=UTC)

    # Directly override the attributes on the mock object the component is using

    bs_mod.dt_util.utcnow = lambda: now_val
    # We use a simple lambda with a fallback to handle ISO parsing without Z support if needed
    bs_mod.dt_util.parse_datetime = lambda s: (
        datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    )

    mock_coordinator.hyxi_metadata["last_success"] = (
        now_val - timedelta(seconds=last_success_offset)
    ).isoformat()
    assert sensor.extra_state_attributes["data_freshness"] == expected_label

    # 4. Unknown (No data)
    mock_coordinator.hyxi_metadata["last_success"] = None
    assert sensor.extra_state_attributes["data_freshness"] == "Unknown"


def test_hyxi_alarm_sensor_missing_metric(mock_coordinator):
    """Test what happens to extra_state_attributes when metrics does not contain deviceState."""
    from unittest.mock import PropertyMock
    from unittest.mock import patch

    if not hasattr(bs_mod, "HyxiAlarmSensor"):
        pytest.skip("HyxiAlarmSensor not available in this test environment")

    mock_coordinator.data = {"SN123": {"metrics": {"other_key": "123"}}}

    sensor = bs_mod.HyxiAlarmSensor(mock_coordinator, "SN123")  # pylint: disable=no-member

    with patch.object(type(sensor), "is_on", new_callable=PropertyMock) as mock_is_on:
        mock_is_on.return_value = False
        attrs = sensor.extra_state_attributes

        assert attrs["status_code"] == "Unknown"
        assert attrs["status_message"] == "Alarm"


def test_connectivity_sensor_quality_labels(mock_coordinator, mock_entry):
    """Test connection quality labels."""
    sensor = bs_mod.HyxiConnectivitySensor(mock_coordinator, mock_entry)

    # 1. Offline (is_on is False)
    mock_coordinator.last_update_success = False
    assert sensor.extra_state_attributes["connection_quality"] == "Offline"

    # 2. Degraded (> 1 retry)
    mock_coordinator.last_update_success = True
    mock_coordinator.hyxi_metadata["last_attempts"] = 3
    assert sensor.extra_state_attributes["connection_quality"] == "Degraded (3 retries)"

    # 3. Stable (1 retry)
    mock_coordinator.hyxi_metadata["last_attempts"] = 1
    assert sensor.extra_state_attributes["connection_quality"] == "Stable"

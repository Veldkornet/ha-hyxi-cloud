"""Tests for the binary sensor platform."""

import sys
from unittest.mock import MagicMock
import pytest
from datetime import timedelta, datetime, timezone

# 1. SETUP BULLETPROOF MOCKS
class FakeBase: pass
class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, **kwargs):
        self.coordinator = coordinator
        self._attr_extra_state_attributes = {}
    def _handle_coordinator_update(self) -> None: pass

class FakeBinarySensorEntity(FakeBase): pass

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
sys.modules["homeassistant.util"] = mock_ha

# We need a real-ish dt_util for parsing to work in the component
import homeassistant.util.dt as dt_util

mock_dt = MagicMock()
mock_dt.UTC = timezone.utc
mock_dt.parse_datetime = dt_util.parse_datetime
mock_dt.utcnow = MagicMock(return_value=datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc))
sys.modules["homeassistant.util.dt"] = mock_dt

# Now import the component
from custom_components.hyxi_cloud.binary_sensor import (
    async_setup_entry,
    HyxiConnectivitySensor,
    HyxiDeviceAlarmSensor,
)
from custom_components.hyxi_cloud.const import DOMAIN

@pytest.fixture
def mock_coordinator():
    coord = MagicMock()
    coord.on_unload = MagicMock()
    coord.last_update_success = True
    coord.last_exception = None
    coord.data = {"SN123": {"device_name": "Test Device", "alarms": []}}
    fixed_now = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    coord.hyxi_metadata = {
        "last_attempts": 1, 
        "last_success": fixed_now.isoformat(),
        "last_error": None
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

    await async_setup_entry(hass, mock_entry, async_add_entities)

    assert async_add_entities.called
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 2
    assert isinstance(entities[0], HyxiConnectivitySensor)
    assert isinstance(entities[1], HyxiDeviceAlarmSensor)

def test_connectivity_sensor_diagnostics(mock_coordinator, mock_entry):
    """Test connectivity sensor error and availability attributes."""
    sensor = HyxiConnectivitySensor(mock_coordinator, mock_entry)
    
    # 1. Test success state
    attrs = sensor.extra_state_attributes
    assert attrs["last_update"] == mock_coordinator.hyxi_metadata["last_success"]
    assert attrs["last_error"] == "None"
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
    
    sensor = HyxiDeviceAlarmSensor(mock_coordinator, mock_entry, "SN123")
    
    assert sensor.is_on is True
    assert sensor.extra_state_attributes["active_alarms_count"] == 2
    
    # Test update via coordinator handle
    mock_coordinator.data["SN123"]["alarms"] = []
    sensor._handle_coordinator_update()
    assert sensor.is_on is False

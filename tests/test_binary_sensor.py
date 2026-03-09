"""Tests for the HYXi Cloud binary sensor logic."""

from unittest.mock import MagicMock

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.hyxi_cloud.binary_sensor import HyxiDeviceAlarmSensor


def test_alarm_sensor_active():
    """Test device active alarm sensor evaluation."""
    coordinator = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    sn = "55555555555555"

    # Mock an active alarm (State 0 - Unprocessed)
    coordinator.data = {
        sn: {
            "device_name": "Test Inverter",
            "alarms": [{"alarmState": 0, "alarmName": "Fault 1"}],
        }
    }

    sensor = HyxiDeviceAlarmSensor(coordinator, entry, sn)

    assert sensor.is_on is True
    assert sensor.extra_state_attributes["active_alarms_count"] == 1
    assert sensor.device_class == BinarySensorDeviceClass.PROBLEM


def test_alarm_sensor_restored():
    """Test device active alarm sensor returns False on restored alarms."""
    coordinator = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    sn = "11111111111111"

    # Mock a resolved alarm (State 2 - Restored)
    coordinator.data = {sn: {"alarms": [{"alarmState": 2, "alarmName": "Fault 2"}]}}

    sensor = HyxiDeviceAlarmSensor(coordinator, entry, sn)

    assert sensor.is_on is False
    assert sensor.extra_state_attributes["active_alarms_count"] == 0

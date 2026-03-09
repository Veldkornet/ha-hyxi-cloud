"""Tests for the HYXI Cloud binary sensor logic."""
# pylint: disable=wrong-import-position, unused-argument, import-outside-toplevel, redefined-outer-name, invalid-name

import sys
from unittest.mock import MagicMock


class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


class FakeBinarySensorEntity(FakeBase):
    pass


mock_ha = MagicMock()
mock_ha.__path__ = []
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha

mock_binary_sensor = MagicMock()
mock_binary_sensor.BinarySensorEntity = FakeBinarySensorEntity


class FakeBinarySensorDeviceClass:
    PROBLEM = "problem"
    CONNECTIVITY = "connectivity"


mock_binary_sensor.BinarySensorDeviceClass = FakeBinarySensorDeviceClass
sys.modules["homeassistant.components.binary_sensor"] = mock_binary_sensor

mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator
sys.modules["homeassistant.util"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["hyxi_cloud_api"] = mock_ha
sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha
sys.modules["homeassistant.helpers.entity_platform"] = mock_ha

from homeassistant.components.binary_sensor import BinarySensorDeviceClass  # noqa: E402, I001
from custom_components.hyxi_cloud.binary_sensor import HyxiDeviceAlarmSensor  # noqa: E402, I001


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
    assert (
        getattr(sensor, "device_class", sensor._attr_device_class)
        == BinarySensorDeviceClass.PROBLEM
    )


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

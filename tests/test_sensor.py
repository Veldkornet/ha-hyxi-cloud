"""Tests for the sensor platform."""

import importlib
import sys
import unittest
from datetime import UTC, datetime
from typing import Any
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


class FakeSensorEntity(FakeBase):
    @property
    def native_value(self):
        return getattr(self, "_attr_native_value", None)


class FakeRestoreEntity(FakeBase):
    async def async_get_last_state(self):
        return None

    async def async_added_to_hass(self):
        pass


mock_ha: Any = sys.modules.get("homeassistant")
if mock_ha is None:
    mock_ha = MagicMock()
    mock_ha.__path__ = []
    sys.modules["homeassistant"] = mock_ha

mock_ha.CoordinatorEntity = FakeCoordinatorEntity
mock_ha.SensorEntity = FakeSensorEntity

if "homeassistant.components" not in sys.modules:
    sys.modules["homeassistant.components"] = MagicMock()
if "homeassistant.components.sensor" not in sys.modules:
    sys.modules["homeassistant.components.sensor"] = MagicMock()
sensor_mock: Any = sys.modules["homeassistant.components.sensor"]
sensor_mock.SensorEntity = FakeSensorEntity
sensor_mock.SensorDeviceClass = MagicMock()
sensor_mock.SensorStateClass = MagicMock()
sensor_mock.SensorStateClass.TOTAL_INCREASING = "total_increasing"
sensor_mock.SensorStateClass.MEASUREMENT = "measurement"


class FakeSensorEntityDescription:
    def __init__(self, key, **kwargs):
        self.key = key
        self.translation_key = kwargs.get("translation_key")
        self.device_class = kwargs.get("device_class")
        self.state_class = kwargs.get("state_class")
        self.native_unit_of_measurement = kwargs.get("native_unit_of_measurement")
        self.entity_category = kwargs.get("entity_category")
        self.icon = kwargs.get("icon")
        self.options = kwargs.get("options")


sensor_mock.SensorEntityDescription = FakeSensorEntityDescription
sensor_mock.EntityCategory = MagicMock()

if "homeassistant.config_entries" not in sys.modules:
    sys.modules["homeassistant.config_entries"] = mock_ha
if "homeassistant.const" not in sys.modules:
    sys.modules["homeassistant.const"] = mock_ha
if "homeassistant.core" not in sys.modules:
    sys.modules["homeassistant.core"] = mock_ha
if "homeassistant.helpers" not in sys.modules:
    sys.modules["homeassistant.helpers"] = mock_ha
if "homeassistant.helpers.storage" not in sys.modules:
    sys.modules["homeassistant.helpers.storage"] = mock_ha
    sys.modules["homeassistant.helpers"] = mock_ha
if "homeassistant.helpers.restore_state" not in sys.modules:
    sys.modules["homeassistant.helpers.restore_state"] = mock_ha
sys.modules["homeassistant.helpers.restore_state"].RestoreEntity = FakeRestoreEntity  # type: ignore[attr-defined]

if "homeassistant.helpers.update_coordinator" not in sys.modules:
    sys.modules["homeassistant.helpers.update_coordinator"] = mock_ha
coord_mock: Any = sys.modules["homeassistant.helpers.update_coordinator"]
coord_mock.CoordinatorEntity = FakeCoordinatorEntity


mock_util = sys.modules.get("homeassistant.util")
if mock_util is None:
    mock_util = MagicMock()
    mock_util.__spec__ = None
    sys.modules["homeassistant.util"] = mock_util


# We need a real-ish dt_util for parsing to work in the component
mock_dt = MagicMock()
mock_dt.__spec__ = None
sys.modules["homeassistant.util.dt"] = mock_dt
import homeassistant.util.dt as dt_util

mock_dt = MagicMock()
mock_dt.UTC = UTC
mock_dt.parse_datetime = dt_util.parse_datetime
# Fixed return value for utcnow to be consistent
mock_dt.utcnow.return_value = datetime(2026, 3, 11, 12, 0, 0, tzinfo=UTC)
sys.modules["homeassistant.util.dt"] = mock_dt
mock_ha.util.dt = mock_dt  # Ensure both paths work

# Now import and reload the component to ensure it uses the mock
import custom_components.hyxi_cloud.sensor as sensor_mod

importlib.reload(sensor_mod)

from custom_components.hyxi_cloud.const import DOMAIN


@pytest.fixture
def mock_coordinator():
    coord = MagicMock()
    coord.on_unload = MagicMock()
    coord.data = {
        "SN123": {
            "deviceCode": "1",
            "metrics": {
                "ph1Loadp": "100.0",
                "batSoc": "50",
                "acE": "10.5",
                "grid_import": "null",
                "totalE": "20.1",
            },
            "model": "HYS-3.0",
            "device_name": "Test Inverter",
        },
        "SN456": {
            "deviceCode": "5",
            "model": "DMU",
            "device_name": "Test Collector",
            "metrics": {
                "signalIntensity": "-60",
                "comMode": "WiFi",
            },
        },
    }
    coord.hyxi_metadata = {"last_success": "2026-03-11T12:00:00Z"}
    coord.push_status = "active"
    coord.alarm_push_status = "active"
    coord.subscribe_code = "SUB123"
    coord.push_url = "http://test"
    coord.last_push_received = datetime(2026, 3, 11, 11, 55, 0, tzinfo=UTC)
    coord.push_error = None
    coord.entry = MagicMock()
    coord.entry.options = {"push_rate": 60}
    return coord


@pytest.fixture
def mock_entry():
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {}
    return entry


@pytest.mark.asyncio
async def test_async_setup_entry(mock_coordinator, mock_entry):
    """Test setting up sensors."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry.entry_id: mock_coordinator}}
    async_add_entities = MagicMock()

    # Mock battery control to False for simplicity
    with unittest.mock.patch(
        "custom_components.hyxi_cloud.sensor.is_battery_control_enabled",
        return_value=False,
    ):
        await sensor_mod.async_setup_entry(hass, mock_entry, async_add_entities)

    assert async_add_entities.called
    entities = async_add_entities.call_args[0][0]

    # Check that integration health sensors are added
    health_sensors = [
        e for e in entities if isinstance(e, sensor_mod.HyxiLastUpdateSensor)
    ]
    assert len(health_sensors) == 1

    subscription_sensors = [
        e for e in entities if isinstance(e, sensor_mod.HyxiSubscriptionStatusSensor)
    ]
    assert len(subscription_sensors) == 1

    # Check that device sensors are added
    device_sensors = [e for e in entities if isinstance(e, sensor_mod.HyxiSensor)]
    assert len(device_sensors) > 0


def test_process_numeric_value_normal():
    """Test standard numeric processing."""

    sensor = sensor_mod.HyxiBaseSensor(MagicMock())
    sensor.entity_description = MagicMock()
    sensor.entity_description.native_unit_of_measurement = "W"
    sensor.entity_description.state_class = "measurement"

    # Test normal conversion
    assert sensor._process_numeric_value("100.5") == 100.5

    # Test null handling
    assert sensor._process_numeric_value("null") is None

    # Test fallback on invalid numeric
    assert sensor._process_numeric_value("invalid") == "invalid"


def test_anti_dip_filter():
    """Test that the anti-dip filter works correctly for TOTAL_INCREASING sensors."""

    sensor = sensor_mod.HyxiBaseSensor(MagicMock())
    sensor.entity_description = MagicMock()
    sensor.entity_description.key = "acE"
    sensor.entity_description.native_unit_of_measurement = "kWh"
    sensor.entity_description.state_class = "total_increasing"

    # Initial value
    assert sensor._process_numeric_value("100.0") == 100.0

    # Normal increase
    assert sensor._process_numeric_value("105.0") == 105.0

    # Small dip (should be prevented)
    assert sensor._process_numeric_value("104.0") == 105.0

    # Valid reset (drop to ~0, significant drop)
    assert sensor._process_numeric_value("0.05") == 0.05


def test_anti_spike_filter():
    """Test that the anti-spike filter works correctly for TOTAL_INCREASING sensors."""

    sensor = sensor_mod.HyxiBaseSensor(MagicMock())
    sensor.entity_description = MagicMock()
    sensor.entity_description.key = "acE"
    sensor.entity_description.native_unit_of_measurement = "kWh"
    sensor.entity_description.state_class = "total_increasing"

    # Initial value
    assert sensor._process_numeric_value("10.0") == 10.0

    # Time elapsed makes spike acceptable or not. Let's mock a short time.
    sensor._last_valid_time = datetime(2026, 3, 11, 11, 55, 0, tzinfo=UTC)
    sensor_mod.dt_util.utcnow.return_value = datetime(2026, 3, 11, 12, 0, 0, tzinfo=UTC)

    # Small increase
    assert sensor._process_numeric_value("15.0") == 15.0

    # Impossible jump (> threshold)
    # The default formula: 100 + (50 * elapsed_hours)
    # 5 mins = 0.0833 hrs -> threshold ~ 104.16
    assert sensor._process_numeric_value("1000.0") == 15.0


def test_hyxi_sensor_parsing_int():
    """Test integer parsing for keys inside INT_SENSOR_KEYS."""
    coord = MagicMock()
    coord.data = {"SN1": {"deviceCode": "1", "metrics": {"batSoc": "95.5"}}}

    desc = MagicMock()
    desc.key = "batSoc"  # The check `key_lower in INT_SENSOR_KEYS` will catch "batsoc"
    desc.translation_key = "batsoc"
    desc.device_class = None
    desc.state_class = None

    sensor = sensor_mod.HyxiSensor(coord, "SN1", desc)
    sensor._device_type = "micro_inverter"
    sensor._update_native_value()
    assert sensor.native_value == 96  # Rounded and int cast


def test_fallback_micro_inverter():
    """Test acE falling back to efpv for micro inverters."""
    coord = MagicMock()
    coord.data = {
        "SN1": {
            "deviceCode": "3",  # Micro Inverter
            "metrics": {"acE": "0.0", "efpv": "50.5"},
        }
    }

    desc = MagicMock()
    desc.key = "acE"
    desc.translation_key = "ace"
    desc.device_class = None
    desc.state_class = None

    sensor = sensor_mod.HyxiSensor(coord, "SN1", desc)
    sensor._device_type = "micro_inverter"
    sensor._update_native_value()
    assert sensor.native_value == 50.5


def test_health_sensor(mock_coordinator, mock_entry):
    """Test integration health sensor native value."""
    sensor = sensor_mod.HyxiLastUpdateSensor(mock_coordinator, mock_entry)
    assert sensor.native_value == "2026-03-11T12:00:00Z"


def test_subscription_status_sensor(mock_coordinator, mock_entry):
    """Test subscription status sensor combined state."""
    sensor = sensor_mod.HyxiSubscriptionStatusSensor(mock_coordinator, mock_entry)
    assert sensor.native_value == "active"

    # Set alarm to error
    mock_coordinator.alarm_push_status = "error"
    sensor._update_value()
    assert sensor.native_value == "error"

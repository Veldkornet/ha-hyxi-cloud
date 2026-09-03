"""Tests for the Hyxi Cloud sensor entity logic."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel, too-many-lines
import importlib
import logging
import sys
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# 1. THE BULLETPROOF MOCK
class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    # Allow CoordinatorEntity[HyxiDataUpdateCoordinator] subscripting in class bases
    __class_getitem__ = classmethod(lambda cls, item: cls)

    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator

    def _handle_coordinator_update(self) -> None:
        pass


class FakeSensorEntity(FakeBase):
    @property
    def native_value(self):
        return getattr(self, "_attr_native_value", None)


class FakeRestoreEntity(FakeBase):
    async def async_added_to_hass(self):
        pass


# Create a mock homeassistant environment BEFORE importing integration code
mock_ha = sys.modules.get("homeassistant")
if mock_ha is None:
    mock_ha = MagicMock()
    mock_ha.__name__ = "mock_ha"
    mock_ha.__path__ = []  # IMPORTANT for nested module resolution
    mock_ha.callback = lambda func: func
    sys.modules["homeassistant"] = mock_ha

if "homeassistant.components" not in sys.modules:
    sys.modules["homeassistant.components"] = MagicMock()
if "homeassistant.config_entries" not in sys.modules:
    sys.modules["homeassistant.config_entries"] = mock_ha
if "homeassistant.core" not in sys.modules:
    sys.modules["homeassistant.core"] = mock_ha
if "homeassistant.exceptions" not in sys.modules:
    sys.modules["homeassistant.exceptions"] = mock_ha
if "homeassistant.const" not in sys.modules:
    sys.modules["homeassistant.const"] = mock_ha

# Also ensure hyxi_cloud_api has __version__ even if it's mocked
mock_api = sys.modules["hyxi_cloud_api"]

# We need SensorEntityDescription to retain its attributes instead of being a generic mock
if "homeassistant.components.sensor" not in sys.modules:
    sys.modules["homeassistant.components.sensor"] = MagicMock()
mock_sensor: Any = sys.modules["homeassistant.components.sensor"]


def mock_sensor_entity_description(**kwargs):
    desc = MagicMock()
    for k, v in kwargs.items():
        setattr(desc, k, v)
    return desc


mock_sensor.SensorEntityDescription = mock_sensor_entity_description
mock_sensor.SensorEntity = FakeSensorEntity
if not hasattr(mock_sensor, "SensorDeviceClass"):
    mock_sensor.SensorDeviceClass = MagicMock()
if not hasattr(mock_sensor, "SensorStateClass"):
    mock_sensor.SensorStateClass = MagicMock()

# Other mocked dependencies
if "homeassistant.helpers" not in sys.modules:
    sys.modules["homeassistant.helpers"] = mock_ha

if "homeassistant.helpers.restore_state" not in sys.modules:
    sys.modules["homeassistant.helpers.restore_state"] = MagicMock()
mock_restore: Any = sys.modules["homeassistant.helpers.restore_state"]
mock_restore.RestoreEntity = FakeRestoreEntity

if "homeassistant.helpers.update_coordinator" not in sys.modules:
    sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
mock_coordinator: Any = sys.modules["homeassistant.helpers.update_coordinator"]
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity

if "homeassistant.helpers.aiohttp_client" not in sys.modules:
    sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
if "homeassistant.util" not in sys.modules:
    sys.modules["homeassistant.util"] = mock_ha

if "aiohttp" not in sys.modules:
    sys.modules["aiohttp"] = MagicMock()


# Standardize import style to resolve code scanning alert no. 50
import custom_components.hyxi_cloud.const as const_mod
import custom_components.hyxi_cloud.sensor as sensor_mod

try:
    importlib.reload(const_mod)
except ImportError:
    # reload failures are intentionally ignored because the modules have already
    # been imported and the tests can still run.
    pass
try:
    importlib.reload(sensor_mod)
except ImportError:
    # If sensor_mod cannot be reloaded, we skip the tests to avoid silent failures
    # or carrying over stale MagicMock pollution from other test files.
    pytest.skip("Could not reload sensor_mod; skipping to avoid stale mock pollution")

# Wire up real const.py functions into sensor_mod to bypass MagicMock pollution.
# This ensures that any test-level patch() targeting
# 'custom_components.hyxi_cloud.sensor.normalize_device_type' or
# 'custom_components.hyxi_cloud.sensor.get_raw_device_code'
# correctly overrides the real implementations rather than a MagicMock.
sensor_mod.normalize_device_type = const_mod.normalize_device_type
sensor_mod.get_raw_device_code = const_mod.get_raw_device_code
sensor_mod.mask_sn = const_mod.mask_sn
sensor_mod.is_null_value = const_mod.is_null_value


@pytest.fixture
def base_sensor():
    """Fixture to create a standard energy sensor for testing."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"totalE": 2742.0}}}
    description = MagicMock()
    description.key = "totalE"
    description.native_unit_of_measurement = "kWh"
    description.state_class = "total_increasing"

    sensor = sensor_mod.HyxiSensor(coordinator, "SN123", description)
    sensor.hass = None
    return sensor, coordinator


def test_mask_sn():
    """Verify mask_sn correctly hides the middle of serial numbers."""
    from custom_components.hyxi_cloud.sensor import mask_sn

    # Empty/None handling
    assert mask_sn(None) == "****"
    assert mask_sn("") == "****"

    # Short string handling
    assert mask_sn("1234567") == "8bb0cf6e"

    # Exact length of 8
    assert mask_sn("12345678") == "ef797c81"

    # Longer string
    assert mask_sn("1234567890") == "c775e7b7"

    # Integer values
    assert mask_sn(12345678) == "ef797c81"


def test_anti_dip_recovery(base_sensor):
    """Verify the exact scenario in your graph: 2742 -> 2738 -> 2747."""
    sensor, coordinator = base_sensor

    # Baseline
    assert sensor.native_value == 2742.0

    # 📉 The Dip (Should be blocked)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2738.0
    sensor._handle_coordinator_update()
    assert sensor.native_value == 2742.0

    # 📈 The Recovery (Should be allowed as it's a valid increase from baseline)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2747.0
    sensor._handle_coordinator_update()
    assert sensor.native_value == 2747.0


def test_anti_spike_prevention(base_sensor):
    """Verify that jumps greater than 100.0 are blocked."""
    sensor, coordinator = base_sensor

    # Baseline
    assert sensor.native_value == 2742.0

    # 📈 Valid jump <= 100.0 (allowed)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2842.0
    sensor._handle_coordinator_update()
    assert sensor.native_value == 2842.0

    # 🚀 Invalid spike > 100.0 (blocked, returns last valid value)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2943.0
    sensor._handle_coordinator_update()
    assert sensor.native_value == 2842.0

    # 📉 Small increase after spike (allowed)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2850.0
    sensor._handle_coordinator_update()
    assert sensor.native_value == 2850.0


def test_null_data_handling(base_sensor):
    """Ensure the sensor returns None instead of crashing on empty or null-equivalent API data."""
    sensor, coordinator = base_sensor

    # Standard None/Empty
    for val in [None, ""]:
        coordinator.data["SN123"]["metrics"]["totalE"] = val
        sensor._handle_coordinator_update()
        assert sensor.native_value is None

    # Null-equivalent strings handled by the fix
    for val in ["null", "none", "na", "--", "  null  ", "None"]:
        coordinator.data["SN123"]["metrics"]["totalE"] = val
        sensor._handle_coordinator_update()
        assert sensor.native_value is None, (
            f"Failed to handle null-equivalent string: {val}"
        )


def test_timestamp_scaling(base_sensor):
    """Verify 10-digit (sec) and 13-digit (ms) timestamps both work."""
    sensor, _ = base_sensor
    sensor.entity_description.key = "collectTime"
    sensor._parser_func = sensor._parse_collect_time
    sensor.entity_description.native_unit_of_measurement = (
        None  # Timestamps don't have units
    )

    # 10 Digits
    sensor.coordinator.data["SN123"]["metrics"]["collectTime"] = 1741248000
    sensor._handle_coordinator_update()
    assert isinstance(sensor.native_value, datetime)

    # 13 Digits
    sensor.coordinator.data["SN123"]["metrics"]["collectTime"] = 1741248000000
    sensor._handle_coordinator_update()
    assert isinstance(sensor.native_value, datetime)


def test_collecttime_error_handling(base_sensor):
    """Verify that invalid collectTime values are caught and return None."""
    sensor, coordinator = base_sensor
    sensor.entity_description.key = "collectTime"
    sensor._parser_func = sensor._parse_collect_time

    # Test ValueError (unparsable string)
    coordinator.data["SN123"]["metrics"]["collectTime"] = "invalid_timestamp"
    sensor._handle_coordinator_update()
    assert sensor.native_value is None

    # Test TypeError (invalid type like dict or list)
    coordinator.data["SN123"]["metrics"]["collectTime"] = {"time": 123}
    sensor._handle_coordinator_update()
    assert sensor.native_value is None

    # Test extreme value causing OverflowError/OSError in datetime.fromtimestamp
    # A huge number that passes the 10-digit check but is still too large for datetime
    coordinator.data["SN123"]["metrics"]["collectTime"] = 1000000000000000000
    sensor._handle_coordinator_update()
    assert sensor.native_value is None

    # Test extreme overflow value (triggering OverflowError on many platforms)
    coordinator.data["SN123"]["metrics"]["collectTime"] = 10**25
    sensor._handle_coordinator_update()
    assert sensor.native_value is None

    # Test OSError explicitly by patching datetime since OverflowError is now ValueError in Python 3.12+
    with patch("custom_components.hyxi_cloud.sensor.datetime") as mock_dt:
        mock_dt.fromtimestamp.side_effect = OSError("mocked OSError")
        coordinator.data["SN123"]["metrics"]["collectTime"] = 1234567890
        sensor._handle_coordinator_update()
        assert sensor.native_value is None


def test_rounding_protection(base_sensor):
    """Ensure floating point noise (2.73199999) is rounded correctly."""
    sensor, coordinator = base_sensor
    coordinator.data["SN123"]["metrics"]["totalE"] = 2742.123456
    sensor._handle_coordinator_update()
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
    sensor._handle_coordinator_update()
    val = sensor.native_value

    print(f"[Night Correction] Jumped from 2742.0 to {val} kWh")
    assert val == 2743.5  # Should be ALLOWED


def test_batsoc_batsoh_casting(base_sensor):
    """Verify batSoc and batSoh correctly cast to integers after rounding."""
    sensor, coordinator = base_sensor

    # Test batSoc
    sensor.entity_description.key = "batSoc"
    sensor._parser_func = sensor._parse_int_sensor
    coordinator.data["SN123"]["metrics"]["batSoc"] = 85.6
    sensor._handle_coordinator_update()
    assert sensor.native_value == 86

    # Test batSoh
    sensor.entity_description.key = "batSoh"
    sensor._parser_func = sensor._parse_int_sensor
    coordinator.data["SN123"]["metrics"]["batSoh"] = 99.1
    sensor._handle_coordinator_update()
    assert sensor.native_value == 99

    # Test invalid string gracefully handled (falls back to _process_numeric_value)
    coordinator.data["SN123"]["metrics"]["batSoh"] = "invalid"
    sensor._handle_coordinator_update()
    assert sensor.native_value == "invalid"

    # Test invalid type gracefully handled (falls back to _process_numeric_value)
    coordinator.data["SN123"]["metrics"]["batSoh"] = {"invalid": "dict"}
    sensor._handle_coordinator_update()
    assert sensor.native_value == {"invalid": "dict"}


@pytest.mark.asyncio
async def test_new_api_metrics_registration():
    """Verify that all new PV, Phase, Battery, and Status sensors instantiate correctly."""
    from custom_components.hyxi_cloud.const import DOMAIN

    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {}  # No virtual battery

    coordinator = MagicMock()

    # Simulate a hybrid inverter payload containing all the new metrics
    coordinator.data = {
        "INV123": {
            "device_type_code": "HYBRID_INVERTER",
            "metrics": {
                "ph1Loadp": 120.0,
                "ph2Loadp": 240.0,
                "ph3Loadp": 360.0,
                "ph1v": 220.0,
                "ph2v": 220.0,
                "ph3v": 220.0,
                "ph1i": 5.0,
                "ph2i": 5.0,
                "ph3i": 5.0,
                "ph1p": 1100.0,
                "ph2p": 1100.0,
                "ph3p": 1100.0,
                "pv1v": 300.1,
                "pv2v": 310.2,
                "pv1i": 5.5,
                "pv2i": 6.6,
                "pv1p": 1650.55,
                "pv2p": 2047.32,
                "batV": 48.2,
                "batI": -12.5,
                "vbus": 400.0,
                "f": 50.01,
                "acE": 12345.6,
                "deviceState": "Running",
                "ratedPower": 10000,
                "ratedVoltage": 220,
            },
        },
        "COLL123": {
            "device_type_code": "COLLECTOR",
            "metrics": {
                "childNum": 3,
                "batCap": 20.0,
                "maxChargePower": 10000.0,
                "maxDischargePower": 10000.0,
            },
        },
    }
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    # We need to capture the sensors that async_setup_entry attempts to register
    registered_entities = []

    def mock_async_add_entities(entities):
        registered_entities.extend(entities)

    await sensor_mod.async_setup_entry(hass, entry, mock_async_add_entities)

    # Extract just the string keys of the sensors that were registered (ignoring diagnostics without descriptions)
    registered_keys = [
        getattr(entity.entity_description, "key", None)
        for entity in registered_entities
        if hasattr(entity, "entity_description")
    ]

    # Verify all new metrics exist in the registration list
    expected_new_keys = [
        "ph1Loadp",
        "ph2Loadp",
        "ph3Loadp",
        "ph1v",
        "ph2v",
        "ph3v",
        "ph1i",
        "ph2i",
        "ph3i",
        "ph1p",
        "ph2p",
        "ph3p",
        "pv1v",
        "pv2v",
        "pv1i",
        "pv2i",
        "pv1p",
        "pv2p",
        "batV",
        "batI",
        "vbus",
        "f",
        "acE",
        "deviceState",
        "ratedPower",
        "ratedVoltage",
        "childNum",
    ]

    for key in expected_new_keys:
        assert key in registered_keys, (
            f"Sensor '{key}' was not registered by async_setup_entry"
        )


@pytest.mark.asyncio
async def test_async_setup_entry_no_data():
    """Verify that async_setup_entry returns early when coordinator has no data."""
    from custom_components.hyxi_cloud.const import DOMAIN

    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    coordinator = MagicMock()
    coordinator.data = {}
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    mock_async_add_entities = MagicMock()
    await sensor_mod.async_setup_entry(hass, entry, mock_async_add_entities)

    # Should exit early and not add any entities if data is empty
    mock_async_add_entities.assert_not_called()

    # Also test None
    coordinator.data = None
    await sensor_mod.async_setup_entry(hass, entry, mock_async_add_entities)

    # Should exit early and not add any entities if data is None
    mock_async_add_entities.assert_not_called()


def test_sensor_int_conversion_error(base_sensor):
    """Test that invalid numeric strings or objects return None for batSoc, batSoh, signalVal."""
    sensor, coordinator = base_sensor
    coordinator.data["SN123"]["metrics"]["batSoc"] = "100"

    # Test keys: batsoc, batsoh, signalval (case insensitive in sensor.py)
    for key in ["batSoc", "batSoh", "signalVal"]:
        sensor.entity_description.key = key
        sensor._parser_func = sensor._parse_int_sensor

        # Test valid string
        coordinator.data["SN123"]["metrics"][key] = "85.5"
        sensor._handle_coordinator_update()
        assert sensor.native_value == 86

        # Test invalid string (falls back to _process_numeric_value)
        coordinator.data["SN123"]["metrics"][key] = "invalid_string"
        sensor._handle_coordinator_update()
        assert sensor.native_value == "invalid_string"

        # Test non-numeric object (falls back to _process_numeric_value)
        coordinator.data["SN123"]["metrics"][key] = {"unexpected": "data"}
        sensor._handle_coordinator_update()
        assert sensor.native_value == {"unexpected": "data"}

        # Test None value (handled by earlier check but good to verify)
        coordinator.data["SN123"]["metrics"][key] = None
        sensor._handle_coordinator_update()
        assert sensor.native_value is None

        # Test empty string (handled by earlier check)
        coordinator.data["SN123"]["metrics"][key] = ""
        sensor._handle_coordinator_update()
        assert sensor.native_value is None


def test_sensor_int_conversion_non_numeric_string(base_sensor):
    """Test ValueError and TypeError handling specifically for INT_SENSOR_KEYS."""
    sensor, coordinator = base_sensor
    coordinator.data["SN123"]["metrics"]["batSoc"] = "100"

    # We choose one key from INT_SENSOR_KEYS
    sensor.entity_description.key = "batSoc"
    sensor._parser_func = sensor._parse_int_sensor

    # String that raises ValueError on float() conversion (falls back to _process_numeric_value)
    coordinator.data["SN123"]["metrics"]["batSoc"] = "non_numeric_string"
    sensor._handle_coordinator_update()
    assert sensor.native_value == "non_numeric_string"

    # Object that raises TypeError on float() conversion (falls back to _process_numeric_value)
    coordinator.data["SN123"]["metrics"]["batSoc"] = {"unexpected": "object"}
    sensor._handle_coordinator_update()
    assert sensor.native_value == {"unexpected": "object"}


def test_float_conversion_error(base_sensor):
    """Verify that a non-numeric string gracefully falls back."""
    sensor, coordinator = base_sensor
    coordinator.data["SN123"]["metrics"]["totalE"] = "bad_data"
    sensor._handle_coordinator_update()
    assert sensor.native_value == "bad_data"


@pytest.mark.asyncio
async def test_sensor_added_to_hass_restoration():
    """Verify that HyxiSensor restores its last state on addition to Home Assistant."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"totalE": None}}}
    description = MagicMock()
    description.key = "totalE"
    description.state_class = "total_increasing"

    sensor = sensor_mod.HyxiSensor(coordinator, "SN123", description)
    sensor.hass = MagicMock()

    # Mock last state
    last_state = MagicMock()
    last_state.state = "123.45"
    sensor.async_get_last_state = AsyncMock(return_value=last_state)

    await sensor.async_added_to_hass()

    assert sensor._last_valid_value == 123.45
    assert sensor._last_valid_time is None


@pytest.mark.asyncio
async def test_sensor_added_to_hass_no_restoration():
    """Verify that HyxiSensor handles missing last state gracefully."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"totalE": None}}}
    description = MagicMock()
    description.key = "totalE"
    description.state_class = "total_increasing"

    sensor = sensor_mod.HyxiSensor(coordinator, "SN123", description)
    sensor.hass = MagicMock()

    # Mock missing last state
    sensor.async_get_last_state = AsyncMock(return_value=None)

    await sensor.async_added_to_hass()

    assert sensor._last_valid_value is None


@pytest.mark.asyncio
async def test_sensor_added_to_hass_invalid_restoration():
    """Verify that HyxiSensor handles invalid last state values gracefully."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"totalE": None}}}
    description = MagicMock()
    description.key = "totalE"
    description.state_class = "total_increasing"

    sensor = sensor_mod.HyxiSensor(coordinator, "SN123", description)
    sensor.hass = MagicMock()

    # Mock invalid last state
    last_state = MagicMock()
    last_state.state = "unknown"
    sensor.async_get_last_state = AsyncMock(return_value=last_state)

    await sensor.async_added_to_hass()

    assert sensor._last_valid_value is None


@pytest.mark.asyncio
async def test_hyxi_last_update_sensor_failure():
    """Test the diagnostic 'Last Update' sensor failure modes."""

    coordinator = MagicMock()
    coordinator.last_update_success = False
    coordinator.hyxi_metadata = {"last_success": None}
    entry = MagicMock()
    entry.entry_id = "test_entry"

    sensor = sensor_mod.HyxiLastUpdateSensor(coordinator, entry)

    assert sensor.native_value is None


def test_hyxi_base_sensor_direct_unit_return(base_sensor):
    """Test safety return when no units are defined (e.g. state strings)."""
    sensor, coordinator = base_sensor
    sensor.entity_description.native_unit_of_measurement = None

    # Should return exactly what it gets
    coordinator.data["SN123"]["metrics"]["totalE"] = "Any Value"
    sensor._handle_coordinator_update()
    assert sensor.native_value == "Any Value"


def test_hyxi_base_sensor_early_exit_safety(base_sensor):
    """Test early exits for None/Empty values in _process_numeric_value."""
    sensor, _ = base_sensor
    # This specifically tests the _process_numeric_value internal branch
    assert sensor._process_numeric_value(None) is None
    assert sensor._process_numeric_value("") is None


@pytest.mark.asyncio
async def test_hyxi_last_update_sensor_success():
    """Test the diagnostic 'Last Update' sensor success path."""
    from datetime import UTC, datetime

    fixed_dt = datetime(2026, 3, 11, 12, 0, 0, tzinfo=UTC)

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.hyxi_metadata = {"last_success": fixed_dt}
    entry = MagicMock()
    entry.entry_id = "test_entry"

    sensor = sensor_mod.HyxiLastUpdateSensor(coordinator, entry)

    # CoordinatorEntity.available is determined by coordinator.last_update_success.
    # That behaviour belongs to HA's CoordinatorEntity, not our custom code.
    # We verify it's wired correctly by checking the coordinator value passes through.
    assert sensor.coordinator.last_update_success is True
    assert isinstance(sensor.native_value, datetime)


def test_hyxi_sensor_last_seen(base_sensor):
    """Test the last_seen special case."""
    from datetime import UTC, datetime

    sensor, coordinator = base_sensor
    sensor.entity_description.key = "last_seen"
    sensor._parser_func = sensor._parse_last_seen

    fixed_time_str = "2026-03-11T12:00:00+00:00"
    fixed_time_dt = datetime(
        2026,
        3,
        11,
        12,
        0,
        0,
        tzinfo=UTC,
    )
    coordinator.data["SN123"]["metrics"]["last_seen"] = fixed_time_str

    with patch(
        "custom_components.hyxi_cloud.sensor.dt_util.parse_datetime",
        return_value=fixed_time_dt,
    ):
        sensor._handle_coordinator_update()
        assert isinstance(
            sensor.native_value,
            datetime,
        )


@pytest.mark.asyncio
async def test_sensor_batteries_and_collectors():
    """Verify that battery sensors are skipped for COLLECTOR devices.

    Uses the real normalize_device_type + get_raw_device_code pipeline from
    const.py (wired in at module level) rather than patching normalize_device_type.
    This is more resilient: it tests the full lookup chain and is immune to
    import-order issues where a patch may miss its target because the name was
    bound before the patch was applied.
    """

    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    coordinator = MagicMock()

    # 'device_type_code': 'COLLECTOR' is handled by get_raw_device_code, then
    # normalize_device_type maps 'COLLECTOR' -> 'collector' via DEVICE_TYPE_KEYS.
    coordinator.data = {
        "COLL123": {
            "device_type_code": "COLLECTOR",
            "metrics": {
                "batSoc": 100,  # Must be skipped for collector devices
                "signalVal": 80,  # Must be registered
            },
        }
    }
    hass.data = {"hyxi_cloud": {"test_entry": coordinator}}

    registered_entities = []

    def mock_async_add_entities(entities):
        registered_entities.extend(entities)

    # No patch needed: the real pipeline in const.py correctly identifies
    # 'COLLECTOR' as device_type='collector', triggering the battery skip.
    await sensor_mod.async_setup_entry(hass, entry, mock_async_add_entities)

    registered_keys = []
    for e in registered_entities:
        if hasattr(e, "entity_description"):
            registered_keys.append(e.entity_description.key)
        else:
            # Handle HyxiLastUpdateSensor which has no entity_description
            registered_keys.append("LAST_UPDATE")

    assert "signalVal" in registered_keys, "signalVal must be registered for collector"
    assert "batSoc" not in registered_keys, (
        "batSoc must be skipped for collector devices"
    )


def test_battery_serial_mapping(base_sensor):
    """A battery sensor is identified by the inverter serial (stable across
    restarts) but still grouped under the battery device via batSn."""
    coordinator = MagicMock()
    coordinator.data = {
        "INV123": {
            "metrics": {"batSoc": 50, "batSn": "BAT_REAL_123"},
            "device_name": "My Inverter",
        }
    }
    description = MagicMock()
    description.key = "batSoc"

    sensor = sensor_mod.HyxiSensor(coordinator, "INV123", description)

    # Identity: inverter serial, not batSn -- see _migrate_battery_sensor_unique_ids.
    assert sensor._attr_unique_id == "hyxi_INV123_batSoc"
    assert sensor.entity_id == "sensor.hyxi_INV123_batsoc"
    # Grouping: still the battery device.
    assert sensor.device_info["identifiers"] == {("hyxi_cloud", "BAT_REAL_123")}
    assert sensor.device_info["name"] == "Battery BAT_REAL_123"


def test_hyxi_base_sensor_conversion_errors(base_sensor):
    """Test ValueError and TypeError handling in _process_numeric_value."""
    sensor, _ = base_sensor
    # Ensure native_unit_of_measurement is set so it doesn't return early
    sensor.entity_description.native_unit_of_measurement = "W"

    # Test ValueError (uncastable string)
    assert sensor._process_numeric_value("invalid_float") == "invalid_float"

    # Test TypeError (uncastable object)
    assert sensor._process_numeric_value({"a": 1}) == {"a": 1}
    assert sensor._process_numeric_value([1, 2]) == [1, 2]


def test_log_glitch_once(base_sensor):
    """Verify that _log_glitch_once logs a value only once per distinct value,
    defaulting to DEBUG level (callers like the enum out-of-range guard can
    override via `level=`)."""
    sensor, _ = base_sensor

    with patch("custom_components.hyxi_cloud.sensor._LOGGER.log") as mock_log:
        # First time with value 123.4
        sensor._log_glitch_once(123.4, "Test glitch %s", 123.4)
        mock_log.assert_called_once_with(logging.DEBUG, "Test glitch %s", 123.4)
        assert sensor._last_logged_glitch == 123.4
        mock_log.reset_mock()

        # Second time with same value 123.4 - should NOT log
        sensor._log_glitch_once(123.4, "Test glitch %s", 123.4)
        mock_log.assert_not_called()
        assert sensor._last_logged_glitch == 123.4

        # Third time with a DIFFERENT value 123.5 - should log again
        sensor._log_glitch_once(123.5, "Test glitch %s", 123.5)
        mock_log.assert_called_once_with(logging.DEBUG, "Test glitch %s", 123.5)
        assert sensor._last_logged_glitch == 123.5

        # A non-default level (e.g. WARNING) is passed straight through.
        mock_log.reset_mock()
        sensor._log_glitch_once("9", "Test warning %s", "9", level=logging.WARNING)
        mock_log.assert_called_once_with(logging.WARNING, "Test warning %s", "9")
        assert sensor._last_logged_glitch == "9"


@pytest.fixture
def enum_status_sensor():
    """Fixture to create an ENUM-classed sensor for testing the
    out-of-range guard in _update_native_value."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"status": "2"}}}
    description = MagicMock()
    description.key = "status"
    description.native_unit_of_measurement = None
    description.device_class = sensor_mod.SensorDeviceClass.ENUM
    description.options = ["0", "1", "2"]

    sensor = sensor_mod.HyxiSensor(coordinator, "SN123", description)
    sensor.hass = None
    return sensor, coordinator


def test_enum_value_in_options_passes_through(enum_status_sensor):
    """A value present in `options` is reported as-is, without logging."""
    sensor, coordinator = enum_status_sensor
    coordinator.data["SN123"]["metrics"]["status"] = "1"

    with patch.object(sensor, "_log_glitch_once") as mock_log_once:
        sensor._handle_coordinator_update()

    assert sensor.native_value == "1"
    mock_log_once.assert_not_called()


def test_enum_value_out_of_range_reports_unknown(enum_status_sensor):
    """A value outside `options` is reported as None (unknown) and logged
    once at WARNING, instead of being passed through to a state HA's real
    SensorEntity.state would reject."""
    sensor, coordinator = enum_status_sensor
    coordinator.data["SN123"]["metrics"]["status"] = "9"

    with patch.object(sensor, "_log_glitch_once") as mock_log_once:
        sensor._handle_coordinator_update()

    assert sensor.native_value is None
    mock_log_once.assert_called_once()
    args, kwargs = mock_log_once.call_args
    assert args[0] == "9"
    assert kwargs.get("level") == logging.WARNING


def test_enum_out_of_range_warns_again_after_a_valid_reading(enum_status_sensor):
    """A bad value that recurs after a valid reading in between must warn
    again, not stay silenced by the dedup marker from its first occurrence."""
    sensor, coordinator = enum_status_sensor

    coordinator.data["SN123"]["metrics"]["status"] = "9"
    with patch.object(sensor, "_log_glitch_once") as mock_log_once:
        sensor._handle_coordinator_update()
    mock_log_once.assert_called_once()

    coordinator.data["SN123"]["metrics"]["status"] = "2"
    sensor._handle_coordinator_update()
    assert sensor.native_value == "2"

    coordinator.data["SN123"]["metrics"]["status"] = "9"
    with patch.object(sensor, "_log_glitch_once") as mock_log_once:
        sensor._handle_coordinator_update()
    mock_log_once.assert_called_once()


def test_enum_empty_options_accepts_any_value(enum_status_sensor):
    """`options=[]` means no constraint, matching the truthiness check HA's
    own SensorEntity.state uses (`if options and value not in options`) --
    regression test for using `if options` rather than `is not None`."""
    sensor, coordinator = enum_status_sensor
    sensor.entity_description.options = []
    coordinator.data["SN123"]["metrics"]["status"] = "anything"

    with patch.object(sensor, "_log_glitch_once") as mock_log_once:
        sensor._handle_coordinator_update()

    assert sensor.native_value == "anything"
    mock_log_once.assert_not_called()


def test_observed_undocumented_enum_values_are_declared_not_hidden():
    """invSts=6 and currentOperatingMode=13/14/15/16 are undocumented but
    observed on real hybrid inverter hardware. Declared with no state
    translation -- shown as the raw number rather than "unknown" --
    without guessing what any of them actually means."""
    invsts = sensor_mod.SENSOR_TYPES_BY_KEY["invSts"]
    assert invsts.options == ["0", "1", "2", "3", "4", "6"]

    mode = sensor_mod.SENSOR_TYPES_BY_KEY["currentOperatingMode"]
    assert mode.options == ["1", "2", "3", "4", "5", "6", "7", "13", "14", "15", "16"]


@pytest.mark.asyncio
async def test_base_sensor_added_to_hass_invalid_restoration():
    """Verify that HyxiBaseSensor handles TypeError and fallback to entity_id."""
    coordinator = MagicMock()
    sensor = sensor_mod.HyxiBaseSensor(coordinator)

    # Manually configure the sensor attributes
    description = MagicMock()
    description.key = "totalE"
    description.state_class = "total_increasing"
    sensor.entity_description = description
    sensor.entity_id = "sensor.hyxi_test_sensor"
    sensor.hass = MagicMock()

    # Mock last state with a non-floatable value to trigger ValueError/TypeError
    last_state = MagicMock()
    last_state.state = "not-a-number"
    sensor.async_get_last_state = AsyncMock(return_value=last_state)

    with patch("custom_components.hyxi_cloud.sensor._LOGGER.debug") as mock_debug:
        await sensor.async_added_to_hass()

        # Verify that _last_valid_value is None
        assert sensor._last_valid_value is None

        # Verify the debug message used entity_id
        mock_debug.assert_called_once_with(
            "HYXI Restore: Could not parse restored state '%s' for %s",
            "not-a-number",
            "sensor.hyxi_test_sensor",
        )


def test_anti_spike_direct_call(base_sensor):
    """Directly test _check_anti_spike logic and coverage."""
    from datetime import timedelta

    import homeassistant.util.dt as dt_util

    sensor, _ = base_sensor

    # Initialize _last_valid_value and time
    sensor._last_valid_value = 100.0
    sensor._last_valid_time = dt_util.utcnow()

    # Valid jump <= 100.0 returns None (meaning let it through)
    assert sensor._check_anti_spike(200.0) is None

    # Invalid jump > 100.0 returns _last_valid_value and logs glitch
    with patch.object(sensor, "_log_glitch_once") as mock_log:
        assert sensor._check_anti_spike(200.1) == 100.0
        mock_log.assert_called_once_with(
            200.1,
            "HYXI High-Spike Filter: Ignoring impossible jump on %s from %s to %s (max allowed: %s)",
            sensor.entity_description.key,
            100.0,
            200.1,
            100.0,
        )

    # If the last update was a long time ago (e.g. 3 hours), a larger jump is allowed
    sensor._last_valid_time = dt_util.utcnow() - timedelta(hours=3)
    # Allowed jump is 100.0 + 50.0 * 3 = 250.0. A jump of 249.0 should be allowed.
    assert sensor._check_anti_spike(349.0) is None
    # A jump of 251.0 should still be blocked
    assert sensor._check_anti_spike(351.0) == 100.0


def test_anti_dip_direct_call(base_sensor):
    """Directly test _check_anti_dip logic and coverage."""
    sensor, _ = base_sensor

    # Initialize _last_valid_value
    sensor._last_valid_value = 100.0

    # Test valid reset (new value is practically zero AND drop is > 50%)
    # This covers the `return None` path at the end of the method
    assert sensor._check_anti_dip(0.0) is None


def test_process_numeric_value_anti_spike(base_sensor):
    """Test the return path for _check_anti_spike inside _process_numeric_value."""
    import homeassistant.util.dt as dt_util

    sensor, _ = base_sensor

    # Seed an existing valid value and time
    sensor._last_valid_value = 100.0
    sensor._last_valid_time = dt_util.utcnow()

    # Pass a value that creates a spike > 100.0
    # _process_numeric_value handles rounding internally, so 200.11 will trigger the spike block
    result = sensor._process_numeric_value(200.11)

    assert result == 100.0


@pytest.mark.asyncio
async def test_async_setup_entry_null_string_filtering():
    """Verify that metrics with 'null' or 'NA' strings are filtered out during registration."""
    from custom_components.hyxi_cloud.const import DOMAIN

    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {}

    coordinator = MagicMock()
    # We provide one valid metric and several 'null' equivalent ones
    coordinator.data = {
        "COLL123": {
            "device_type_code": "COLLECTOR",
            "metrics": {
                "pvPower": "1200.5",  # Valid
                "invSts": "null",  # Should be filtered
                "gridSts": "NA",  # Should be filtered
            },
        }
    }
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    registered_entities = []

    def mock_async_add_entities(entities):
        registered_entities.extend(entities)

    await sensor_mod.async_setup_entry(hass, entry, mock_async_add_entities)

    registered_keys = [
        getattr(entity.entity_description, "key", None)
        for entity in registered_entities
        if hasattr(entity, "entity_description")
    ]

    # Verify 'pvPower' is there but 'invSts', 'gridSts', etc., are NOT
    assert "pvPower" in registered_keys
    assert "invSts" not in registered_keys
    assert "gridSts" not in registered_keys


@pytest.mark.asyncio
async def test_new_telemetry_keys_registration_and_parsing():
    """Verify that all 29 new telemetry/Micro ESS sensors are registered and cast correctly."""
    from custom_components.hyxi_cloud.const import DOMAIN

    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {}

    # Mock coordinator with all 29 new metrics
    coordinator = MagicMock()
    coordinator.data = {
        "INV123": {
            "device_type_code": "HYBRID_INVERTER",
            "device_name": "My Inverter",
            "metrics": {
                "invSts": "2",  # enum state -> Alarm (cast as integer)
                "faultSts": "1",  # enum state -> Fault (cast as integer)
                "gridSts": "0",  # enum state -> Normal (cast as integer)
                "deviceGridConn": "1",  # enum state -> On Grid (cast as integer)
                "deviceSwitchStatus": "0",  # enum state -> Shutdown (cast as integer)
                "pvPower": "1200.5",  # float
                "pvNum": "4",  # integer
                "acSideTemper": "45.2",  # float
                "dcSideTemper": "50.1",  # float
                "gridF": "50.02",  # float
                "gridP": "800.0",  # float
                "gridQ": "-200.0",  # float
                "gridPfd": "0.95",  # float
                "gridAp": "850.0",  # float
                "offGridF": "50.00",  # float
                "offGridP": "0.0",  # float
                "offGridQ": "0.0",  # float
                "offGridPfd": "1.0",  # float
                "offGridAp": "0.0",  # float
                "batVch": "3.45",  # float (battery)
                "batVcl": "3.21",  # float (battery)
                "batTch": "28.5",  # float (battery)
                "batTcl": "22.1",  # float (battery)
                "batIcm": "50.0",  # float (battery)
                "batIdm": "100.0",  # float (battery)
                "bat_charge_total": "1500.5",  # float (battery)
                "bat_discharge_total": "1200.2",  # float (battery)
                "batP": "150.7",  # float (battery)
                "ratedFrequency": "50",  # integer (from queryDeviceInfo)
                "batSn": "BAT_REAL_123",
            },
        }
    }
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    registered_entities = []

    def mock_async_add_entities(entities):
        registered_entities.extend(entities)

    await sensor_mod.async_setup_entry(hass, entry, mock_async_add_entities)

    registered_keys = []
    registered_by_key = {}
    for entity in registered_entities:
        if hasattr(entity, "entity_description"):
            key = entity.entity_description.key
            registered_keys.append(key)
            registered_by_key[key] = entity

    # Check that all 29 keys + ratedFrequency are registered
    expected_new_keys = [
        "invSts",
        "faultSts",
        "gridSts",
        "deviceGridConn",
        "deviceSwitchStatus",
        "pvPower",
        "pvNum",
        "acSideTemper",
        "dcSideTemper",
        "gridF",
        "gridP",
        "gridQ",
        "gridPfd",
        "gridAp",
        "offGridF",
        "offGridP",
        "offGridQ",
        "offGridPfd",
        "offGridAp",
        "batVch",
        "batVcl",
        "batTch",
        "batTcl",
        "batIcm",
        "batIdm",
        "bat_charge_total",
        "bat_discharge_total",
        "batP",
        "ratedFrequency",
    ]

    for key in expected_new_keys:
        assert key in registered_keys, f"{key} was not registered as a sensor"

    # Verify enum and integer casting of metrics
    assert registered_by_key["invSts"].native_value == "2"
    assert registered_by_key["faultSts"].native_value == "1"
    assert registered_by_key["gridSts"].native_value == "0"
    assert registered_by_key["deviceGridConn"].native_value == "1"
    assert registered_by_key["deviceSwitchStatus"].native_value == "0"
    assert registered_by_key["ratedFrequency"].native_value == 50

    assert registered_by_key["pvPower"].native_value == 1200.5
    assert registered_by_key["pvNum"].native_value == 4
    assert registered_by_key["acSideTemper"].native_value == 45.2
    assert registered_by_key["dcSideTemper"].native_value == 50.1
    assert registered_by_key["batP"].native_value == 150.7

    # Battery sensors: identified by the inverter serial, grouped under the
    # battery device.
    battery_keys = [
        "batVch",
        "batVcl",
        "batTch",
        "batTcl",
        "batIcm",
        "batIdm",
        "bat_charge_total",
        "bat_discharge_total",
        "batP",
    ]
    for key in battery_keys:
        sensor_entity = registered_by_key[key]
        assert sensor_entity._attr_unique_id == f"hyxi_INV123_{key}"
        assert sensor_entity.device_info["identifiers"] == {
            ("hyxi_cloud", "BAT_REAL_123")
        }


# --- EMSensor and EM platform tests ---


@pytest.mark.asyncio
async def test_em_sensor_lifecycle():
    """Verify EMSensor lifecycle, callbacks, and value getters."""
    coordinator = MagicMock()
    coordinator.engine = None

    device_info = MagicMock()
    sensor_def = sensor_mod.EMSensorDef(
        key="p1_average",
        device_info=device_info,
        unit="W",
    )

    # 1. Init
    sensor = sensor_mod.EMSensor(coordinator, "SN123", sensor_def)
    assert sensor._attr_unique_id == "hyxi_SN123_em_p1_average"

    # 2. Added to HASS without engine
    await sensor.async_added_to_hass()

    # 3. Added to HASS with engine
    mock_engine = MagicMock()
    coordinator.engine = mock_engine
    await sensor.async_added_to_hass()
    mock_engine.register_update_callback.assert_called_once_with(sensor._engine_updated)

    # 4. Engine updated triggers state write
    sensor.hass = MagicMock()
    sensor.async_write_ha_state = MagicMock()
    sensor._engine_updated()
    sensor.async_write_ha_state.assert_called_once()

    # 5. Native value lookup: engine is None
    coordinator.engine = None
    assert sensor.native_value is None

    # 6. Native value lookup: valid value from engine
    coordinator.engine = mock_engine
    mock_engine.p1_avg = 123.456
    assert sensor.native_value == 123.5

    # 7. Native value lookup: non-float value
    sensor._key = "status"
    mock_engine.status = "running"
    assert sensor.native_value == "running"

    # 8. Native value lookup: key with no entry in _VALUE_GETTERS
    sensor._key = "not_a_real_key"
    assert sensor.native_value is None

    # 9. Native value lookup: getter maps to a callable (method), not a plain
    # attribute -- e.g. battery_energy_available_wh() is a method on the real
    # engine, unlike decision/status/p1_avg which are properties.
    sensor._key = "battery_energy_available"
    mock_engine.battery_energy_available_wh = MagicMock(return_value=456.78)
    assert sensor.native_value == 456.8
    mock_engine.battery_energy_available_wh.assert_called_once()

    # 10. Will remove from HASS
    await sensor.async_will_remove_from_hass()
    mock_engine.unregister_update_callback.assert_called_once_with(
        sensor._engine_updated
    )


@pytest.mark.asyncio
async def test_hyxi_last_sent_mode_sensor_lifecycle():
    """Verify HyxiLastSentModeSensor added to HASS and state restoration."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"device_name": "Test Inverter", "model": "H5K-HT"}}

    # 1. Init
    sensor = sensor_mod.HyxiLastSentModeSensor(coordinator, "SN123")
    assert sensor._attr_unique_id == "hyxi_SN123_last_sent_mode"
    assert sensor.device_info["name"] == "Test Inverter"

    # 2. Added to HASS: no state to restore
    sensor.async_get_last_state = AsyncMock(return_value=None)
    await sensor.async_added_to_hass()

    # 2b. Added to HASS: restored state is a placeholder (unknown/unavailable/
    # empty), not a real mode -- must not be replayed to the controller.
    coordinator.protection_controllers = {"SN123": MagicMock()}
    for placeholder in ("unknown", "unavailable", ""):
        placeholder_state = MagicMock()
        placeholder_state.state = placeholder
        sensor.async_get_last_state = AsyncMock(return_value=placeholder_state)
        await sensor.async_added_to_hass()
        coordinator.protection_controllers[
            "SN123"
        ].restore_last_sent_mode.assert_not_called()

    # 3. Added to HASS: restore state, no controller
    last_state = MagicMock()
    last_state.state = "idle"
    sensor.async_get_last_state = AsyncMock(return_value=last_state)
    coordinator.protection_controllers = {}
    await sensor.async_added_to_hass()

    # 4. Added to HASS: restore state with controller
    mock_controller = MagicMock()
    coordinator.protection_controllers = {"SN123": mock_controller}
    await sensor.async_added_to_hass()
    mock_controller.restore_last_sent_mode.assert_called_once_with("idle")

    # 5. Native value lookup
    mock_controller.last_sent_mode = "charge"
    assert sensor.native_value == "charge"

    # 6. Native value lookup: no controller
    coordinator.protection_controllers = {}
    assert sensor.native_value is None


def test_hyxi_subscription_status_sensor():
    """Verify combined subscription status logic and attributes."""
    coordinator = MagicMock()
    coordinator.push_status = "active"
    coordinator.push_url = "http://push.url"
    coordinator.subscribe_code = "123"
    coordinator.push_error = None
    coordinator.last_push_received = None

    coordinator.alarm_push_status = "active"
    coordinator.alarm_push_url = "http://alarm.url"
    coordinator.alarm_subscribe_code = "456"
    coordinator.alarm_push_error = None
    coordinator.alarm_last_push_received = None

    entry = MagicMock()
    entry.entry_id = "entry_id"
    entry.options = {"push_rate": 60}

    sensor = sensor_mod.HyxiSubscriptionStatusSensor(coordinator, entry)

    # 1. Combined active
    assert sensor.native_value == "active"

    # 2. Combined partial
    coordinator.alarm_push_status = "inactive"
    sensor._update_value()
    assert sensor.native_value == "partial"

    # 3. Combined inactive
    coordinator.push_status = "inactive"
    sensor._update_value()
    assert sensor.native_value == "inactive"

    # 4. Combined error
    coordinator.push_status = "error"
    sensor._update_value()
    assert sensor.native_value == "error"

    # 5. Extra state attributes
    import datetime

    dt = datetime.datetime(2026, 6, 2, 8, 0, 0, tzinfo=datetime.UTC)
    coordinator.last_push_received = dt
    coordinator.alarm_last_push_received = dt

    attrs = sensor.extra_state_attributes
    assert attrs["data_push"]["status"] == "error"
    assert attrs["data_push"]["last_push_received"] == dt.isoformat()
    assert attrs["alarm_push"]["status"] == "inactive"
    assert attrs["alarm_push"]["last_push_received"] == dt.isoformat()
    assert attrs["alarm_push"]["error"] is None

    # 6. Alarm push failure surfaces its own error message, independently of
    # data push -- previously alarm_push had no error key at all.
    coordinator.alarm_push_error = "request failed (code=B004002): ..."
    attrs = sensor.extra_state_attributes
    assert attrs["alarm_push"]["error"] == "request failed (code=B004002): ..."


def test_hyxi_sensor_advanced_mappings(base_sensor):
    """Verify via_device linkage and fallback in HyxiSensor.

    acP is asserted to pass through unmodified: it used to be adjusted by
    subtracting an "acl" metric and scaling by 0.96, but that had no
    verifiable basis (see the comment above _parse_device_type in
    sensor.py) and was removed.
    """
    sensor, coordinator = base_sensor

    # Setup inverter with extra metrics
    coordinator.data = {
        "INV123": {
            "device_name": "Test Inverter",
            "model": "H5K-HT",
            "metrics": {
                "acP": 150.0,
                "parentSn": "COLLECTOR_123",
            },
        }
    }

    # 1. Parent Sn via_device link
    sensor.entity_description.key = "acP"
    sensor._sn = "INV123"
    sensor._dev_data = coordinator.data["INV123"]
    sensor._metrics = sensor._dev_data["metrics"]

    assert sensor.device_info["via_device"] == ("hyxi_cloud", "COLLECTOR_123")

    # 1a. acP actually passes through unmodified -- via _handle_coordinator_update
    # so _parser_func/native_value are recomputed the same way a real
    # coordinator refresh would, not just via_device's dict lookup above.
    sensor._parser_func = sensor._parse_default
    sensor._last_valid_value = None  # Reset baseline
    sensor._handle_coordinator_update()
    assert sensor.native_value == 150.0

    # 2. Micro Inverter fallback: acE is None or 0.0 -> uses efpv
    sensor.entity_description.key = "acE"
    sensor._device_type = "micro_inverter"
    sensor._parser_func = sensor._parse_default

    # acE is 0.0, efpv is 12.34
    sensor._metrics["acE"] = 0.0
    sensor._metrics["efpv"] = 12.34
    sensor._last_valid_value = None  # Reset baseline
    sensor._handle_coordinator_update()
    assert sensor.native_value == 12.34


def test_same_quantity_fallback_table(base_sensor):
    """Verify the _SAME_QUANTITY_FALLBACKS-driven substitutions for gridF
    and batTmp: fires only when the primary key is missing/null and only
    on the intended device types, and -- unlike acE -- does *not* treat
    0.0 as missing (gridF/batTmp entries leave treat_zero_as_null unset).
    """
    sensor, coordinator = base_sensor
    sensor._parser_func = sensor._parse_default
    sensor._sn = "INV123"
    coordinator.data = {"INV123": {"device_name": "Test Inverter", "metrics": {}}}
    metrics = coordinator.data["INV123"]["metrics"]

    def set_and_read(key, device_type, values):
        sensor.entity_description.key = key
        sensor._device_type = device_type
        metrics.clear()
        metrics.update(values)
        sensor._last_valid_value = None  # Reset anti-dip/spike baseline
        sensor._handle_coordinator_update()
        return sensor.native_value

    # gridF missing -> falls back to "f", but only on the intended device
    # types (grid_connected_inverter/micro_inverter).
    assert set_and_read("gridF", "micro_inverter", {"f": 50.02}) == 50.02
    assert set_and_read("gridF", "hybrid_inverter", {"f": 50.02}) is None

    # gridF present as 0.0 -> NOT treated as missing (unlike acE).
    assert set_and_read("gridF", "micro_inverter", {"gridF": 0.0, "f": 50.02}) == 0.0

    # batTmp missing -> falls back to "batTch", but only on the intended
    # device types (hybrid_inverter/all_in_one).
    assert set_and_read("batTmp", "hybrid_inverter", {"batTch": 28.5}) == 28.5
    assert set_and_read("batTmp", "micro_inverter", {"batTch": 28.5}) is None

    # batTmp present as 0.0 -> NOT treated as missing (unlike acE).
    assert (
        set_and_read("batTmp", "hybrid_inverter", {"batTmp": 0.0, "batTch": 28.5})
        == 0.0
    )


@pytest.mark.asyncio
async def test_async_setup_entry_em_and_battery_options(monkeypatch):
    """Verify async_setup_entry registers EM and last_sent_mode sensors when enabled."""
    from custom_components.hyxi_cloud.const import DOMAIN

    # Stub dependencies directly on the module dictionary to bypass MagicMock discrepancies
    monkeypatch.setattr(sensor_mod, "is_battery_control_enabled", lambda entry: True)
    monkeypatch.setattr(sensor_mod, "detect_phase_type", lambda d: "three_phase")
    monkeypatch.setattr(sensor_mod, "CONF_EM_ENABLED", "em_enabled")
    monkeypatch.setattr(sensor_mod, "CONF_EM_INVERTER_SN", "em_inverter_sn")

    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {
        "em_enabled": True,
        "em_inverter_sn": "INV123",
        "enable_battery_control": True,
    }

    coordinator = MagicMock()
    coordinator.data = {
        "INV123": {
            "device_type_code": "1",  # HYBRID_INVERTER
            "model": "H5K-HT",
            "device_name": "Test Inverter",
            "metrics": {"batSoc": 50},
        }
    }
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    registered_entities = []

    def mock_async_add_entities(entities):
        registered_entities.extend(entities)

    # We mock _LOGGER.isEnabledFor(logging.DEBUG) to True to cover that block
    with patch(
        "custom_components.hyxi_cloud.sensor._LOGGER.isEnabledFor", return_value=True
    ):
        await sensor_mod.async_setup_entry(hass, entry, mock_async_add_entities)

    registered_keys = []
    for entity in registered_entities:
        if hasattr(entity, "entity_description"):
            registered_keys.append(entity.entity_description.key)
        elif hasattr(entity, "_key"):
            registered_keys.append(entity._key)
        elif hasattr(entity, "_attr_unique_id"):
            registered_keys.append(entity._attr_unique_id)

    # EM sensors should be registered
    assert "p1_average" in registered_keys
    # last_sent_mode sensor should be registered
    assert "hyxi_INV123_last_sent_mode" in registered_keys


# --- HyxiBatteryEnergyPeriodSensor ----------------------------------------


def _period_sensor(direction="charge", period="today", metrics=None):
    coordinator = MagicMock()
    coordinator.data = {"INV1": {"metrics": dict(metrics or {})}}
    return sensor_mod.HyxiBatteryEnergyPeriodSensor(
        coordinator, "INV1", direction, period
    )


def _at(sensor, moment):
    with patch("custom_components.hyxi_cloud.sensor.dt_util.now", return_value=moment):
        sensor._recompute()


@pytest.mark.parametrize(
    ("period", "expected_month", "expected_day"),
    [("today", 9, 3), ("week", 8, 31), ("month", 9, 1), ("year", 1, 1)],
)
def test_period_start_boundaries(period, expected_month, expected_day):
    from datetime import UTC

    moment = datetime(2026, 9, 3, 14, 30, tzinfo=UTC)
    start = sensor_mod._period_start(moment, period)
    assert (start.month, start.day) == (expected_month, expected_day)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)


def test_period_sensor_derives_delta_from_lifetime_counter():
    from datetime import UTC

    sensor = _period_sensor(metrics={"bat_charge_total": 100.0})
    _at(sensor, datetime(2026, 9, 3, 12, 0, tzinfo=UTC))
    assert sensor.native_value == 0.0  # anchored on the first read

    sensor.coordinator.data["INV1"]["metrics"]["bat_charge_total"] = 104.5
    _at(sensor, datetime(2026, 9, 3, 18, 0, tzinfo=UTC))
    assert sensor.native_value == 4.5


def test_period_sensor_rolls_over_at_the_period_boundary():
    from datetime import UTC

    sensor = _period_sensor(period="today", metrics={"bat_charge_total": 100.0})
    _at(sensor, datetime(2026, 9, 3, 23, 0, tzinfo=UTC))

    sensor.coordinator.data["INV1"]["metrics"]["bat_charge_total"] = 108.0
    _at(sensor, datetime(2026, 9, 4, 1, 0, tzinfo=UTC))
    # Re-anchored to the last pre-boundary reading (100), not the first one
    # after (108), so the 8 kWh gained across the gap isn't dropped.
    assert sensor._anchor == 100.0
    assert sensor.native_value == 8.0


def test_period_sensor_prefers_the_device_daily_counter():
    from datetime import UTC

    sensor = _period_sensor(
        period="today",
        metrics={"bat_charge_total": 900.0, "bat_charge_today": 6.2},
    )
    _at(sensor, datetime(2026, 9, 3, 12, 0, tzinfo=UTC))
    assert sensor.native_value == 6.2
    assert sensor._anchor is None  # derivation path never taken


def test_period_sensor_week_ignores_the_device_daily_counter():
    from datetime import UTC

    sensor = _period_sensor(
        direction="discharge",
        period="week",
        metrics={"bat_discharge_total": 50.0, "bat_discharge_today": 3.0},
    )
    _at(sensor, datetime(2026, 9, 3, 12, 0, tzinfo=UTC))
    assert sensor.native_value == 0.0  # derived from the lifetime counter


def test_period_sensor_survives_a_single_spurious_low_reading():
    from datetime import UTC

    sensor = _period_sensor(metrics={"bat_charge_total": 40.0})
    _at(sensor, datetime(2026, 9, 3, 12, 0, tzinfo=UTC))
    sensor.coordinator.data["INV1"]["metrics"]["bat_charge_total"] = 44.0
    _at(sensor, datetime(2026, 9, 3, 12, 5, tzinfo=UTC))
    assert sensor.native_value == 4.0

    # One near-zero sample then a recovery -- must NOT re-anchor (CodeRabbit
    # 40 -> 0.5 -> 40.5 case), or the recovery reads as 40 kWh of "new" energy.
    sensor.coordinator.data["INV1"]["metrics"]["bat_charge_total"] = 0.5
    _at(sensor, datetime(2026, 9, 3, 12, 10, tzinfo=UTC))
    assert sensor.native_value == 0.0
    assert sensor._anchor == 40.0
    sensor.coordinator.data["INV1"]["metrics"]["bat_charge_total"] = 44.5
    _at(sensor, datetime(2026, 9, 3, 12, 15, tzinfo=UTC))
    assert sensor.native_value == 4.5


def test_period_sensor_reanchors_on_a_confirmed_counter_reset():
    from datetime import UTC

    sensor = _period_sensor(metrics={"bat_charge_total": 100.0})
    _at(sensor, datetime(2026, 9, 3, 12, 0, tzinfo=UTC))

    # Near-zero for two consecutive reads -> real reset (battery swap).
    for minute in (5, 10):
        sensor.coordinator.data["INV1"]["metrics"]["bat_charge_total"] = 0.3
        _at(sensor, datetime(2026, 9, 3, 12, minute, tzinfo=UTC))
    assert sensor._anchor == 0.3
    sensor.coordinator.data["INV1"]["metrics"]["bat_charge_total"] = 1.5
    _at(sensor, datetime(2026, 9, 3, 12, 15, tzinfo=UTC))
    assert sensor.native_value == 1.2


def test_period_sensor_is_unknown_without_a_source_value():
    from datetime import UTC

    sensor = _period_sensor(metrics={})
    _at(sensor, datetime(2026, 9, 3, 12, 0, tzinfo=UTC))
    assert sensor.native_value is None


def test_period_sensor_ignores_non_numeric_and_non_finite_values():
    from datetime import UTC

    for bad in ("n/a but not null", "inf", "nan", "-inf"):
        sensor = _period_sensor(metrics={"bat_charge_total": bad})
        _at(sensor, datetime(2026, 9, 3, 12, 0, tzinfo=UTC))
        assert sensor.native_value is None, bad


def test_period_sensor_enabled_by_default_only_for_today_and_month():
    enabled = {
        p: _period_sensor(period=p)._attr_entity_registry_enabled_default
        for p in ("today", "week", "month", "year")
    }
    assert enabled == {"today": True, "week": False, "month": True, "year": False}


def _hyxi_sensor(key, metrics):
    coordinator = MagicMock()
    coordinator.data = {"INV1": {"metrics": dict(metrics)}}
    description = MagicMock()
    description.key = key
    description.translation_key = None
    description.native_unit_of_measurement = "kWh"
    description.state_class = "total_increasing"
    return sensor_mod.HyxiSensor(coordinator, "INV1", description)


def test_etodayin_demoted_when_grid_import_today_present():
    """On the hybrid Modbus day block grid_import_today shadows eTodayIn,
    so eTodayIn ships disabled there."""
    demoted = _hyxi_sensor("eTodayIn", {"eTodayIn": "2.0", "grid_import_today": "3.0"})
    assert demoted._attr_entity_registry_enabled_default is False


def test_etodayin_stays_default_without_grid_import_today():
    """HALO reports eTodayIn but never grid_import_today -- it stays on."""
    kept = _hyxi_sensor("eTodayIn", {"eTodayIn": "2.0"})
    assert not hasattr(kept, "_attr_entity_registry_enabled_default")
    # grid_import_today itself is not demoted by anything.
    grid = _hyxi_sensor("grid_import_today", {"grid_import_today": "3.0"})
    assert not hasattr(grid, "_attr_entity_registry_enabled_default")

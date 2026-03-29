"""Tests for the Hyxi Cloud sensor entity logic."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel
import importlib
import sys
from datetime import datetime
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


# 1. THE BULLETPROOF MOCK
class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


class FakeSensorEntity(FakeBase):
    pass


class FakeRestoreEntity(FakeBase):
    async def async_added_to_hass(self):
        pass


# Create a mock homeassistant environment BEFORE importing integration code
mock_ha = MagicMock()
mock_ha.__name__ = "mock_ha"
mock_ha.__path__ = []  # IMPORTANT for nested module resolution
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha

# Also ensure hyxi_cloud_api has __version__ even if it's mocked
mock_api = MagicMock()
mock_api.__name__ = "hyxi_cloud_api"
mock_api.__version__ = "1.0.4"
sys.modules["hyxi_cloud_api"] = mock_api

# We need SensorEntityDescription to retain its attributes instead of being a generic mock
mock_sensor = MagicMock()


def mock_sensor_entity_description(**kwargs):
    desc = MagicMock()
    for k, v in kwargs.items():
        setattr(desc, k, v)
    return desc


mock_sensor.SensorEntityDescription = mock_sensor_entity_description
mock_sensor.SensorEntity = FakeSensorEntity
mock_sensor.SensorDeviceClass = MagicMock()
mock_sensor.SensorStateClass = MagicMock()

sys.modules["homeassistant.components.sensor"] = mock_sensor

# Other mocked dependencies
mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity  # Keep this from original

mock_restore = MagicMock()
mock_restore.RestoreEntity = FakeRestoreEntity

sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.restore_state"] = mock_restore
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator
sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
sys.modules["homeassistant.util"] = mock_ha
sys.modules["aiohttp"] = MagicMock()

# Standardize import style to resolve code scanning alert no. 50
import custom_components.hyxi_cloud.const as const_mod  # noqa: E402
import custom_components.hyxi_cloud.sensor as sensor_mod  # noqa: E402

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
    assert mask_sn("1234567") == "****"

    # Exact length of 8
    assert mask_sn("12345678") == "123XX678"

    # Longer string
    assert mask_sn("1234567890") == "123XXXX890"

    # Integer values
    assert mask_sn(12345678) == "123XX678"


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


def test_anti_spike_prevention(base_sensor):
    """Verify that jumps greater than 100.0 are blocked."""
    sensor, coordinator = base_sensor

    # Baseline
    assert sensor.native_value == 2742.0

    # 📈 Valid jump <= 100.0 (allowed)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2842.0
    assert sensor.native_value == 2842.0

    # 🚀 Invalid spike > 100.0 (blocked, returns last valid value)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2943.0
    assert sensor.native_value == 2842.0

    # 📉 Small increase after spike (allowed)
    coordinator.data["SN123"]["metrics"]["totalE"] = 2850.0
    assert sensor.native_value == 2850.0


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


def test_collecttime_error_handling(base_sensor):
    """Verify that invalid collectTime values are caught and return None."""
    sensor, coordinator = base_sensor
    sensor.entity_description.key = "collectTime"

    # Test ValueError (unparseable string)
    coordinator.data["SN123"]["metrics"]["collectTime"] = "invalid_timestamp"
    assert sensor.native_value is None

    # Test TypeError (invalid type like dict or list)
    coordinator.data["SN123"]["metrics"]["collectTime"] = {"time": 123}
    assert sensor.native_value is None

    # Test extreme value causing OverflowError/OSError in datetime.fromtimestamp
    # A huge number that passes the 10-digit check but is still too large for datetime
    coordinator.data["SN123"]["metrics"]["collectTime"] = 1000000000000000000
    assert sensor.native_value is None

    # Test extreme overflow value (triggering OverflowError on many platforms)
    coordinator.data["SN123"]["metrics"]["collectTime"] = 10**25
    assert sensor.native_value is None

    # Test OSError explicitly by patching datetime since OverflowError is now ValueError in Python 3.12+
    with patch("custom_components.hyxi_cloud.sensor.datetime") as mock_dt:
        mock_dt.fromtimestamp.side_effect = OSError("mocked OSError")
        coordinator.data["SN123"]["metrics"]["collectTime"] = 1234567890
        assert sensor.native_value is None


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


def test_batsoc_batsoh_casting(base_sensor):
    """Verify batSoc and batSoh correctly cast to integers after rounding."""
    sensor, coordinator = base_sensor

    # Test batSoc
    sensor.entity_description.key = "batSoc"
    coordinator.data["SN123"]["metrics"]["batSoc"] = 85.6
    assert sensor.native_value == 86

    # Test batSoh
    sensor.entity_description.key = "batSoh"
    coordinator.data["SN123"]["metrics"]["batSoh"] = 99.1
    assert sensor.native_value == 99

    # Test invalid string gracefully handled
    coordinator.data["SN123"]["metrics"]["batSoh"] = "invalid"
    assert sensor.native_value is None

    # Test invalid type gracefully handled
    coordinator.data["SN123"]["metrics"]["batSoh"] = {"invalid": "dict"}
    assert sensor.native_value is None


@pytest.mark.asyncio
async def test_new_api_metrics_registration():
    """Verify that all new PV, Phase, Battery, and Status sensors instantiate correctly."""
    from custom_components.hyxi_cloud.const import DOMAIN  # noqa: E402

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


def test_sensor_int_conversion_non_numeric_string(base_sensor):
    """Test ValueError and TypeError handling specifically for INT_SENSOR_KEYS."""
    sensor, coordinator = base_sensor
    coordinator.data["SN123"]["metrics"]["batSoc"] = "100"

    # We choose one key from INT_SENSOR_KEYS
    sensor.entity_description.key = "batSoc"

    # String that raises ValueError on float() conversion
    coordinator.data["SN123"]["metrics"]["batSoc"] = "non_numeric_string"
    assert sensor.native_value is None

    # Object that raises TypeError on float() conversion
    coordinator.data["SN123"]["metrics"]["batSoc"] = {"unexpected": "object"}
    assert sensor.native_value is None


def test_float_conversion_error(base_sensor):
    """Verify that a non-numeric string gracefully falls back."""
    sensor, coordinator = base_sensor
    coordinator.data["SN123"]["metrics"]["totalE"] = "bad_data"
    assert sensor.native_value == "bad_data"


@pytest.mark.asyncio
async def test_sensor_added_to_hass_restoration():
    """Verify that HyxiSensor restores its last state on addition to Home Assistant."""
    coordinator = MagicMock()
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


@pytest.mark.asyncio
async def test_sensor_added_to_hass_no_restoration():
    """Verify that HyxiSensor handles missing last state gracefully."""
    coordinator = MagicMock()
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
    from datetime import UTC
    from datetime import datetime

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
    from datetime import UTC
    from datetime import datetime

    sensor, coordinator = base_sensor
    sensor.entity_description.key = "last_seen"

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
    """Verify that battery sensors use batSn if available (Line 586-587 coverage)."""
    coordinator = MagicMock()
    coordinator.data = {
        "INV123": {
            "metrics": {"batSoc": 50, "batSn": "BAT_REAL_123"},
            "device_name": "My Inverter",
        }
    }
    description = MagicMock()
    description.key = "batSoc"

    # This should hit the battery SN block
    sensor = sensor_mod.HyxiSensor(coordinator, "INV123", description)

    assert sensor._actual_sn == "BAT_REAL_123"
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
    """Verify that _log_glitch_once logs a glitch value only once."""
    sensor, _ = base_sensor

    with patch("custom_components.hyxi_cloud.sensor._LOGGER.debug") as mock_debug:
        # First time with value 123.4
        sensor._log_glitch_once(123.4, "Test glitch %s", 123.4)
        mock_debug.assert_called_once_with("Test glitch %s", 123.4)
        assert sensor._last_logged_glitch == 123.4
        mock_debug.reset_mock()

        # Second time with same value 123.4 - should NOT log
        sensor._log_glitch_once(123.4, "Test glitch %s", 123.4)
        mock_debug.assert_not_called()
        assert sensor._last_logged_glitch == 123.4

        # Third time with a DIFFERENT value 123.5 - should log again
        sensor._log_glitch_once(123.5, "Test glitch %s", 123.5)
        mock_debug.assert_called_once_with("Test glitch %s", 123.5)
        assert sensor._last_logged_glitch == 123.5


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

    # Mock last state with an uncastable object to trigger TypeError
    last_state = MagicMock()
    last_state.state = [1]
    sensor.async_get_last_state = AsyncMock(return_value=last_state)

    with patch("custom_components.hyxi_cloud.sensor._LOGGER.debug") as mock_debug:
        await sensor.async_added_to_hass()

        # Verify that _last_valid_value is None
        assert sensor._last_valid_value is None

        # Verify the debug message used entity_id
        mock_debug.assert_called_once_with(
            "HYXI Restore: Could not parse restored state '%s' for %s",
            [1],
            "sensor.hyxi_test_sensor",
        )


def test_anti_spike_direct_call(base_sensor):
    """Directly test _check_anti_spike logic and coverage."""
    sensor, _ = base_sensor

    # Initialize _last_valid_value
    sensor._last_valid_value = 100.0

    # Valid jump <= 100.0 returns None (meaning let it through)
    assert sensor._check_anti_spike(200.0) is None

    # Invalid jump > 100.0 returns _last_valid_value
    assert sensor._check_anti_spike(200.1) == 100.0


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
    sensor, _ = base_sensor

    # Seed an existing valid value
    sensor._last_valid_value = 100.0

    # Pass a value that creates a spike > 100.0
    # _process_numeric_value handles rounding internally, so 200.11 will trigger the spike block
    result = sensor._process_numeric_value(200.11)

    assert result == 100.0

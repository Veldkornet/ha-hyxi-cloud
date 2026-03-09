# pylint: disable=missing-module-docstring, unused-argument, wrong-import-position, redefined-outer-name, import-outside-toplevel
import importlib
import sys
from datetime import datetime
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


# Create a mock homeassistant environment BEFORE importing integration code
mock_ha = MagicMock()
mock_ha.__path__ = []  # IMPORTANT for nested module resolution
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha
sys.modules["hyxi_cloud_api"] = mock_ha

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
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator
sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
sys.modules["homeassistant.util"] = mock_ha
sys.modules["hyxi_cloud_api"] = mock_ha

import custom_components.hyxi_cloud.sensor  # noqa: E402

importlib.reload(custom_components.hyxi_cloud.sensor)

from custom_components.hyxi_cloud.sensor import HyxiSensor  # noqa: E402


@pytest.fixture
def base_sensor():
    """Fixture to create a standard energy sensor for testing."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"totalE": 2742.0}}}
    description = MagicMock()
    description.key = "totalE"
    description.native_unit_of_measurement = "kWh"
    description.state_class = "total_increasing"

    sensor = HyxiSensor(coordinator, "SN123", description)
    sensor.hass = None
    return sensor, coordinator


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


def test_virtual_battery_soc_soh_casting():
    """Verify that the virtual battery properly casts avg_soc and avg_soh to ints."""
    from custom_components.hyxi_cloud.sensor import HyxiBatterySystemSensor

    coordinator = MagicMock()
    # Mocking get_battery_summary rather than relying on internal raw data
    coordinator.get_battery_summary.return_value = {"avg_soc": 49.0, "avg_soh": 98.6}

    entry = MagicMock()
    entry.entry_id = "test_entry"

    # Test avg_soc
    desc_soc = MagicMock()
    desc_soc.key = "avg_soc"
    sensor_soc = HyxiBatterySystemSensor(coordinator, entry, desc_soc)
    sensor_soc.hass = None
    assert sensor_soc.native_value == 49

    # Test avg_soh
    desc_soh = MagicMock()
    desc_soh.key = "avg_soh"
    sensor_soh = HyxiBatterySystemSensor(coordinator, entry, desc_soh)
    sensor_soh.hass = None
    assert sensor_soh.native_value == 99

    # Test invalid string gracefully handled
    coordinator.get_battery_summary.return_value = {"avg_soh": "invalid"}
    assert sensor_soh.native_value is None


@pytest.mark.asyncio
async def test_new_api_metrics_registration():
    """Verify that all new PV, Phase, Battery, and Status sensors instantiate correctly."""
    from custom_components.hyxi_cloud.const import DOMAIN  # noqa: E402
    from custom_components.hyxi_cloud.sensor import async_setup_entry  # noqa: E402

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

    await async_setup_entry(hass, entry, mock_async_add_entities)

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
    from custom_components.hyxi_cloud.sensor import async_setup_entry

    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    coordinator = MagicMock()
    coordinator.data = {}
    hass.data = {DOMAIN: {"test_entry": coordinator}}

    mock_async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, mock_async_add_entities)

    # Should exit early and not add any entities if data is empty
    mock_async_add_entities.assert_not_called()

    # Also test None
    coordinator.data = None
    await async_setup_entry(hass, entry, mock_async_add_entities)

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

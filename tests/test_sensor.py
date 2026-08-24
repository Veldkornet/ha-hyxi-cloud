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
    # Allow CoordinatorEntity[HyxiDataUpdateCoordinator] subscripting in class bases
    __class_getitem__ = classmethod(lambda cls, item: cls)

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

    # Connection type/framing is a Modbus-only distinction -- a cloud entry
    # has none of its own to report.
    connection_type_sensors = [
        e for e in entities if isinstance(e, sensor_mod.HyxiModbusConnectionTypeSensor)
    ]
    assert len(connection_type_sensors) == 0

    # Check that device sensors are added
    device_sensors = [e for e in entities if isinstance(e, sensor_mod.HyxiSensor)]
    assert len(device_sensors) > 0

    # No microinverters in this plant -- aggregate sensors must not be created
    micro_sum_sensors = [
        e for e in entities if isinstance(e, sensor_mod.HyxiMicroinverterSumSensor)
    ]
    assert len(micro_sum_sensors) == 0


@pytest.mark.asyncio
async def test_async_setup_entry_modbus_skips_unread_pre_registered_keys():
    """Modbus has no webhook path, so a key this poll didn't produce will
    never arrive later the way it can for cloud -- pre-registering it just
    means a sensor stuck on "unknown" forever. A Modbus entry must only get
    sensors for keys its own metrics actually contain, while a key that IS
    present (even one that's normally in the pre-registered set) still
    gets its sensor, same as any other transport."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"transport": "modbus"}
    entry.options = {}
    coordinator = MagicMock()
    coordinator.data = {
        "SN123": {
            "deviceCode": "1",  # hybrid_inverter
            "metrics": {
                "batSoc": "82",  # present -- must still get a sensor
                # acE, totalEnt, bmsState etc. are absent: neither Modbus
                # client ever produces them for this device family.
            },
            "model": "HYX-H10K-HT",
            "device_name": "Test Hybrid",
        }
    }
    coordinator.hyxi_metadata = {"last_success": "2026-03-11T12:00:00Z"}
    hass.data = {DOMAIN: {entry.entry_id: coordinator}}
    async_add_entities = MagicMock()

    with unittest.mock.patch(
        "custom_components.hyxi_cloud.sensor.is_battery_control_enabled",
        return_value=False,
    ):
        await sensor_mod.async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    keys = {
        e.entity_description.key
        for e in entities
        if isinstance(e, sensor_mod.HyxiSensor)
    }
    assert "batSoc" in keys
    assert keys.isdisjoint({"acE", "acP", "totalEnt", "totalEpt", "bmsState"})


@pytest.mark.asyncio
async def test_async_setup_entry_adds_microinverter_summary(mock_entry):
    """When the plant has microinverters, the two aggregate summary sensors
    (total AC power, total daily yield) must be created alongside the
    per-device sensors."""
    coord = MagicMock()
    coord.on_unload = MagicMock()
    coord.data = {
        "SN_MICRO_1": {
            "device_type_code": "MICRO_INVERTER",
            "metrics": {"acP": "18.0", "eToday": "4.51"},
        },
        "SN_MICRO_2": {
            "device_type_code": "MICRO_INVERTER",
            "metrics": {"acP": "22.5", "eToday": "3.29"},
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

    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry.entry_id: coord}}
    async_add_entities = MagicMock()

    with unittest.mock.patch(
        "custom_components.hyxi_cloud.sensor.is_battery_control_enabled",
        return_value=False,
    ):
        await sensor_mod.async_setup_entry(hass, mock_entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    micro_sum_sensors = {
        e.entity_description.key: e
        for e in entities
        if isinstance(e, sensor_mod.HyxiMicroinverterSumSensor)
    }

    assert set(micro_sum_sensors) == {
        "micro_ac_power_total",
        "micro_daily_yield_total",
    }
    assert micro_sum_sensors["micro_ac_power_total"].native_value == 40.5
    assert micro_sum_sensors["micro_daily_yield_total"].native_value == 7.8


def test_microinverter_sum_sensor_skips_unparseable_values():
    """Test a non-numeric metric value on one microinverter is skipped rather
    than aborting the sum for the rest."""
    coordinator = MagicMock()
    coordinator.data = {
        "SN_MICRO_1": {
            "device_type_code": "MICRO_INVERTER",
            "metrics": {"acP": "18.0"},
        },
        "SN_MICRO_2": {
            "device_type_code": "MICRO_INVERTER",
            "metrics": {"acP": "not-a-number"},
        },
        "SN_MICRO_3": {
            "device_type_code": "MICRO_INVERTER",
            "metrics": {"acP": "12.5"},
        },
    }

    entry = MagicMock()
    entry.entry_id = "test_entry"
    description = MagicMock()
    description.key = "micro_ac_power_total"

    sensor = sensor_mod.HyxiMicroinverterSumSensor(
        coordinator, entry, "acP", description
    )

    assert sensor.native_value == 30.5  # 18.0 + 12.5, "not-a-number" skipped


@pytest.mark.asyncio
async def test_async_setup_entry_skips_last_sent_mode_for_unknown_phase(
    mock_coordinator, mock_entry
):
    """Test a control-capable device whose phase can't be detected does not
    get a HyxiLastSentModeSensor (there's nowhere to route mode commands)."""
    mock_coordinator.data = {
        "SN123": {
            "device_name": "Test Inverter",
            "deviceCode": "HYBRID_INVERTER",
            "model": "Unbranded",  # no -HT/-HS suffix
            "metrics": {},  # no phase-indicating metrics either
            "alarms": [],
        }
    }
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry.entry_id: mock_coordinator}}
    async_add_entities = MagicMock()

    with unittest.mock.patch(
        "custom_components.hyxi_cloud.sensor.is_battery_control_enabled",
        return_value=True,
    ):
        await sensor_mod.async_setup_entry(hass, mock_entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    assert not any(isinstance(e, sensor_mod.HyxiLastSentModeSensor) for e in entities)


@pytest.mark.asyncio
async def test_async_setup_entry_modbus_halo_gets_last_sent_mode_sensor(
    mock_coordinator,
):
    """A Modbus micro_ess (HALO) device is control-capable over local
    Modbus (is_control_capable_device_type) and gets a
    HyxiBatteryProtectionController started for it
    (_async_setup_battery_protection in __init__.py) -- without this
    sensor, that controller has no entity to restore last_sent_mode from
    after a restart. HALO has no phase 2/3 registers at all, so the phase
    check that gates cloud entries must not apply here either."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"transport": "modbus"}
    mock_coordinator.data = {
        "SN123": {
            "device_name": "Test HALO",
            "deviceCode": "15",  # micro_ess
            "model": "HYX-MS3000AC",
            "metrics": {},  # HALO has no phase-indicating metrics
            "alarms": [],
        }
    }
    hass = MagicMock()
    hass.data = {DOMAIN: {entry.entry_id: mock_coordinator}}
    async_add_entities = MagicMock()

    with unittest.mock.patch(
        "custom_components.hyxi_cloud.sensor.is_battery_control_enabled",
        return_value=True,
    ):
        await sensor_mod.async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    assert any(isinstance(e, sensor_mod.HyxiLastSentModeSensor) for e in entities)


@pytest.mark.asyncio
async def test_async_setup_entry_cloud_halo_skips_last_sent_mode_sensor(
    mock_coordinator, mock_entry
):
    """A cloud micro_ess (HALO) device stays excluded -- HYXI's cloud API
    rejects the control write outright (MICRO_ESS_CONTROL_SUPPORTED),
    so no controller is ever started for it and this sensor would have
    nothing to report."""
    mock_coordinator.data = {
        "SN123": {
            "device_name": "Test HALO",
            "deviceCode": "15",  # micro_ess
            "model": "HYX-MS3000AC",
            "metrics": {},
            "alarms": [],
        }
    }
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry.entry_id: mock_coordinator}}
    async_add_entities = MagicMock()

    with unittest.mock.patch(
        "custom_components.hyxi_cloud.sensor.is_battery_control_enabled",
        return_value=True,
    ):
        await sensor_mod.async_setup_entry(hass, mock_entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    assert not any(isinstance(e, sensor_mod.HyxiLastSentModeSensor) for e in entities)


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


def test_anti_spike_no_prior_value_returns_none():
    """Test _check_anti_spike is a guarded no-op with nothing to compare against.

    _process_numeric_value only calls it once _last_valid_value is already
    set, so this guard is otherwise unreachable through normal use -- call it
    directly to cover the defensive branch."""
    sensor = sensor_mod.HyxiBaseSensor(MagicMock())
    assert sensor._last_valid_value is None
    assert sensor._check_anti_spike(50.0) is None


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


def test_hyxi_sensor_extra_state_attributes():
    """Test HyxiSensor.extra_state_attributes exposes the coordinator's metadata."""
    coord = MagicMock()
    coord.data = {"SN1": {"deviceCode": "1", "metrics": {"batSoc": "50"}}}
    coord.hyxi_metadata = {"api_status": "Online", "last_attempts": 1}

    desc = MagicMock()
    desc.key = "batSoc"
    desc.translation_key = "batsoc"
    desc.device_class = None
    desc.state_class = None

    sensor = sensor_mod.HyxiSensor(coord, "SN1", desc)

    assert sensor.extra_state_attributes is coord.hyxi_metadata
    assert sensor.extra_state_attributes["api_status"] == "Online"


def test_health_sensor_handle_coordinator_update(mock_coordinator, mock_entry):
    """Test HyxiLastUpdateSensor refreshes its cached value on a coordinator update."""
    sensor = sensor_mod.HyxiLastUpdateSensor(mock_coordinator, mock_entry)
    assert sensor.native_value == "2026-03-11T12:00:00Z"

    mock_coordinator.hyxi_metadata["last_success"] = "2026-03-11T13:30:00Z"
    sensor._handle_coordinator_update()
    assert sensor.native_value == "2026-03-11T13:30:00Z"


def test_subscription_status_sensor_handle_coordinator_update(
    mock_coordinator, mock_entry
):
    """Test HyxiSubscriptionStatusSensor refreshes its combined state on a
    coordinator update (mirrors _update_value, called directly elsewhere)."""
    sensor = sensor_mod.HyxiSubscriptionStatusSensor(mock_coordinator, mock_entry)
    assert sensor.native_value == "active"

    mock_coordinator.alarm_push_status = "inactive"
    mock_coordinator.push_status = "inactive"
    sensor._handle_coordinator_update()
    assert sensor.native_value == "inactive"


def test_connection_type_sensor_serial(mock_coordinator):
    """A serial entry reports "serial", regardless of any framer key --
    wire framing is a TCP-only distinction."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"transport": "modbus", "modbus_type": "serial"}

    sensor = sensor_mod.HyxiModbusConnectionTypeSensor(mock_coordinator, entry)

    assert sensor.native_value == "serial"


def test_connection_type_sensor_tcp_socket(mock_coordinator):
    """A TCP entry detected as native Modbus-TCP framing reports "tcp_socket"."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {
        "transport": "modbus",
        "modbus_type": "tcp",
        "modbus_framer": "socket",
    }

    sensor = sensor_mod.HyxiModbusConnectionTypeSensor(mock_coordinator, entry)

    assert sensor.native_value == "tcp_socket"


def test_connection_type_sensor_tcp_rtu_default(mock_coordinator):
    """A TCP entry with no stored framer (created before auto-detection
    existed) falls back to "tcp_rtu" -- the old hardcoded behavior."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"transport": "modbus", "modbus_type": "tcp"}

    sensor = sensor_mod.HyxiModbusConnectionTypeSensor(mock_coordinator, entry)

    assert sensor.native_value == "tcp_rtu"


@pytest.mark.asyncio
async def test_async_setup_entry_modbus_adds_connection_type_sensor():
    """The connection-type sensor is Modbus-only -- a cloud entry has no
    physical link or wire framing of its own to report (see
    test_async_setup_entry for the cloud-entry case, where it must be
    absent)."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"transport": "modbus", "modbus_type": "serial"}
    entry.options = {}
    coordinator = MagicMock()
    coordinator.data = {
        "SN123": {
            "deviceCode": "1",  # hybrid_inverter
            "metrics": {"batSoc": "82"},
            "model": "HYX-H10K-HT",
            "device_name": "Test Hybrid",
        }
    }
    coordinator.hyxi_metadata = {"last_success": "2026-03-11T12:00:00Z"}
    hass.data = {DOMAIN: {entry.entry_id: coordinator}}
    async_add_entities = MagicMock()

    with unittest.mock.patch(
        "custom_components.hyxi_cloud.sensor.is_battery_control_enabled",
        return_value=False,
    ):
        await sensor_mod.async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    connection_type_sensors = [
        e for e in entities if isinstance(e, sensor_mod.HyxiModbusConnectionTypeSensor)
    ]
    assert len(connection_type_sensors) == 1
    assert connection_type_sensors[0].native_value == "serial"

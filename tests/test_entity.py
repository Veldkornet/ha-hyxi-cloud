"""Tests for the base entity."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel
import sys
import time
from unittest.mock import MagicMock

import pytest


# 1. BULLETPROOF MOCKS
class FakeBase:
    """Fake base class for testing."""


class FakeCoordinatorEntity(FakeBase):
    """Fake coordinator entity."""

    # Allow CoordinatorEntity[HyxiDataUpdateCoordinator] subscripting in class bases
    __class_getitem__ = classmethod(lambda cls, item: cls)

    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


# Retrieve or create mocks
mock_ha = sys.modules.get("homeassistant")
if mock_ha is None:
    mock_ha = MagicMock()
    mock_ha.__path__ = []
    sys.modules["homeassistant"] = mock_ha

if "homeassistant.helpers" not in sys.modules:
    sys.modules["homeassistant.helpers"] = mock_ha

if "homeassistant.helpers.update_coordinator" not in sys.modules:
    sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()

mock_coordinator = sys.modules["homeassistant.helpers.update_coordinator"]
if isinstance(mock_coordinator, MagicMock):
    mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity

# 2. LOCAL IMPORTS (After patching sys.modules)
from custom_components.hyxi_cloud.const import DOMAIN, MANUFACTURER
from custom_components.hyxi_cloud.entity import HyxiEntity, SettingsSyncMixin


class FakeSettingsSyncEntity(SettingsSyncMixin):  # pylint: disable=abstract-method
    """Minimal host for exercising SettingsSyncMixin in isolation.

    Deliberately doesn't override _apply_settings_metrics -- one test below
    exercises the mixin's own stub directly.
    """

    def __init__(self, coordinator, sn):
        self.coordinator = coordinator
        self._sn = sn


def test_hyxi_entity_initialization_with_complete_data():
    """Test entity initialization with full device data."""
    coordinator = MagicMock()
    sn = "123456"
    dev_data = {"device_name": "My Inverter", "model": "HYXI-Model-X"}

    entity = HyxiEntity(coordinator, sn, dev_data)

    assert entity._sn == sn
    assert getattr(entity, "_attr_has_entity_name", None) is True
    assert entity._attr_device_info == {
        "identifiers": {(DOMAIN, sn)},
        "name": "My Inverter",
        "manufacturer": MANUFACTURER,
        "model": "HYXI-Model-X",
        "serial_number": sn,
    }


def test_hyxi_entity_initialization_with_missing_data():
    """Test entity initialization with missing device data."""
    coordinator = MagicMock()
    sn = "654321"
    dev_data: dict[str, str] = {}

    entity = HyxiEntity(coordinator, sn, dev_data)

    assert entity._sn == sn
    assert getattr(entity, "_attr_has_entity_name", None) is True
    assert entity._attr_device_info == {
        "identifiers": {(DOMAIN, sn)},
        "name": f"Device {sn}",
        "manufacturer": MANUFACTURER,
        "model": None,
        "serial_number": sn,
    }


def test_hyxi_entity_initialization_with_falsy_name():
    """Test entity initialization with falsy device name."""
    coordinator = MagicMock()
    sn = "999999"
    dev_data = {"device_name": "", "model": "Basic"}

    entity = HyxiEntity(coordinator, sn, dev_data)

    assert entity._sn == sn
    assert getattr(entity, "_attr_has_entity_name", None) is True
    assert entity._attr_device_info == {
        "identifiers": {(DOMAIN, sn)},
        "name": f"Device {sn}",
        "manufacturer": MANUFACTURER,
        "model": "Basic",
        "serial_number": sn,
    }


def test_hyxi_entity_initialization_with_none_name():
    """Test entity initialization with None device name."""
    coordinator = MagicMock()
    sn = "888888"
    dev_data = {"device_name": None, "model": None}

    entity = HyxiEntity(coordinator, sn, dev_data)

    assert entity._sn == sn
    assert getattr(entity, "_attr_has_entity_name", None) is True
    assert entity._attr_device_info == {
        "identifiers": {(DOMAIN, sn)},
        "name": f"Device {sn}",
        "manufacturer": MANUFACTURER,
        "model": None,
        "serial_number": sn,
    }


def test_settings_metrics_returns_none_without_a_read_marker():
    """A device dict with no settings read yet (or one from before the
    metrics-embedding change) has nothing to adopt."""
    coordinator = MagicMock()
    coordinator.data = {"SN1": {"metrics": {}}}
    entity = FakeSettingsSyncEntity(coordinator, "SN1")

    assert entity._settings_metrics() is None


def test_settings_metrics_adopts_a_read_seen_for_the_first_time():
    """The first settings read, arriving with its own marker, is adopted."""
    coordinator = MagicMock()
    coordinator.data = {
        "SN1": {"metrics": {"_settings_read_at": 100.0, "self_use_soc": 10}}
    }
    entity = FakeSettingsSyncEntity(coordinator, "SN1")

    metrics = entity._settings_metrics()

    assert metrics == {"_settings_read_at": 100.0, "self_use_soc": 10}


def test_settings_metrics_re_adopts_the_same_read_seen_before():
    """The same read_at arriving again (an unrelated poll that didn't refresh
    settings) is harmless to hand back again -- nothing tracks "already
    seen" once the write-ordering guard is the only thing that matters for
    safety."""
    coordinator = MagicMock()
    coordinator.data = {"SN1": {"metrics": {"_settings_read_at": 100.0}}}
    entity = FakeSettingsSyncEntity(coordinator, "SN1")
    entity._settings_metrics()

    assert entity._settings_metrics() == {"_settings_read_at": 100.0}


def test_settings_metrics_ignores_a_read_that_predates_the_last_write():
    """A settings read that was already in flight before this entity's own
    write landed must not clobber it, even though it is otherwise "new"."""
    coordinator = MagicMock()
    coordinator.data = {"SN1": {"metrics": {"_settings_read_at": 100.0}}}
    entity = FakeSettingsSyncEntity(coordinator, "SN1")
    entity._last_write_at = 100.0  # write landed at/after the read started

    assert entity._settings_metrics() is None


def test_settings_metrics_adopts_a_read_that_follows_the_last_write():
    """Once a later read genuinely postdates the write, it is safe again."""
    coordinator = MagicMock()
    coordinator.data = {"SN1": {"metrics": {"_settings_read_at": 200.0}}}
    entity = FakeSettingsSyncEntity(coordinator, "SN1")
    entity._last_write_at = 100.0

    assert entity._settings_metrics() == {"_settings_read_at": 200.0}


def test_apply_settings_metrics_is_left_for_each_host_class_to_implement():
    """The mixin's own stub is never meant to run -- HyxiSettingNumber and
    HyxiAntiStarvationSwitch both supply their own extraction step."""
    coordinator = MagicMock()
    entity = FakeSettingsSyncEntity(coordinator, "SN1")

    with pytest.raises(NotImplementedError):
        entity._apply_settings_metrics({})


def test_note_write_stamps_last_write_at_with_the_current_monotonic_time():
    coordinator = MagicMock()
    entity = FakeSettingsSyncEntity(coordinator, "SN1")

    before = time.monotonic()
    entity._note_write()
    after = time.monotonic()

    last_write_at = entity._last_write_at
    assert last_write_at is not None
    assert before <= last_write_at <= after

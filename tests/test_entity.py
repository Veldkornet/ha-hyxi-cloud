"""Tests for the base entity."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel
import sys
from unittest.mock import MagicMock


# 1. BULLETPROOF MOCKS
class FakeBase:
    """Fake base class for testing."""


class FakeCoordinatorEntity(FakeBase):
    """Fake coordinator entity."""

    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


mock_ha = MagicMock()
mock_ha.__path__ = []
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.update_coordinator"] = mock_ha
mock_ha.CoordinatorEntity = FakeCoordinatorEntity

# 2. LOCAL IMPORTS (After patching sys.modules)
from custom_components.hyxi_cloud.const import DOMAIN, MANUFACTURER  # noqa: E402
from custom_components.hyxi_cloud.entity import HyxiEntity  # noqa: E402


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

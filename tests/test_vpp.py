"""Tests for VPP awareness and dynamic control defaults."""
# pylint: disable=wrong-import-position

import importlib
import sys
from unittest.mock import MagicMock, patch


# Define authorization exception for testing imports
class ConfigEntryAuthFailed(Exception):
    pass


# Define Fake classes to match button/switch mock base inheritance
class FakeBase:
    @property
    def available(self) -> bool:
        return True


class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, **kwargs):
        self.coordinator = coordinator
        self._attr_extra_state_attributes = {}

    def _handle_coordinator_update(self) -> None:
        pass


class FakeBinarySensorEntity(FakeBase):
    pass


class FakeButtonEntity(FakeBase):
    pass


class FakeSwitchEntity(FakeBase):
    pass


# Helper to force set attributes on mock modules, bypassing ensure_mock's checks
def force_mock_attribute(module_name, attr_name, attr_value):
    if module_name not in sys.modules:
        sys.modules[module_name] = MagicMock()
    mod = sys.modules[module_name]
    setattr(mod, attr_name, attr_value)


force_mock_attribute(
    "homeassistant.exceptions", "ConfigEntryAuthFailed", ConfigEntryAuthFailed
)
force_mock_attribute(
    "homeassistant.helpers.update_coordinator",
    "CoordinatorEntity",
    FakeCoordinatorEntity,
)
force_mock_attribute(
    "homeassistant.components.binary_sensor",
    "BinarySensorEntity",
    FakeBinarySensorEntity,
)
force_mock_attribute(
    "homeassistant.components.button", "ButtonEntity", FakeButtonEntity
)
force_mock_attribute(
    "homeassistant.components.switch", "SwitchEntity", FakeSwitchEntity
)


# Mock hyxi_cloud_api
mock_api = MagicMock()
mock_api.VPP_ACTIVE_MODES = frozenset({"13", "14", "16"})
if "hyxi_cloud_api" not in sys.modules:
    sys.modules["hyxi_cloud_api"] = mock_api
else:
    sys.modules["hyxi_cloud_api"].VPP_ACTIVE_MODES = frozenset({"13", "14", "16"})  # type: ignore[attr-defined]

# Import modules and force reload to ensure they use our fakes
import custom_components.hyxi_cloud.binary_sensor as binary_sensor_mod
import custom_components.hyxi_cloud.button as button_mod
import custom_components.hyxi_cloud.entity as entity_mod
import custom_components.hyxi_cloud.switch as switch_mod

importlib.reload(entity_mod)
importlib.reload(binary_sensor_mod)
importlib.reload(button_mod)
importlib.reload(switch_mod)

from custom_components.hyxi_cloud.const import is_battery_control_enabled


def test_vpp_control_sensor_states():
    """Test VPP control status binary sensor behavior and attributes."""
    coordinator = MagicMock()
    coordinator.data = {
        "SN123": {
            "metrics": {
                "vppMode": "16",
                "vppCode": "VPP1",
                "vppName": "Virtual Power Plant",
                "vppManufacturer": "HYXI",
                "vppSupplierName": "Supplier",
            }
        }
    }
    entry = MagicMock()
    entry.entry_id = "test_entry"

    with patch(
        "custom_components.hyxi_cloud.binary_sensor.VPP_ACTIVE_MODES",
        frozenset({"13", "14", "16"}),
    ):
        sensor = binary_sensor_mod.HyxiVppControlSensor(coordinator, entry, "SN123", {})
        assert sensor.is_on is True
        attrs = sensor.extra_state_attributes
        assert attrs["vpp_mode"] == "16"
        assert attrs["vpp_code"] == "VPP1"
        assert attrs["vpp_name"] == "Virtual Power Plant"
        assert attrs["vpp_manufacturer"] == "HYXI"
        assert attrs["vpp_supplier_name"] == "Supplier"

        # Test fallback behavior when details are empty but VPP is active (using integer mode 13/14/16)
        for active_mode in (13, 14, 16):
            coordinator.data["SN123"]["metrics"] = {
                "vppMode": active_mode,
                "vppCode": "",
                "vppName": "",
                "vppManufacturer": "",
                "vppSupplierName": "",
            }
            sensor_int = binary_sensor_mod.HyxiVppControlSensor(
                coordinator, entry, "SN123", {}
            )
            assert sensor_int.is_on is True
            attrs = sensor_int.extra_state_attributes
            assert attrs["vpp_mode"] == str(active_mode)
            assert attrs["vpp_code"] == "Active"
            assert attrs["vpp_name"] == "Active VPP"
            assert attrs["vpp_manufacturer"] == "Enrolled"
            assert attrs["vpp_supplier_name"] == "Enrolled (Active VPP)"

        # Test inactive VPP
        coordinator.data["SN123"]["metrics"] = {
            "vppMode": "0",
            "vppCode": "",
            "vppName": "",
            "vppManufacturer": "",
            "vppSupplierName": "",
        }
        assert sensor.is_on is False
        attrs = sensor.extra_state_attributes
        assert attrs["vpp_mode"] == "0"
        assert attrs["vpp_code"] == "None"
        assert attrs["vpp_name"] == "None"
        assert attrs["vpp_manufacturer"] == "Not enrolled"
        assert attrs["vpp_supplier_name"] == "Not enrolled"


def test_is_battery_control_enabled_helper():
    """Test dynamic default resolving logic of is_battery_control_enabled."""
    entry = MagicMock()
    # Case 1: Explicitly set in options
    entry.options = {"enable_battery_control": True}
    assert is_battery_control_enabled(entry, None) is True

    entry.options = {"enable_battery_control": False}
    assert is_battery_control_enabled(entry, None) is False

    # Case 2: Not set, no active VPP (should default to False)
    entry.options = {}
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"workMode": "0"}}}
    with patch("hyxi_cloud_api.VPP_ACTIVE_MODES", frozenset({"16"})):
        assert is_battery_control_enabled(entry, coordinator) is False

        # Case 3: Not set, active VPP (should still default to False)
        coordinator.data["SN123"]["metrics"]["workMode"] = "16"
        assert is_battery_control_enabled(entry, coordinator) is False

        # Case 4: Coordinator is None
        assert is_battery_control_enabled(entry, None) is False


def test_button_and_switch_lockout():
    """Test that buttons and switches report unavailable when VPP is active."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"SN123": {"metrics": {"vppMode": "16"}}}

    with (
        patch(
            "custom_components.hyxi_cloud.button.VPP_ACTIVE_MODES",
            frozenset({"13", "14", "16"}),
        ),
        patch(
            "custom_components.hyxi_cloud.switch.VPP_ACTIVE_MODES",
            frozenset({"13", "14", "16"}),
        ),
    ):
        for active_mode in (13, 14, 16):
            # Button lockout (with integer and string modes)
            coordinator.data["SN123"]["metrics"]["vppMode"] = active_mode
            btn = button_mod.HyxiModeButton(coordinator, "SN123", {}, "idle")
            assert btn.available is False

            # Switch lockout
            sw = switch_mod.HyxiFrequencyControlSwitch(coordinator, "SN123", {})
            assert sw.available is False

        # Inactive VPP controls are available
        coordinator.data["SN123"]["metrics"]["vppMode"] = 0
        btn = button_mod.HyxiModeButton(coordinator, "SN123", {}, "idle")
        sw = switch_mod.HyxiFrequencyControlSwitch(coordinator, "SN123", {})
        assert btn.available is True
        assert sw.available is True

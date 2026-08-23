"""Tests for VPP awareness binary sensor and battery control defaults."""
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
    # Allow CoordinatorEntity[HyxiDataUpdateCoordinator] subscripting in class bases
    __class_getitem__ = classmethod(lambda cls, item: cls)

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


# VPP_ACTIVE_MODES now only contains confirmed active dispatch modes (13, 14).
# Mode 16 (VPP enrolled/standby) was removed after live API diagnostic confirmed
# it causes false-positive lockouts for enrolled-but-idle devices.
mock_api = MagicMock()
mock_api.VPP_ACTIVE_MODES = frozenset({"13", "14"})
if "hyxi_cloud_api" not in sys.modules:
    sys.modules["hyxi_cloud_api"] = mock_api
else:
    sys.modules["hyxi_cloud_api"].VPP_ACTIVE_MODES = frozenset({"13", "14"})  # type: ignore[attr-defined]

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


def test_vpp_dispatch_sensor_on_during_active_dispatch():
    """VPP binary sensor is ON only during confirmed dispatch modes (13=charge, 14=discharge).

    Reads workMode -- the only field the cloud API actually populates.
    vppCode/vppName/vppManufacturer/vppSupplierName never existed in
    hyxi_cloud_api and were removed along with the vppMode key mismatch
    that made this sensor permanently read as "off" regardless of the
    real device state.
    """
    coordinator = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"

    with patch(
        "custom_components.hyxi_cloud.binary_sensor.VPP_ACTIVE_MODES",
        frozenset({"13", "14"}),
    ):
        for active_mode in (13, 14):
            coordinator.data = {"SN123": {"metrics": {"workMode": active_mode}}}
            sensor = binary_sensor_mod.HyxiVppDispatchSensor(
                coordinator, entry, "SN123", {}
            )
            assert sensor.is_on is True, (
                f"Mode {active_mode} should activate the VPP sensor"
            )
            assert sensor.extra_state_attributes == {"work_mode": str(active_mode)}


def test_vpp_dispatch_sensor_off_in_standby_and_normal_modes():
    """Mode 16 (enrolled/standby) and normal modes must NOT trigger the VPP sensor."""
    coordinator = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"

    with patch(
        "custom_components.hyxi_cloud.binary_sensor.VPP_ACTIVE_MODES",
        frozenset({"13", "14"}),
    ):
        for inactive_mode in ("16", "0", "1", "2", "3"):
            coordinator.data = {"SN123": {"metrics": {"workMode": inactive_mode}}}
            sensor = binary_sensor_mod.HyxiVppDispatchSensor(
                coordinator, entry, "SN123", {}
            )
            assert sensor.is_on is False, (
                f"Mode {inactive_mode!r} should NOT activate the VPP sensor"
            )

        # No workMode reported at all -- falls back to "None", not a crash.
        coordinator.data["SN123"]["metrics"] = {}
        sensor = binary_sensor_mod.HyxiVppDispatchSensor(
            coordinator, entry, "SN123", {}
        )
        assert sensor.is_on is False
        assert sensor.extra_state_attributes == {"work_mode": "None"}


def test_is_battery_control_enabled_helper():
    """Battery control defaults to False and is not affected by workMode."""
    entry = MagicMock()
    # Case 1: Explicitly set in options
    entry.options = {"enable_battery_control": True}
    assert is_battery_control_enabled(entry, None) is True

    entry.options = {"enable_battery_control": False}
    assert is_battery_control_enabled(entry, None) is False

    # Case 2: Not set — always defaults to False regardless of workMode
    entry.options = {}
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"workMode": "0"}}}
    assert is_battery_control_enabled(entry, coordinator) is False

    coordinator.data["SN123"]["metrics"]["workMode"] = "16"
    assert is_battery_control_enabled(entry, coordinator) is False

    # Case 3: Coordinator is None
    assert is_battery_control_enabled(entry, None) is False

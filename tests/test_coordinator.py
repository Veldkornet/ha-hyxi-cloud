"""Tests for the DataUpdateCoordinator logic."""
# pylint: disable=wrong-import-position

import sys
from unittest.mock import MagicMock


class MockModule(MagicMock):
    @classmethod
    def __getattr__(cls, name):
        if name == "__path__":
            return []
        if name == "__spec__":
            return None
        return MagicMock()


mock_ha = MockModule()
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha

mock_helpers = MockModule()
sys.modules["homeassistant.helpers"] = mock_helpers
sys.modules["homeassistant.helpers.aiohttp_client"] = MockModule()

mock_util = MockModule()
sys.modules["homeassistant.util"] = mock_util

mock_config = MockModule()
sys.modules["homeassistant.config_entries"] = mock_config

mock_coordinator = MockModule()


class DummyDataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval):  # pylint: disable=unused-argument
        self.data = {}


mock_coordinator.DataUpdateCoordinator = DummyDataUpdateCoordinator
mock_coordinator.UpdateFailed = Exception
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator

mock_api = MagicMock()
sys.modules["hyxi_cloud_api"] = mock_api

mock_const = MagicMock()
mock_const.DOMAIN = "hyxi_cloud"
sys.modules["custom_components.hyxi_cloud.const"] = mock_const


from custom_components.hyxi_cloud.coordinator import (  # noqa: E402, I001
    HyxiDataUpdateCoordinator,
    _safe_float,
)


def test_safe_float():
    """Verify _safe_float extraction handles normal values and edge cases."""

    assert _safe_float(500.5) == 500.5
    assert _safe_float("123.45") == 123.45
    assert _safe_float("invalid") == 0.0
    assert _safe_float(None) == 0.0
    assert _safe_float("") == 0.0

    mock_entry = MagicMock()
    mock_entry.data = {"access_key": "ak", "secret_key": "sk", "base_url": "url"}
    mock_entry.options = {"update_interval": 5}
    coordinator = HyxiDataUpdateCoordinator(MagicMock(), MagicMock(), mock_entry)
    coordinator.data = {}

    # Empty data
    assert coordinator.get_battery_summary() is None

    # Provide valid data mimicking _execute_fetch_all response
    coordinator.data = {
        "SN123": {
            "device_type_code": "BATTERY_SYSTEM",
            "metrics": {"batSoc": "85.2", "batSoh": 99.1},
        },
        "SN456": {
            "device_type_code": "HYBRID_INVERTER",
            "metrics": {"batSoc": "12.8", "batSoh": "98.0"},
        },
        "SN789": {
            "device_type_code": "SMART_METER",
            "metrics": {
                "batSoc": "100.0"  # Should be ignored
            },
        },
    }
    # 85.2 + 12.8 = 98.0 / 2 = 49.0
    # 99.1 + 98.0 = 197.1 / 2 = 98.55 -> python round(x, 1) goes to even -> 98.5
    res = coordinator.get_battery_summary()
    assert res is not None
    assert res["avg_soc"] == 49.0
    assert res["avg_soh"] == 98.5
    assert res["count"] == 2


def test_get_battery_summary():
    """Test get_battery_summary with different device types and missing data."""
    mock_entry = MagicMock()
    mock_entry.data = {"access_key": "ak", "secret_key": "sk", "base_url": "url"}
    mock_entry.options = {"update_interval": 5}
    coordinator = HyxiDataUpdateCoordinator(MagicMock(), MagicMock(), mock_entry)

    # Empty data
    coordinator.data = {}
    assert coordinator.get_battery_summary() is None

    # Full data
    coordinator.data = {
        "SN1": {
            "device_type_code": "BATTERY_SYSTEM",
            "metrics": {
                "pbat": "100.0",
                "batSoc": "80.0",
                "batSoh": "100.0",
                "bat_charge_total": "500.0",
                "bat_discharge_total": "100.0",
                "bat_charging": "10.0",
                "bat_discharging": "5.0",
            },
        },
        "SN2": {
            "device_type_code": "EMS",
            "metrics": {
                "pbat": "50.0",
                "batSoc": "40.0",
                "batSoh": "90.0",
                "bat_charge_total": "200.0",
                "bat_discharge_total": "50.0",
                "bat_charging": "0.0",
                "bat_discharging": "0.0",
            },
        },
        "SN3": {
            "device_type_code": "HYBRID_INVERTER",
            "metrics": {
                "pbat": "200.0",
                "batSoc": "10.0",
                "batSoh": "80.0",
                "bat_charge_total": "1000.0",
                "bat_discharge_total": "500.0",
                "bat_charging": "20.0",
                "bat_discharging": "10.0",
            },
        },
        "SN4": {
            "device_type_code": "ALL_IN_ONE",
            "metrics": {
                "pbat": "150.0",
                "batSoc": "60.0",
                "batSoh": "70.0",
                "bat_charge_total": "300.0",
                "bat_discharge_total": "200.0",
                "bat_charging": "5.0",
                "bat_discharging": "2.0",
            },
        },
        "SN5": {
            "device_type_code": "SMART_METER",
            "metrics": {
                "pbat": "10000.0",
                "batSoc": "100.0",
                "batSoh": "100.0",
            },
        },
        "SN6": {
            "device_type_code": "BATTERY_SYSTEM",
            "metrics": {},  # Missing metrics
        },
    }

    res = coordinator.get_battery_summary()
    assert res is not None
    # 5 matching devices: SN1, SN2, SN3, SN4, SN6. SN5 is ignored.
    assert res["count"] == 5

    # Sums
    assert res["total_pbat"] == 100.0 + 50.0 + 200.0 + 150.0 + 0.0  # 500.0
    assert res["bat_charge_total"] == 500.0 + 200.0 + 1000.0 + 300.0 + 0.0  # 2000.0
    assert res["bat_discharge_total"] == 100.0 + 50.0 + 500.0 + 200.0 + 0.0  # 850.0
    assert res["bat_charging"] == 10.0 + 0.0 + 20.0 + 5.0 + 0.0  # 35.0
    assert res["bat_discharging"] == 5.0 + 0.0 + 10.0 + 2.0 + 0.0  # 17.0

    # Averages
    # avg_soc sum = 80.0 + 40.0 + 10.0 + 60.0 + 0.0 = 190.0
    # avg_soc avg = 190.0 / 5 = 38.0
    assert res["avg_soc"] == 38.0

    # avg_soh sum = 100.0 + 90.0 + 80.0 + 70.0 + 0.0 = 340.0
    # avg_soh avg = 340.0 / 5 = 68.0
    assert res["avg_soh"] == 68.0

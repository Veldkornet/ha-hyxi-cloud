"""Tests for the DataUpdateCoordinator logic."""
# pylint: disable=wrong-import-position

import sys
from unittest.mock import MagicMock

mock_ha = MagicMock()
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.helpers"] = mock_ha

mock_util = MagicMock()
sys.modules["homeassistant.util"] = mock_util

mock_config = MagicMock()
sys.modules["homeassistant.config_entries"] = mock_config

mock_coordinator = MagicMock()


class DummyDataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval):  # pylint: disable=unused-argument
        self.data = {}


mock_coordinator.DataUpdateCoordinator = DummyDataUpdateCoordinator
mock_coordinator.UpdateFailed = Exception
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator
import importlib
if 'custom_components.hyxi_cloud.coordinator' in sys.modules:
    importlib.reload(sys.modules['custom_components.hyxi_cloud.coordinator'])

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

"""Tests for the DataUpdateCoordinator logic."""
# pylint: disable=wrong-import-position

import sys
from unittest.mock import AsyncMock
from unittest.mock import MagicMock


class ModuleMock(MagicMock):
    __path__ = []


mock_ha = ModuleMock()
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha

mock_util = ModuleMock()
sys.modules["homeassistant.util"] = mock_util

mock_config = ModuleMock()
sys.modules["homeassistant.config_entries"] = mock_config

mock_coordinator = ModuleMock()


class DummyDataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval):  # pylint: disable=unused-argument
        self.data = {}


mock_coordinator.DataUpdateCoordinator = DummyDataUpdateCoordinator
# We cannot try/except import from homeassistant here because it will hit
# our mocked modules and raise an AttributeError on __spec__.
# Instead, we define our exception types locally first, assign them to the mocks,
# and use these local types in the test assertions below.
ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
UpdateFailed = type("UpdateFailed", (Exception,), {})

mock_coordinator.UpdateFailed = UpdateFailed
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator

mock_exceptions = sys.modules["homeassistant.exceptions"]
mock_exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed

mock_api = MagicMock()
sys.modules["hyxi_cloud_api"] = mock_api

mock_const = MagicMock()
mock_const.DOMAIN = "hyxi_cloud"
sys.modules["custom_components.hyxi_cloud.const"] = mock_const


import pytest  # noqa: E402

from custom_components.hyxi_cloud.coordinator import (  # noqa: E402, I001
    HyxiDataUpdateCoordinator,
)
from custom_components.hyxi_cloud.coordinator import _safe_float  # noqa: E402, I001


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


@pytest.mark.asyncio
async def test_async_update_data_success():
    """Test successful data update from API."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = AsyncMock()
    mock_client.get_all_device_data.return_value = {
        "data": {"SN123": {"metrics": {"test": 1}}},
        "attempts": 2,
    }

    coordinator = HyxiDataUpdateCoordinator(MagicMock(), mock_client, mock_entry)

    # Assert return value is pure device dictionary
    result = await coordinator._async_update_data()
    assert result == {"SN123": {"metrics": {"test": 1}}}

    # Assert metadata is updated
    assert coordinator.hyxi_metadata["last_attempts"] == 2
    assert coordinator.hyxi_metadata["last_success"] is not None


@pytest.mark.asyncio
async def test_async_update_data_auth_failed():
    """Test auth_failed handling from API."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = AsyncMock()
    mock_client.get_all_device_data.return_value = "auth_failed"

    coordinator = HyxiDataUpdateCoordinator(MagicMock(), mock_client, mock_entry)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_none_result():
    """Test None result handling from API."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = AsyncMock()
    mock_client.get_all_device_data.return_value = None

    coordinator = HyxiDataUpdateCoordinator(MagicMock(), mock_client, mock_entry)

    with pytest.raises(UpdateFailed, match="HYXi Cloud unreachable"):
        await coordinator._async_update_data()

    assert coordinator.hyxi_metadata["last_attempts"] == 3


@pytest.mark.asyncio
async def test_async_update_data_unexpected_error():
    """Test unexpected error handling from API."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = AsyncMock()
    mock_client.get_all_device_data.side_effect = Exception("Test exception")

    coordinator = HyxiDataUpdateCoordinator(MagicMock(), mock_client, mock_entry)
    initial_attempts = coordinator.hyxi_metadata["last_attempts"]

    with pytest.raises(UpdateFailed, match="Unexpected error"):
        await coordinator._async_update_data()

    assert coordinator.hyxi_metadata["last_attempts"] == initial_attempts + 1

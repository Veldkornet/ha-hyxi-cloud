"""Tests for the DataUpdateCoordinator logic."""
# pylint: disable=wrong-import-position

import importlib
import sys
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

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
    """Dummy class to mock DataUpdateCoordinator."""

    def __init__(self, hass, logger, name, update_interval, config_entry=None):  # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
        self.hass = hass
        self.data = {}


mock_coordinator.DataUpdateCoordinator = DummyDataUpdateCoordinator


class DummyUpdateFailed(Exception):
    pass


mock_coordinator.UpdateFailed = DummyUpdateFailed
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator


class DummyConfigEntryAuthFailed(Exception):
    pass


mock_config_exceptions = MagicMock()
mock_config_exceptions.ConfigEntryAuthFailed = DummyConfigEntryAuthFailed
sys.modules["homeassistant.exceptions"] = mock_config_exceptions

mock_api = MagicMock()
mock_api.__version__ = "1.0.4"
sys.modules["hyxi_cloud_api"] = mock_api

mock_const = MagicMock()
mock_const.DOMAIN = "hyxi_cloud"
sys.modules["custom_components.hyxi_cloud.const"] = mock_const


import custom_components.hyxi_cloud.coordinator as hc_coord  # pylint: disable=wrong-import-position # noqa: E402

importlib.reload(hc_coord)


@pytest.mark.asyncio
async def test_async_update_data_unexpected_error():
    """Test unexpected errors are caught and logged."""
    mock_entry = MagicMock()
    mock_entry.data = {"access_key": "ak", "secret_key": "sk", "base_url": "url"}
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.get_all_device_data = AsyncMock(
        side_effect=TimeoutError("Test unexpected error")
    )

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    assert coordinator.hyxi_metadata["last_attempts"] == 0
    assert coordinator.hyxi_metadata["api_status"] == "Starting"

    with pytest.raises(hc_coord.UpdateFailed) as excinfo:
        await coordinator._async_update_data()

    assert "Unexpected error: Test unexpected error" in str(excinfo.value)
    assert coordinator.hyxi_metadata["last_attempts"] == 1
    assert coordinator.hyxi_metadata["last_error"] == "Test unexpected error"
    assert coordinator.hyxi_metadata["api_status"] == "Error"


@pytest.mark.asyncio
async def test_async_update_data_auth_failed():
    """Test auth_failed response is handled."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.get_all_device_data = AsyncMock(return_value="auth_failed")

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    with pytest.raises(hc_coord.ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_none_result():
    """Test None response is handled."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.get_all_device_data = AsyncMock(return_value=None)

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    with pytest.raises(hc_coord.UpdateFailed) as excinfo:
        await coordinator._async_update_data()

    assert "HYXI Cloud unreachable" in str(excinfo.value)
    assert coordinator.hyxi_metadata["last_attempts"] == 3


@pytest.mark.asyncio
async def test_async_update_data_success():
    """Test successful data update."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.get_all_device_data = AsyncMock(
        return_value={"data": {"SN123": {"metrics": {}}}, "attempts": 1}
    )

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    result = await coordinator._async_update_data()

    assert result == {"SN123": {"metrics": {}}}
    assert coordinator.hyxi_metadata["last_attempts"] == 1
    assert coordinator.hyxi_metadata["last_success"] is not None

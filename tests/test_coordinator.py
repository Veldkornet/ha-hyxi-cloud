"""Tests for the DataUpdateCoordinator logic."""
# pylint: disable=wrong-import-position

import importlib
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Retrieve or create mocks
mock_ha = sys.modules.get("homeassistant")
if mock_ha is None:
    mock_ha = MagicMock()
    sys.modules["homeassistant"] = mock_ha

if "homeassistant.components" not in sys.modules:
    sys.modules["homeassistant.components"] = mock_ha

if "homeassistant.core" not in sys.modules:
    sys.modules["homeassistant.core"] = mock_ha

if "homeassistant.exceptions" not in sys.modules:
    sys.modules["homeassistant.exceptions"] = mock_ha

if "homeassistant.helpers" not in sys.modules:
    sys.modules["homeassistant.helpers"] = mock_ha

if "homeassistant.util" not in sys.modules:
    sys.modules["homeassistant.util"] = MagicMock()

if "homeassistant.config_entries" not in sys.modules:
    sys.modules["homeassistant.config_entries"] = MagicMock()


class DummyDataUpdateCoordinator:
    """Dummy class to mock DataUpdateCoordinator."""

    def __init__(self, hass, logger, name, update_interval, config_entry=None):  # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
        self.hass = hass
        self.data = {}


class DummyUpdateFailed(Exception):
    pass


# Retrieve or create update_coordinator mock, and set the dummy classes
if "homeassistant.helpers.update_coordinator" not in sys.modules:
    sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()

mock_coordinator: Any = sys.modules["homeassistant.helpers.update_coordinator"]
mock_coordinator.DataUpdateCoordinator = DummyDataUpdateCoordinator
mock_coordinator.UpdateFailed = DummyUpdateFailed


class DummyConfigEntryAuthFailed(Exception):
    pass


# Ensure ConfigEntryAuthFailed is set on exceptions and config_entries
mock_exceptions = sys.modules["homeassistant.exceptions"]
if isinstance(mock_exceptions, MagicMock):
    mock_exceptions.ConfigEntryAuthFailed = DummyConfigEntryAuthFailed

mock_config = sys.modules["homeassistant.config_entries"]
if isinstance(mock_config, MagicMock):
    mock_config.ConfigEntryAuthFailed = DummyConfigEntryAuthFailed

if "hyxi_cloud_api" not in sys.modules:
    mock_api = MagicMock()
    mock_api.__version__ = "1.0.4"
    sys.modules["hyxi_cloud_api"] = mock_api


import custom_components.hyxi_cloud.coordinator as hc_coord  # pylint: disable=wrong-import-position

importlib.reload(hc_coord)


@pytest.fixture(autouse=True)
def mock_store():
    """Mock the Store class."""
    with patch("custom_components.hyxi_cloud.coordinator.Store") as mock_store:
        mock_instance = mock_store.return_value
        mock_instance.async_save = AsyncMock()
        mock_instance.async_load = AsyncMock(return_value=None)
        yield mock_store


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
    """Test successful data update with non-empty metrics."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.get_all_device_data = AsyncMock(
        return_value={"data": {"SN123": {"metrics": {"tinv": "45.0"}}}, "attempts": 1}
    )
    mock_client._request = AsyncMock(  # pylint: disable=protected-access
        return_value=(200, {"success": False})
    )

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    result = await coordinator._async_update_data()

    assert result["SN123"]["metrics"] == {"tinv": "45.0"}
    assert coordinator.hyxi_metadata["last_attempts"] == 1
    assert coordinator.hyxi_metadata["last_success"] is not None


@pytest.mark.asyncio
async def test_async_update_data_empty_telemetry():
    """Test that empty telemetry warns but does not raise UpdateFailed."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    # Device with empty metrics (only last_seen) — should warn, not fail
    mock_client.get_all_device_data = AsyncMock(
        return_value={
            "data": {"SN123": {"metrics": {"last_seen": "2026-05-22"}}},
            "attempts": 1,
        }
    )
    mock_client._request = AsyncMock(  # pylint: disable=protected-access
        return_value=(200, {"success": False})
    )

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    result = await coordinator._async_update_data()

    # Data returned despite empty telemetry — no UpdateFailed, no backoff
    assert "SN123" in result
    assert coordinator.hyxi_metadata["api_status"] == "Online"
    assert coordinator.hyxi_metadata["last_success"] is not None


@pytest.mark.asyncio
async def test_async_update_data_empty_telemetry_collector_only():
    """Test that empty metrics for collectors only does not raise UpdateFailed."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    # Device type "3" (collector) with empty metrics should NOT trigger UpdateFailed
    mock_client.get_all_device_data = AsyncMock(
        return_value={
            "data": {"SN123": {"device_type_code": "3", "metrics": {}}},
            "attempts": 1,
        }
    )
    mock_client._request = AsyncMock(  # pylint: disable=protected-access
        return_value=(200, {"success": False})
    )

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    result = await coordinator._async_update_data()
    assert result["SN123"]["metrics"] == {}
    assert coordinator.hyxi_metadata["last_success"] is not None


@pytest.mark.asyncio
async def test_async_sync_device_metadata_no_change():
    """Test that device registry is not updated if versions match."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), MagicMock(), mock_entry
    )

    mock_dev_reg = MagicMock()
    with (
        patch(
            "custom_components.hyxi_cloud.coordinator.dr.async_get",
            return_value=mock_dev_reg,
        ),
        patch(
            "custom_components.hyxi_cloud.coordinator.get_software_version",
            return_value="1.2.3",
        ),
    ):
        mock_device = MagicMock()
        mock_device.model = None
        mock_device.sw_version = "1.2.3"
        mock_device.hw_version = "V1"
        mock_device.id = "device_id"
        mock_dev_reg.async_get_device.return_value = mock_device

        devices = {"SN123": {"sw_version": "1.2.3", "hw_version": "V1"}}
        await coordinator._async_sync_device_metadata(devices)

        mock_dev_reg.async_update_device.assert_not_called()


@pytest.mark.asyncio
async def test_async_sync_device_metadata_with_change():
    """Test that device registry is updated if versions differ."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), MagicMock(), mock_entry
    )

    mock_dev_reg = MagicMock()
    with (
        patch(
            "custom_components.hyxi_cloud.coordinator.dr.async_get",
            return_value=mock_dev_reg,
        ),
        patch(
            "custom_components.hyxi_cloud.coordinator.get_software_version",
            return_value="1.2.3",
        ),
    ):
        mock_device = MagicMock()
        mock_device.model = "Generic Model"
        mock_device.sw_version = "1.2.2"
        mock_device.hw_version = "V1"
        mock_device.id = "device_id"
        mock_dev_reg.async_get_device.return_value = mock_device

        devices = {
            "SN123": {
                "model": "HYX-H9K-HTA",
                "sw_version": "1.2.3",
                "hw_version": "V1",
            }
        }
        await coordinator._async_sync_device_metadata(devices)

        mock_dev_reg.async_update_device.assert_called_once_with(
            "device_id",
            model="HYX-H9K-HTA",
            sw_version="1.2.3",
            hw_version="V1",
        )


@pytest.mark.asyncio
async def test_async_sync_device_metadata_device_not_found():
    """Test that it handles case where device is not in registry."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), MagicMock(), mock_entry
    )

    mock_dev_reg = MagicMock()
    with patch(
        "custom_components.hyxi_cloud.coordinator.dr.async_get",
        return_value=mock_dev_reg,
    ):
        mock_dev_reg.async_get_device.return_value = None

        devices = {"SN123": {"sw_version": "1.2.3", "hw_version": "V1"}}
        await coordinator._async_sync_device_metadata(devices)

        mock_dev_reg.async_update_device.assert_not_called()


@pytest.mark.asyncio
async def test_async_update_data_empty_devices_warning():
    """Verify update warning when no devices are returned."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.get_all_device_data = AsyncMock(
        return_value={"data": {}, "attempts": 1}
    )

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    with patch("custom_components.hyxi_cloud.coordinator._LOGGER.warning") as mock_warn:
        result = await coordinator._async_update_data()
        assert result == {}
        mock_warn.assert_any_call(
            "HYXI Cloud returned success, but no plants or devices were found. "
            "If your developer email differs from your app email, you must share your Plant "
            "from the app to the developer email first."
        )


@pytest.mark.asyncio
async def test_async_update_data_merge_existing_metrics():
    """Verify that update merges new metrics with existing cached metrics and derived metrics."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.get_all_device_data = AsyncMock(
        return_value={
            "data": {
                "SN123": {
                    "device_type_code": "1",
                    "metrics": {"new_metric": "value_new", "overlapping": "newer"},
                }
            },
            "attempts": 1,
        }
    )
    mock_client.compute_derived_metrics.return_value = {"derived_key": "derived_value"}

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    # Pre-populate coordinator data
    coordinator.data = {
        "SN123": {"metrics": {"old_metric": "value_old", "overlapping": "older"}}
    }

    result = await coordinator._async_update_data()

    # Overlapping should be updated to "newer"
    # old_metric should be preserved
    # derived_key should be calculated and added
    expected_metrics = {
        "old_metric": "value_old",
        "overlapping": "newer",
        "new_metric": "value_new",
        "derived_key": "derived_value",
    }
    assert result["SN123"]["metrics"] == expected_metrics
    mock_client.compute_derived_metrics.assert_called_once_with(
        {
            "old_metric": "value_old",
            "overlapping": "newer",
            "new_metric": "value_new",
            "derived_key": "derived_value",
        },
        "1",
    )

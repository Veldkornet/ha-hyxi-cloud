"""Tests for the DataUpdateCoordinator logic."""
# pylint: disable=wrong-import-position

import importlib
import sys
from datetime import timedelta
from types import SimpleNamespace
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
        self.update_interval = update_interval

    def __class_getitem__(cls, item):
        return cls


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
# The coordinator catches HyxiAuthError, which must be a real exception class
# whichever stub (or the real package) is installed.
_hyxi_api: Any = sys.modules["hyxi_cloud_api"]
if not isinstance(getattr(_hyxi_api, "HyxiAuthError", None), type):
    _hyxi_api.HyxiAuthError = type("HyxiAuthError", (Exception,), {})


import custom_components.hyxi_cloud.coordinator as hc_coord  # pylint: disable=wrong-import-position

importlib.reload(hc_coord)


@pytest.fixture(autouse=True)
def no_retry_delays():
    """Retry polls and key re-checks without waiting."""
    with (
        patch.object(hc_coord, "POLL_RETRY_DELAY", 0),
        patch.object(hc_coord, "AUTH_RECHECK_DELAY", 0),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_store():
    """Mock the Store class."""
    with patch("custom_components.hyxi_cloud.coordinator.Store") as mock_store:
        mock_instance = mock_store.return_value
        mock_instance.async_save = AsyncMock()
        mock_instance.async_load = AsyncMock(return_value=None)
        yield mock_store


@pytest.mark.asyncio
async def test_a_failed_poll_attempt_is_retried():
    """A transient poll failure is retried within the same update, and the
    attempt that succeeded is reported."""
    mock_entry = MagicMock()
    mock_entry.data = {"access_key": "ak", "secret_key": "sk", "base_url": "url"}
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    devices = {"SN123": {"metrics": {"tinv": "45.0"}}}
    mock_client.poll_devices = AsyncMock(side_effect=[TimeoutError("boom"), devices])

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )
    coordinator._async_sync_device_metadata = AsyncMock()

    assert coordinator.hyxi_metadata["last_attempts"] == 0
    assert coordinator.hyxi_metadata["api_status"] == "Starting"

    assert await coordinator._async_update_data() == devices
    assert coordinator.hyxi_metadata["last_attempts"] == 2
    assert coordinator.hyxi_metadata["api_status"] == "Online"


def _auth_coordinator(fetch_effects):
    """A coordinator whose client answers successive fetches with
    fetch_effects."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock(side_effect=fetch_effects)
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )
    return coordinator, mock_client


@pytest.mark.asyncio
@patch.object(hc_coord, "AUTH_RECHECK_DELAY", 0)
async def test_key_rejection_repeated_on_retry_raises_auth_failed():
    """Keys rejected on two fetches in a row raise ConfigEntryAuthFailed."""
    rejected = hc_coord.HyxiAuthError("rejected")
    coordinator, _ = _auth_coordinator([rejected, rejected])

    with pytest.raises(hc_coord.ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
@patch.object(hc_coord, "AUTH_RECHECK_DELAY", 0)
async def test_key_rejection_followed_by_network_failure_is_not_auth_failed():
    """If the retry cannot reach HYXI, the poll fails as a connection error
    rather than asking the user to re-enter keys that may be fine."""
    coordinator, _ = _auth_coordinator(
        [
            hc_coord.HyxiAuthError("rejected"),
            TimeoutError(),
            TimeoutError(),
            TimeoutError(),
        ]
    )

    with pytest.raises(hc_coord.UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
@patch.object(hc_coord, "AUTH_RECHECK_DELAY", 0)
async def test_one_off_key_rejection_returns_the_retried_fetch():
    """A rejection the retry does not repeat returns the retried data."""
    devices = {"SN1": {"metrics": {"last_seen": "now", "gridP": 1}}}
    coordinator, mock_client = _auth_coordinator(
        [hc_coord.HyxiAuthError("rejected"), devices]
    )
    coordinator._async_sync_device_metadata = AsyncMock()
    coordinator.device_store = MagicMock(async_save=AsyncMock())

    assert await coordinator._async_update_data() == devices
    assert mock_client.poll_devices.await_count == 2


@pytest.mark.asyncio
async def test_every_poll_attempt_failing_fails_the_update():
    """When every poll attempt fails, the update fails as unreachable."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock(side_effect=TimeoutError("boom"))

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    with pytest.raises(hc_coord.UpdateFailed) as excinfo:
        await coordinator._async_update_data()

    assert "HYXI Cloud unreachable" in str(excinfo.value)
    assert coordinator.hyxi_metadata["last_attempts"] == 3
    assert mock_client.poll_devices.await_count == hc_coord.POLL_ATTEMPTS


@pytest.mark.asyncio
async def test_async_update_data_success():
    """Test successful data update with non-empty metrics."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock(
        return_value={"SN123": {"metrics": {"tinv": "45.0"}}}
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
    mock_client.poll_devices = AsyncMock(
        return_value={"SN123": {"metrics": {"last_seen": "2026-05-22"}}}
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
    mock_client.poll_devices = AsyncMock(
        return_value={"SN123": {"device_type_code": "3", "metrics": {}}}
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
        mock_dev_reg.async_get_device_by_identifier.return_value = mock_device

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
        mock_dev_reg.async_get_device_by_identifier.return_value = mock_device

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
        mock_dev_reg.async_get_device_by_identifier.return_value = None

        devices = {"SN123": {"sw_version": "1.2.3", "hw_version": "V1"}}
        await coordinator._async_sync_device_metadata(devices)

        mock_dev_reg.async_update_device.assert_not_called()


@pytest.mark.asyncio
async def test_async_update_data_empty_devices_warning():
    """Verify update warning when no devices are returned."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock(return_value={})

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
    mock_client.poll_devices = AsyncMock(
        return_value={
            "SN123": {
                "device_type_code": "1",
                "metrics": {"new_metric": "value_new", "overlapping": "newer"},
            }
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


def test_is_cache_expired_none():
    """A missing cache payload is treated as expired."""
    assert hc_coord._is_cache_expired(None) is True  # pylint: disable=protected-access


def test_is_cache_expired_old_format():
    """A bare device dict (old cache format, no 'cached_at') is treated as expired."""
    raw: dict[str, Any] = {"SN123": {"metrics": {}}}
    assert hc_coord._is_cache_expired(raw) is True  # pylint: disable=protected-access


def test_is_cache_expired_fresh():
    """A recently-cached payload is not expired."""
    raw = {"cached_at": hc_coord.dt_util.utcnow().isoformat(), "devices": {}}
    assert hc_coord._is_cache_expired(raw) is False  # pylint: disable=protected-access


def test_is_cache_expired_stale():
    """A payload older than CACHE_MAX_AGE is expired."""
    stale_time = hc_coord.dt_util.utcnow() - hc_coord.CACHE_MAX_AGE - timedelta(days=1)
    raw = {"cached_at": stale_time.isoformat(), "devices": {}}
    assert hc_coord._is_cache_expired(raw) is True  # pylint: disable=protected-access


def test_is_cache_expired_unparseable_timestamp():
    """A malformed 'cached_at' value is treated as expired rather than raising."""
    raw = {"cached_at": "not-a-timestamp", "devices": {}}
    assert hc_coord._is_cache_expired(raw) is True  # pylint: disable=protected-access


def test_extract_cached_devices_new_format():
    """New-format cache payloads unwrap to the inner 'devices' dict."""
    devices: dict[str, Any] = {"SN123": {"metrics": {}}}
    raw = {"cached_at": "2026-01-01T00:00:00+00:00", "devices": devices}
    assert hc_coord._extract_cached_devices(raw) is devices  # pylint: disable=protected-access


def test_extract_cached_devices_old_format():
    """Old-format cache payloads (bare device dict) pass through unchanged."""
    raw: dict[str, Any] = {"SN123": {"metrics": {}}}
    assert hc_coord._extract_cached_devices(raw) is raw  # pylint: disable=protected-access


def test_extract_cached_devices_none():
    """A missing cache payload extracts to None."""
    assert hc_coord._extract_cached_devices(None) is None  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_async_preload_cache_seeds_data_when_fresh():
    """A fresh cache pre-seeds coordinator.data before the first API call."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), MagicMock(), mock_entry
    )

    devices = {"SN123": {"metrics": {"tinv": "45.0"}}}
    raw = {"cached_at": hc_coord.dt_util.utcnow().isoformat(), "devices": devices}
    coordinator.device_store.async_load = AsyncMock(return_value=raw)

    await coordinator.async_preload_cache()

    assert coordinator.data == devices
    assert coordinator.hyxi_metadata["api_status"] == "Starting (cached)"
    assert coordinator.hyxi_metadata["cache_active"] is True


def test_drop_stale_settings_markers_removes_the_monotonic_timestamp():
    """_settings_read_at is a time.monotonic() value from a previous
    process -- meaningless, and unsafe to compare against, once reloaded
    into a new one."""
    devices = {
        "SN1": {"metrics": {"tinv": "45.0", "_settings_read_at": 12345.0}},
        "SN2": {"metrics": {}},
        "SN3": {},
    }

    result = hc_coord._drop_stale_settings_markers(devices)

    assert result is devices
    assert "_settings_read_at" not in devices["SN1"]["metrics"]
    assert devices["SN1"]["metrics"]["tinv"] == "45.0"
    assert not devices["SN2"]["metrics"]
    assert not devices["SN3"]


@pytest.mark.asyncio
async def test_async_preload_cache_strips_settings_read_at_from_a_modbus_device():
    """A Modbus device's settings-freshness marker must not survive a
    restart in coordinator.data -- see _drop_stale_settings_markers. Without
    this, SettingsSyncMixin would treat a days-old cached settings snapshot
    as though this new process had just confirmed it."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), MagicMock(), mock_entry
    )

    devices = {"SN123": {"metrics": {"self_use_soc": 10, "_settings_read_at": 999.0}}}
    raw = {"cached_at": hc_coord.dt_util.utcnow().isoformat(), "devices": devices}
    coordinator.device_store.async_load = AsyncMock(return_value=raw)

    await coordinator.async_preload_cache()

    assert "_settings_read_at" not in coordinator.data["SN123"]["metrics"]
    assert coordinator.data["SN123"]["metrics"]["self_use_soc"] == 10


@pytest.mark.asyncio
async def test_async_preload_cache_skips_when_expired():
    """An expired cache does not pre-seed coordinator.data."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), MagicMock(), mock_entry
    )

    stale_time = hc_coord.dt_util.utcnow() - hc_coord.CACHE_MAX_AGE - timedelta(days=1)
    raw = {
        "cached_at": stale_time.isoformat(),
        "devices": {"SN123": {"metrics": {}}},
    }
    coordinator.device_store.async_load = AsyncMock(return_value=raw)

    await coordinator.async_preload_cache()

    assert coordinator.hyxi_metadata["cache_active"] is False
    assert coordinator.hyxi_metadata["api_status"] == "Starting"


@pytest.mark.asyncio
async def test_async_preload_cache_skips_when_empty():
    """No cache on disk leaves the coordinator in its default starting state."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), MagicMock(), mock_entry
    )
    coordinator.device_store.async_load = AsyncMock(return_value=None)

    await coordinator.async_preload_cache()

    assert coordinator.hyxi_metadata["cache_active"] is False
    assert coordinator.data == {}


@pytest.mark.asyncio
async def test_async_preload_cache_handles_load_failure():
    """A storage read failure is swallowed and leaves cache_active False."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), MagicMock(), mock_entry
    )
    coordinator.device_store.async_load = AsyncMock(side_effect=OSError("disk error"))

    await coordinator.async_preload_cache()

    assert coordinator.hyxi_metadata["cache_active"] is False


@pytest.mark.asyncio
async def test_async_update_data_falls_back_to_fresh_cache_on_error():
    """A fetch failure falls back to a fresh on-disk cache instead of raising."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock(side_effect=TimeoutError("boom"))

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )
    cached_devices = {"SN123": {"metrics": {"tinv": "1.0"}}}
    raw = {
        "cached_at": hc_coord.dt_util.utcnow().isoformat(),
        "devices": cached_devices,
    }
    coordinator.device_store.async_load = AsyncMock(return_value=raw)

    result = await coordinator._async_update_data()

    assert result == cached_devices
    assert coordinator.hyxi_metadata["api_status"] == "Offline"
    assert coordinator.hyxi_metadata["cache_active"] is True


@pytest.mark.asyncio
async def test_async_update_data_ignores_expired_cache_on_error():
    """A fetch failure with only an expired cache still raises, not masking the error."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock(side_effect=TimeoutError("boom"))

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )
    stale_time = hc_coord.dt_util.utcnow() - hc_coord.CACHE_MAX_AGE - timedelta(days=1)
    raw = {
        "cached_at": stale_time.isoformat(),
        "devices": {"SN123": {"metrics": {}}},
    }
    coordinator.device_store.async_load = AsyncMock(return_value=raw)

    with pytest.raises(hc_coord.UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator.hyxi_metadata["cache_active"] is False


@pytest.mark.asyncio
async def test_async_update_data_cache_fallback_read_itself_fails():
    """If reading the cache during fallback also raises, the original error still propagates."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock(side_effect=TimeoutError("boom"))

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )
    coordinator.device_store.async_load = AsyncMock(side_effect=OSError("disk error"))

    with pytest.raises(hc_coord.UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator.hyxi_metadata["cache_active"] is False


@pytest.mark.asyncio
async def test_async_update_data_unhandled_exception_type():
    """An exception type outside ClientError/TimeoutError/UpdateFailed hits the generic branch."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock(side_effect=ValueError("weird failure"))

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )

    with pytest.raises(hc_coord.UpdateFailed) as excinfo:
        await coordinator._async_update_data()

    assert "Unhandled exception: weird failure" in str(excinfo.value)
    assert coordinator.hyxi_metadata["last_attempts"] == 1
    assert coordinator.hyxi_metadata["api_status"] == "Error"


@pytest.mark.asyncio
async def test_async_update_data_save_failure_still_returns_devices():
    """A cache-write failure is logged but does not prevent returning fresh devices."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock(
        return_value={"SN123": {"metrics": {"tinv": "45.0"}}}
    )

    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )
    coordinator.device_store.async_save = AsyncMock(side_effect=OSError("disk full"))

    result = await coordinator._async_update_data()

    assert result["SN123"]["metrics"] == {"tinv": "45.0"}
    assert coordinator.hyxi_metadata["api_status"] == "Online"


def _discovery_coordinator(results):
    """A discovery coordinator whose client answers successive discoveries
    with results (a list of results or exceptions)."""
    mock_entry = MagicMock()
    mock_entry.options = {}
    mock_client = MagicMock()
    mock_client.discover_devices = AsyncMock(side_effect=results)
    coordinator = hc_coord.HyxiDiscoveryCoordinator(
        MagicMock(), mock_client, mock_entry
    )
    return coordinator, mock_client


def _discovered(complete=True):
    return SimpleNamespace(devices={"SN1": {}}, complete=complete)


@pytest.mark.asyncio
async def test_discovery_runs_hourly_once_complete():
    """A complete discovery keeps the hourly schedule and passes the
    back-discovery option through."""
    coordinator, mock_client = _discovery_coordinator([_discovered()])
    coordinator.entry.options = {hc_coord.CONF_BACK_DISCOVERY: True}

    result = await coordinator._async_update_data()

    assert result.complete
    assert coordinator.update_interval == hc_coord.DISCOVERY_INTERVAL
    mock_client.discover_devices.assert_awaited_once_with(allow_back_discovery=True)


@pytest.mark.asyncio
async def test_incomplete_discovery_retries_sooner_with_backoff():
    """An incomplete or failed discovery is retried after the retry delay,
    doubling up to the hourly interval, and a complete one resets it."""
    retry = hc_coord.DISCOVERY_RETRY
    coordinator, _ = _discovery_coordinator(
        [
            _discovered(complete=False),
            TimeoutError("boom"),
            _discovered(complete=False),
            _discovered(),
            _discovered(complete=False),
        ]
    )

    await coordinator._async_update_data()
    assert coordinator.update_interval == retry
    with pytest.raises(hc_coord.UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.update_interval == retry * 2
    await coordinator._async_update_data()
    assert coordinator.update_interval == retry * 4
    await coordinator._async_update_data()
    assert coordinator.update_interval == hc_coord.DISCOVERY_INTERVAL
    await coordinator._async_update_data()
    assert coordinator.update_interval == retry


@pytest.mark.asyncio
async def test_discovery_backoff_stops_at_the_hourly_interval():
    """The retry delay never grows past the hourly interval."""
    coordinator, _ = _discovery_coordinator([_discovered(complete=False)] * 6)

    for _ in range(6):
        await coordinator._async_update_data()

    assert coordinator.update_interval == hc_coord.DISCOVERY_INTERVAL


@pytest.mark.asyncio
async def test_discovery_key_rejection_on_retry_raises_auth_failed():
    """Keys rejected on two discoveries in a row start reauth."""
    rejected = hc_coord.HyxiAuthError("rejected")
    coordinator, _ = _discovery_coordinator([rejected, rejected])

    with pytest.raises(hc_coord.ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_polling_before_a_discovery_falls_back_to_the_cache():
    """Until a discovery has succeeded there is nothing to poll, so the
    update fails over to the cached devices without polling."""
    mock_entry = MagicMock()
    mock_entry.options = {"update_interval": 5}
    mock_client = MagicMock()
    mock_client.poll_devices = AsyncMock()
    coordinator = hc_coord.HyxiDataUpdateCoordinator(
        MagicMock(), mock_client, mock_entry
    )
    coordinator.discovery = MagicMock(data=None)
    cached_devices = {"SN123": {"metrics": {"tinv": "1.0"}}}
    coordinator.device_store.async_load = AsyncMock(
        return_value={
            "cached_at": hc_coord.dt_util.utcnow().isoformat(),
            "devices": cached_devices,
        }
    )
    coordinator._async_sync_device_metadata = AsyncMock()

    assert await coordinator._async_update_data() == cached_devices
    assert coordinator.hyxi_metadata["api_status"] == "Offline"
    mock_client.poll_devices.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [SimpleNamespace(devices={}, complete=False), KeyError("model")],
    ids=["incomplete-and-empty", "unexpected-error"],
)
async def test_a_discovery_that_lists_nothing_fails_and_retries_sooner(outcome):
    """A discovery that could not list any device, or that broke, fails
    rather than reporting no devices, and is retried after the retry delay."""
    coordinator, _ = _discovery_coordinator([outcome])

    with pytest.raises((hc_coord.UpdateFailed, KeyError)):
        await coordinator._async_update_data()

    assert coordinator.update_interval == hc_coord.DISCOVERY_RETRY

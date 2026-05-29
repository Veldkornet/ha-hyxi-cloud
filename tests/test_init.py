"""Tests for the initial setup of the HYXI Cloud integration."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Define AUTHORITATIVE local exceptions first to avoid MagicMock TypeErrors in pytest.raises
class ConfigEntryAuthFailed(Exception):
    """Authoritative local class for auth failure."""


class ConfigEntryNotReady(Exception):
    """Authoritative local class for entry not ready."""


class UpdateFailed(Exception):
    """Authoritative local class for update failed."""


# We MUST define the initial mocks for sys.modules if they aren't there because the test
# might be run individually, meaning other tests haven't put them there yet.

if "homeassistant.exceptions" not in sys.modules or not hasattr(
    sys.modules["homeassistant.exceptions"], "ConfigEntryAuthFailed"
):
    # Harden the mock to behave like a module
    mock_ha = MagicMock()
    mock_ha.__path__ = []
    mock_ha.__spec__ = MagicMock()
    if "homeassistant" not in sys.modules:
        sys.modules["homeassistant"] = mock_ha
    if "homeassistant.components" not in sys.modules:
        sys.modules["homeassistant.components"] = mock_ha
    if "homeassistant.const" not in sys.modules:
        sys.modules["homeassistant.const"] = mock_ha
    if "homeassistant.core" not in sys.modules:
        sys.modules["homeassistant.core"] = mock_ha
    if "homeassistant.exceptions" not in sys.modules:
        sys.modules["homeassistant.exceptions"] = mock_ha
    if "homeassistant.helpers" not in sys.modules:
        sys.modules["homeassistant.helpers"] = mock_ha
    if "homeassistant.util" not in sys.modules:
        sys.modules["homeassistant.util"] = mock_ha

    sys.modules[
        "homeassistant.exceptions"
    ].ConfigEntryAuthFailed = ConfigEntryAuthFailed  # type: ignore[attr-defined]
    sys.modules["homeassistant.exceptions"].ConfigEntryNotReady = ConfigEntryNotReady  # type: ignore[attr-defined]
    sys.modules["homeassistant.exceptions"].UpdateFailed = UpdateFailed  # type: ignore[attr-defined]

    # Also inject into the specific locations expected by the component
    if "homeassistant.config_entries" not in sys.modules:
        sys.modules["homeassistant.config_entries"] = MagicMock()
    sys.modules[
        "homeassistant.config_entries"
    ].ConfigEntryAuthFailed = ConfigEntryAuthFailed  # type: ignore[attr-defined]
    sys.modules[
        "homeassistant.config_entries"
    ].ConfigEntryNotReady = ConfigEntryNotReady  # type: ignore[attr-defined]

    if "homeassistant.helpers.update_coordinator" not in sys.modules:
        sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
    sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed = UpdateFailed  # type: ignore[attr-defined]


if "homeassistant.helpers.aiohttp_client" not in sys.modules:
    sys.modules["homeassistant.helpers.aiohttp_client"] = MagicMock()

if "aiohttp" not in sys.modules:
    sys.modules["aiohttp"] = MagicMock()
    sys.modules["aiohttp"].ClientError = type("ClientError", (Exception,), {})  # type: ignore[attr-defined]

mock_api = MagicMock()
mock_api.__name__ = "hyxi_cloud_api"
mock_api.__version__ = "1.0.4"
sys.modules["hyxi_cloud_api"] = mock_api

import custom_components.hyxi_cloud.__init__ as hc_init  # pylint: disable=wrong-import-position

# DIRECT NAMESPACE INJECTION: Force the component to use our authoritative classes
# This is the only way to guarantee class identity consistency in a mocked environment.
hc_init.ConfigEntryAuthFailed = ConfigEntryAuthFailed
hc_init.ConfigEntryNotReady = ConfigEntryNotReady
hc_init.UpdateFailed = UpdateFailed


# Redefine for local use is now redundant but kept for legacy nomenclature compatibility
LocalEntryAuthFailed = ConfigEntryAuthFailed
LocalEntryNotReady = ConfigEntryNotReady
LocalUpdateFailed = UpdateFailed


async_setup_entry = hc_init.async_setup_entry
async_unload_entry = hc_init.async_unload_entry
async_reload_entry = hc_init.async_reload_entry

# Inject back into the module if they were mocked by mistake during the import process

from custom_components.hyxi_cloud.const import (  # pylint: disable=wrong-import-position # pylint: disable=wrong-import-position
    DOMAIN,
    PLATFORMS,
)


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = AsyncMock()
    return hass


@pytest.fixture
def mock_entry():
    from custom_components.hyxi_cloud.const import CONF_ACCESS_KEY, CONF_SECRET_KEY

    entry = MagicMock()
    entry.data = {
        CONF_ACCESS_KEY: "test_access",
        CONF_SECRET_KEY: "test_secret",
    }
    entry.options = {}  # Empty options — no EM enabled
    entry.entry_id = "test_id"
    entry.add_update_listener = MagicMock()
    entry.async_on_unload = MagicMock()
    return entry


@pytest.mark.asyncio
async def test_async_setup_entry_success(mock_hass, mock_entry):
    """Test successful setup of entry."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
        patch("custom_components.hyxi_cloud.__init__.dr.async_get") as mock_dr_get,
        patch("custom_components.hyxi_cloud.__init__.async_reload_entry"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.engine = None  # No EM engine
        mock_coordinator.data = {
            "TEST_SN_1": {
                "device_name": "Test Device 1",
                "model": "Model 1",
                "sw_version": "v1",
                "hw_version": "hw1",
                "metrics": {"batSn": "TEST_BAT_1"},
            },
            "TEST_SN_2": {"metrics": {}},
        }

        mock_registry = MagicMock()
        mock_dr_get.return_value = mock_registry

        result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True

        # Check coordinator is in hass.data
        assert DOMAIN in mock_hass.data
        assert mock_entry.entry_id in mock_hass.data[DOMAIN]
        assert mock_hass.data[DOMAIN][mock_entry.entry_id] is mock_coordinator

        # Check parent devices and child device registration
        # Pass 1: SN_1, SN_2
        # Pass 2: BAT_1 (linked to SN_1)
        assert mock_registry.async_get_or_create.call_count == 3

        # We can optionally inspect the calls made to async_get_or_create:
        calls = mock_registry.async_get_or_create.call_args_list
        # Call 1: Base TEST_SN_1
        assert calls[0].kwargs["identifiers"] == {(DOMAIN, "TEST_SN_1")}
        assert calls[0].kwargs["name"] == "Test Device 1"
        assert calls[0].kwargs["serial_number"] == "TEST_SN_1"

        # Call 2: Base TEST_SN_2
        assert calls[1].kwargs["identifiers"] == {(DOMAIN, "TEST_SN_2")}
        assert calls[1].kwargs["name"] == "Device TEST_SN_2"

        # Call 3: Battery TEST_BAT_1 (Pass 2)
        assert calls[2].kwargs["identifiers"] == {(DOMAIN, "TEST_BAT_1")}
        assert calls[2].kwargs["via_device"] == (DOMAIN, "TEST_SN_1")
        assert calls[2].kwargs["serial_number"] == "TEST_BAT_1"

        # Check platforms setup forwarded
        mock_hass.config_entries.async_forward_entry_setups.assert_called_once_with(
            mock_entry, PLATFORMS
        )

        # Check listener added
        mock_entry.add_update_listener.assert_called_once()
        mock_entry.async_on_unload.assert_called_once_with(
            mock_entry.add_update_listener.return_value
        )


@pytest.mark.asyncio
async def test_async_setup_entry_parent_link(mock_hass, mock_entry):
    """Test successful setup of entry with parentSn relationship."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
        patch("custom_components.hyxi_cloud.__init__.dr.async_get") as mock_dr_get,
        patch("custom_components.hyxi_cloud.__init__.async_reload_entry"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.engine = None  # No EM engine
        mock_coordinator.data = {
            "CHILD_SN_1": {
                "device_name": "Child Device",
                "metrics": {"parentSn": "PARENT_SN_1"},
            },
            "PARENT_SN_1": {"device_name": "Parent Device", "metrics": {}},
        }

        mock_registry = MagicMock()
        mock_dr_get.return_value = mock_registry

        result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True

        # Call count: 2 (Pass 1) + 1 (Pass 2 for ParentSn) = 3
        assert mock_registry.async_get_or_create.call_count == 3
        calls = mock_registry.async_get_or_create.call_args_list

        # Verify child links via_device to parent in Pass 2
        # Call 3 is the update call for CHILD_SN_1 in Pass 2
        assert calls[2].kwargs["identifiers"] == {(DOMAIN, "CHILD_SN_1")}
        assert calls[2].kwargs["via_device"] == (DOMAIN, "PARENT_SN_1")

        # Check platforms setup forwarded
        mock_hass.config_entries.async_forward_entry_setups.assert_called_once_with(
            mock_entry, PLATFORMS
        )

        # Check listener added
        mock_entry.add_update_listener.assert_called_once()
        mock_entry.async_on_unload.assert_called_once_with(
            mock_entry.add_update_listener.return_value
        )


@pytest.mark.asyncio
async def test_async_setup_entry_auth_failed(mock_hass, mock_entry):
    """Test setup failing due to authentication error."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryAuthFailed
        )

        with patch(
            "custom_components.hyxi_cloud.__init__._LOGGER.error"
        ) as mock_logger:
            with pytest.raises(ConfigEntryAuthFailed):
                await async_setup_entry(mock_hass, mock_entry)

            mock_logger.assert_called_with("Authentication failed during setup")


@pytest.mark.asyncio
async def test_async_setup_entry_not_ready(mock_hass, mock_entry):
    """Test setup failing due to general exception."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=UpdateFailed("Timeout")
        )

        with patch(
            "custom_components.hyxi_cloud.__init__._LOGGER.warning"
        ) as mock_logger:
            with pytest.raises(ConfigEntryNotReady) as exc:
                await async_setup_entry(mock_hass, mock_entry)

            assert "Connection error: Timeout" in str(exc.value)
            mock_logger.assert_called_with(
                "HYXI Cloud not ready: %s",
                mock_coordinator.async_config_entry_first_refresh.side_effect,
            )


@pytest.mark.asyncio
async def test_async_setup_entry_missing_keys(mock_hass):
    """Test setup failing due to missing keys."""
    entry = MagicMock()
    entry.data = {}

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.error") as mock_logger:
        result = await async_setup_entry(mock_hass, entry)
        assert result is False
        mock_logger.assert_called_with(
            "HYXI Integration could not find Access/Secret keys."
        )


@pytest.mark.asyncio
async def test_async_unload_entry_success(mock_hass, mock_entry):
    """Test successful unload of a config entry."""
    mock_coordinator = MagicMock()
    mock_coordinator.protection_controllers = {}
    mock_coordinator.engine = None
    mock_hass.data[DOMAIN] = {mock_entry.entry_id: mock_coordinator}
    mock_hass.config_entries.async_unload_platforms.return_value = True

    assert await async_unload_entry(mock_hass, mock_entry) is True

    mock_hass.config_entries.async_unload_platforms.assert_called_once_with(
        mock_entry, PLATFORMS
    )
    assert mock_entry.entry_id not in mock_hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_unload_entry_failure(mock_hass, mock_entry):
    """Test failed unload of a config entry."""
    mock_coordinator = MagicMock()
    mock_coordinator.protection_controllers = {}
    mock_coordinator.engine = None
    mock_hass.data[DOMAIN] = {mock_entry.entry_id: mock_coordinator}
    mock_hass.config_entries.async_unload_platforms.return_value = False

    assert await async_unload_entry(mock_hass, mock_entry) is False

    mock_hass.config_entries.async_unload_platforms.assert_called_once_with(
        mock_entry, PLATFORMS
    )
    assert mock_entry.entry_id in mock_hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_reload_entry(mock_hass, mock_entry):
    """Test reload config entry."""

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.debug") as mock_logger:
        await async_reload_entry(mock_hass, mock_entry)

        mock_logger.assert_called_with(
            "HYXI: Options updated, reloading integration to apply new settings"
        )
        mock_hass.config_entries.async_reload.assert_called_once_with(
            mock_entry.entry_id
        )


@pytest.mark.asyncio
async def test_async_setup_entry_battery_first_class_device(mock_hass, mock_entry):
    """Test that bat_sn already in coordinator.data is linked, not re-stubbed.

    When a battery is discovered as a standalone device (it appears in
    coordinator.data with full metadata), Pass 2 must only set the via_device
    link rather than creating a sparse 'Battery {sn}' stub that would
    overwrite the richer entry registered in Pass 1.
    """
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
        patch("custom_components.hyxi_cloud.__init__.dr.async_get") as mock_dr_get,
        patch("custom_components.hyxi_cloud.__init__.async_reload_entry"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.engine = None  # No EM engine
        mock_coordinator.data = {
            # Inverter knows about its battery via metrics
            "INVERTER_SN": {
                "device_name": "Hybrid Inverter",
                "model": "HYB-5K",
                "sw_version": "v2",
                "hw_version": "hw2",
                "metrics": {"batSn": "BATTERY_SN"},
            },
            # Battery is also a first-class device in its own right
            "BATTERY_SN": {
                "device_name": "Battery Pack",
                "model": "ESS-10",
                "sw_version": "v1",
                "hw_version": "hw1",
                "metrics": {},
            },
        }

        mock_registry = MagicMock()
        mock_dr_get.return_value = mock_registry

        result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True

        calls = mock_registry.async_get_or_create.call_args_list

        # Pass 1: 2 calls (INVERTER_SN, BATTERY_SN)
        # Pass 2: 1 call — link BATTERY_SN via_device to INVERTER_SN (guard path)
        assert mock_registry.async_get_or_create.call_count == 3

        # Pass 1 — INVERTER_SN registered with full metadata
        assert calls[0].kwargs["identifiers"] == {(DOMAIN, "INVERTER_SN")}
        assert calls[0].kwargs["name"] == "Hybrid Inverter"

        # Pass 1 — BATTERY_SN registered with full metadata (not a stub)
        assert calls[1].kwargs["identifiers"] == {(DOMAIN, "BATTERY_SN")}
        assert calls[1].kwargs["name"] == "Battery Pack"
        assert calls[1].kwargs["model"] == "ESS-10"

        # Pass 2 — guard path: link only, no name/model/serial overwrite
        assert calls[2].kwargs["identifiers"] == {(DOMAIN, "BATTERY_SN")}
        assert calls[2].kwargs["via_device"] == (DOMAIN, "INVERTER_SN")
        assert "name" not in calls[2].kwargs
        assert "model" not in calls[2].kwargs
        assert "serial_number" not in calls[2].kwargs


@pytest.mark.asyncio
async def test_async_setup_battery_protection_options(mock_hass, mock_entry):
    """Test that battery protection is set up only when option is enabled."""
    from custom_components.hyxi_cloud.__init__ import _async_setup_battery_protection

    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "INVERTER_SN": {
            "device_name": "Hybrid Inverter",
            "deviceType": "HYBRID_INVERTER",
            "model": "H5K-HT",
            "metrics": {},
        }
    }
    mock_coordinator.protection_controllers = {}

    # Case 1: Option is False (default)
    mock_entry.options = {"enable_battery_control": False}
    mock_coordinator.entry = mock_entry

    await _async_setup_battery_protection(mock_hass, mock_coordinator)
    assert len(mock_coordinator.protection_controllers) == 0

    # Case 2: Option is True
    mock_entry.options = {"enable_battery_control": True}
    with patch(
        "custom_components.hyxi_cloud.__init__.HyxiBatteryProtectionController"
    ) as mock_controller_class:
        mock_controller = mock_controller_class.return_value
        mock_controller.async_start = AsyncMock()

        await _async_setup_battery_protection(mock_hass, mock_coordinator)

        assert "INVERTER_SN" in mock_coordinator.protection_controllers
        mock_controller.async_start.assert_called_once()

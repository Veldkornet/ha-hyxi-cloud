"""Tests for the initial setup of the HYXI Cloud integration."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.helpers.update_coordinator import UpdateFailed

# We MUST define the initial mocks for sys.modules if they aren't there because the test
# might be run individually, meaning other tests haven't put them there yet.

if "homeassistant.exceptions" not in sys.modules or not hasattr(
    sys.modules["homeassistant.exceptions"], "ConfigEntryAuthFailed"
):
    mock_ha = MagicMock()
    mock_ha.__path__ = []
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

    class ConfigEntryAuthFailed(Exception):  # pylint: disable=function-redefined
        pass

    class ConfigEntryNotReady(Exception):  # pylint: disable=function-redefined
        pass

    sys.modules[
        "homeassistant.exceptions"
    ].ConfigEntryAuthFailed = ConfigEntryAuthFailed
    sys.modules["homeassistant.exceptions"].ConfigEntryNotReady = ConfigEntryNotReady


class LocalUpdateFailed(Exception):
    """Local fallback for update failed."""


if "homeassistant.helpers.update_coordinator" not in sys.modules:
    sys.modules["homeassistant.helpers.update_coordinator"] = mock_ha

if "homeassistant.helpers.aiohttp_client" not in sys.modules:
    sys.modules["homeassistant.helpers.aiohttp_client"] = MagicMock()

if "aiohttp" not in sys.modules:
    sys.modules["aiohttp"] = MagicMock()
    sys.modules["aiohttp"].ClientError = type("ClientError", (Exception,), {})

mock_api = MagicMock()
mock_api.__name__ = "hyxi_cloud_api"
mock_api.__version__ = "1.0.4"
sys.modules["hyxi_cloud_api"] = mock_api

# Now we can safely import our component code
import custom_components.hyxi_cloud.__init__ as hc_init  # pylint: disable=wrong-import-position # noqa: E402


# Redefine for local use to ensure consistency with legacy nomenclature if needed
LocalEntryAuthFailed = ConfigEntryAuthFailed
LocalEntryNotReady = ConfigEntryNotReady


async_setup_entry = hc_init.async_setup_entry
async_unload_entry = hc_init.async_unload_entry
async_reload_entry = hc_init.async_reload_entry

# Inject back into the module if they were mocked by mistake during the import process

from custom_components.hyxi_cloud.const import (  # pylint: disable=wrong-import-position # noqa: E402  # pylint: disable=wrong-import-position # noqa: E402
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
    mock_hass.data[DOMAIN] = {mock_entry.entry_id: MagicMock()}
    mock_hass.config_entries.async_unload_platforms.return_value = True

    assert await async_unload_entry(mock_hass, mock_entry) is True

    mock_hass.config_entries.async_unload_platforms.assert_called_once_with(
        mock_entry, PLATFORMS
    )
    assert mock_entry.entry_id not in mock_hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_unload_entry_failure(mock_hass, mock_entry):
    """Test failed unload of a config entry."""
    mock_hass.data[DOMAIN] = {mock_entry.entry_id: MagicMock()}
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

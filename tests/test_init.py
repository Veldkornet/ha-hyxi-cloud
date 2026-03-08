"""Tests for the __init__ setup logic."""

import sys
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# We mock the external API dependency since it is not part of HA
mock_api = MagicMock()
sys.modules["hyxi_cloud_api"] = mock_api

# This test requires real exceptions to be thrown and caught in `__init__.py`.
# Because `pytest` runs `test_coordinator.py` first (which completely mocks `sys.modules["homeassistant.exceptions"]`
# with a `MagicMock`), by the time we reach this test, `ConfigEntryAuthFailed` inside
# `custom_components/hyxi_cloud/__init__.py` is a mock, breaking `try/except` blocks.
#
# The safest fix is to patch the exceptions module *inside* the integration namespace during the tests.

class FakeAuthFailed(Exception):
    pass

class FakeNotReady(Exception):
    pass


@pytest.fixture(autouse=True)
def unmock_exceptions():
    """Force __init__.py to use real Python Exceptions for its except blocks."""
    with patch("custom_components.hyxi_cloud.ConfigEntryAuthFailed", FakeAuthFailed), \
         patch("custom_components.hyxi_cloud.ConfigEntryNotReady", FakeNotReady):
        yield


from custom_components.hyxi_cloud import (
    async_setup_entry,
    async_unload_entry,
    async_reload_entry,
)
from custom_components.hyxi_cloud.const import CONF_ACCESS_KEY, CONF_SECRET_KEY, DOMAIN


@pytest.mark.asyncio
async def test_async_setup_entry_missing_keys():
    """Test that setup fails when keys are missing."""
    hass = MagicMock()
    entry = MagicMock()

    # Test with no keys
    entry.data = {}
    result = await async_setup_entry(hass, entry)
    assert result is False

    # Test with only access key
    entry.data = {CONF_ACCESS_KEY: "ak"}
    result = await async_setup_entry(hass, entry)
    assert result is False

    # Test with only secret key
    entry.data = {CONF_SECRET_KEY: "sk"}
    result = await async_setup_entry(hass, entry)
    assert result is False


@pytest.mark.asyncio
async def test_async_setup_entry_auth_failed():
    """Test that setup handles authentication failures."""
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"}

    with patch("custom_components.hyxi_cloud.HyxiDataUpdateCoordinator") as mock_coordinator_class, \
         patch("custom_components.hyxi_cloud.async_get_clientsession"):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=FakeAuthFailed)

        with pytest.raises(FakeAuthFailed):
            await async_setup_entry(hass, entry)


@pytest.mark.asyncio
async def test_async_setup_entry_generic_exception():
    """Test that setup handles generic exceptions by raising ConfigEntryNotReady."""
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"}

    with patch("custom_components.hyxi_cloud.HyxiDataUpdateCoordinator") as mock_coordinator_class, \
         patch("custom_components.hyxi_cloud.async_get_clientsession"):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=Exception("Some connection error"))

        with pytest.raises(FakeNotReady) as exc_info:
            await async_setup_entry(hass, entry)

        assert "Some connection error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_async_setup_entry_success():
    """Test successful setup of the config entry."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    entry = MagicMock()
    entry.data = {CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"}
    entry.entry_id = "test_entry_id"

    with patch("custom_components.hyxi_cloud.HyxiDataUpdateCoordinator") as mock_coordinator_class, \
         patch("custom_components.hyxi_cloud.async_get_clientsession"):

        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()

        result = await async_setup_entry(hass, entry)

        assert result is True
        assert hass.data[DOMAIN][entry.entry_id] == mock_coordinator

        from custom_components.hyxi_cloud.const import PLATFORMS
        hass.config_entries.async_forward_entry_setups.assert_called_once_with(entry, PLATFORMS)
        entry.add_update_listener.assert_called_once()
        entry.async_on_unload.assert_called_once()


@pytest.mark.asyncio
async def test_async_unload_entry():
    """Test unloading a config entry."""
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    entry = MagicMock()
    entry.entry_id = "test_entry_id"

    hass.data = {DOMAIN: {"test_entry_id": MagicMock()}}

    from custom_components.hyxi_cloud.const import PLATFORMS
    result = await async_unload_entry(hass, entry)

    assert result is True
    hass.config_entries.async_unload_platforms.assert_called_once_with(entry, PLATFORMS)
    assert "test_entry_id" not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_unload_entry_failure():
    """Test unloading a config entry when platforms fail to unload."""
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    entry = MagicMock()
    entry.entry_id = "test_entry_id"

    hass.data = {DOMAIN: {"test_entry_id": MagicMock()}}

    result = await async_unload_entry(hass, entry)

    assert result is False
    assert "test_entry_id" in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_reload_entry():
    """Test reloading a config entry."""
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()

    entry = MagicMock()
    entry.entry_id = "test_entry_id"

    await async_reload_entry(hass, entry)

    hass.config_entries.async_reload.assert_called_once_with("test_entry_id")

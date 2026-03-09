import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import sys

# Because the test runner uses "homeassistant" dependencies and we need "hyxi_cloud_api" mocked BEFORE custom component loads:
if "hyxi_cloud_api" not in sys.modules:
    sys.modules["hyxi_cloud_api"] = MagicMock()

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from custom_components.hyxi_cloud.__init__ import async_setup_entry
from custom_components.hyxi_cloud.const import CONF_ACCESS_KEY, CONF_SECRET_KEY, DOMAIN

@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = AsyncMock()
    return hass

@pytest.fixture
def mock_entry():
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
async def test_async_setup_entry_auth_failed(mock_hass, mock_entry):
    """Test setup failing due to authentication error."""
    with patch("custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator") as mock_coordinator_class, \
         patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"), \
         patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=ConfigEntryAuthFailed)

        with patch("custom_components.hyxi_cloud.__init__._LOGGER.error") as mock_logger:
            with pytest.raises(ConfigEntryAuthFailed):
                await async_setup_entry(mock_hass, mock_entry)

            mock_logger.assert_called_with("Authentication failed during setup")

@pytest.mark.asyncio
async def test_async_setup_entry_not_ready(mock_hass, mock_entry):
    """Test setup failing due to general exception."""
    with patch("custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator") as mock_coordinator_class, \
         patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"), \
         patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=Exception("Timeout"))

        with patch("custom_components.hyxi_cloud.__init__._LOGGER.warning") as mock_logger:
            with pytest.raises(ConfigEntryNotReady) as exc:
                await async_setup_entry(mock_hass, mock_entry)

            assert "Connection error: Timeout" in str(exc.value)
            mock_logger.assert_called_with("HYXi Cloud not ready: %s", mock_coordinator.async_config_entry_first_refresh.side_effect)

@pytest.mark.asyncio
async def test_async_setup_entry_missing_keys(mock_hass):
    """Test setup failing due to missing keys."""
    entry = MagicMock()
    entry.data = {}

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.error") as mock_logger:
        result = await async_setup_entry(mock_hass, entry)
        assert result is False
        mock_logger.assert_called_with("HYXi Integration could not find Access/Secret keys.")

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We MUST define the initial mocks for sys.modules if they aren't there because the test
# might be run individually, meaning other tests haven't put them there yet.
if "homeassistant.exceptions" not in sys.modules or not hasattr(
    sys.modules["homeassistant.exceptions"], "ConfigEntryAuthFailed"
):
    mock_ha = MagicMock()
    if "homeassistant" not in sys.modules:
        sys.modules["homeassistant"] = mock_ha
        sys.modules["homeassistant.components"] = mock_ha
        sys.modules["homeassistant.core"] = mock_ha
        sys.modules["homeassistant.exceptions"] = mock_ha

    class ConfigEntryAuthFailed(Exception):
        pass

    class ConfigEntryNotReady(Exception):
        pass

    sys.modules[
        "homeassistant.exceptions"
    ].ConfigEntryAuthFailed = ConfigEntryAuthFailed
    sys.modules["homeassistant.exceptions"].ConfigEntryNotReady = ConfigEntryNotReady

if "homeassistant.helpers.aiohttp_client" not in sys.modules:
    sys.modules["homeassistant.helpers.aiohttp_client"] = MagicMock()

if "hyxi_cloud_api" not in sys.modules:
    sys.modules["hyxi_cloud_api"] = MagicMock()

# Now we can safely import our component code
# Double check that we get exception classes (if the suite runs another test first, they might be MagicMocks)
# noqa flags tell Ruff to ignore the "import not at top of file" rule here
import custom_components.hyxi_cloud.__init__ as hc_init  # noqa: E402
from custom_components.hyxi_cloud.const import DOMAIN  # noqa: E402
from custom_components.hyxi_cloud.const import PLATFORMS  # noqa: E402

ConfigEntryAuthFailed = hc_init.ConfigEntryAuthFailed
ConfigEntryNotReady = hc_init.ConfigEntryNotReady
async_setup_entry = hc_init.async_setup_entry
async_unload_entry = hc_init.async_unload_entry

if not isinstance(hc_init.ConfigEntryAuthFailed, type) or not issubclass(
    hc_init.ConfigEntryAuthFailed, Exception
):

    class DummyConfigEntryAuthFailed(Exception):
        pass

    hc_init.ConfigEntryAuthFailed = DummyConfigEntryAuthFailed
    ConfigEntryAuthFailed = DummyConfigEntryAuthFailed

if not isinstance(hc_init.ConfigEntryNotReady, type) or not issubclass(
    hc_init.ConfigEntryNotReady, Exception
):

    class DummyConfigEntryNotReady(Exception):
        pass

    hc_init.ConfigEntryNotReady = DummyConfigEntryNotReady
    ConfigEntryNotReady = DummyConfigEntryNotReady


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = AsyncMock()
    return hass


@pytest.fixture
def mock_entry():
    from custom_components.hyxi_cloud.const import CONF_ACCESS_KEY
    from custom_components.hyxi_cloud.const import CONF_SECRET_KEY

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
            side_effect=Exception("Timeout")
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

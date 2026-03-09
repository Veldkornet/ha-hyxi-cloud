"""Tests for the ConfigFlow _validate_input logic."""
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

@pytest.fixture(autouse=True, scope="module")
def mock_ha_environment():
    """Mock the Home Assistant environment to prevent import errors and test bleed."""
    # Save original modules
    original_modules = sys.modules.copy()

    mock_ha = MagicMock()
    mock_ha.__path__ = []

    sys.modules["homeassistant"] = mock_ha
    sys.modules["homeassistant.components"] = mock_ha
    sys.modules["homeassistant.core"] = mock_ha
    sys.modules["homeassistant.exceptions"] = mock_ha
    sys.modules["homeassistant.util"] = mock_ha
    sys.modules["homeassistant.const"] = mock_ha

    mock_ce = types.ModuleType("mock_ce")
    class RealConfigFlow:
        def __init_subclass__(cls, **kwargs): pass
        def __init__(self): pass
    class RealOptionsFlow:
        def __init_subclass__(cls, **kwargs): pass
    mock_ce.ConfigFlow = RealConfigFlow
    mock_ce.OptionsFlow = RealOptionsFlow
    mock_ce.ConfigEntry = MagicMock()
    sys.modules["homeassistant.config_entries"] = mock_ce
    mock_ha.config_entries = mock_ce

    sys.modules["homeassistant.helpers"] = mock_ha
    sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
    sys.modules["homeassistant.helpers.update_coordinator"] = mock_ha
    sys.modules["hyxi_cloud_api"] = mock_ha
    sys.modules["voluptuous"] = mock_ha

    # Force a clean import of the module under test
    import importlib
    for m in list(sys.modules.keys()):
        if 'hyxi' in m and m != 'hyxi_cloud_api':
            del sys.modules[m]

    import custom_components.hyxi_cloud.config_flow as config_flow_mod
    importlib.reload(config_flow_mod)

    yield config_flow_mod

    # Restore original modules to prevent test bleed
    sys.modules.clear()
    sys.modules.update(original_modules)

@pytest.fixture
def mock_hyxi_client():
    client_mock = AsyncMock()
    client_mock._refresh_token = AsyncMock()
    return client_mock

@pytest.fixture
def config_flow(mock_ha_environment):
    # Construct normal class instance since ConfigFlow base class is no longer a MagicMock
    flow = mock_ha_environment.HyxiConfigFlow()
    flow.hass = MagicMock()
    return flow

@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient")
@patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession")
async def test_validate_input_success(mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client):
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.return_value = True

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result is None

@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient")
@patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession")
async def test_validate_input_invalid_auth(mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client):
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.return_value = False

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result == "invalid_auth"

@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient")
@patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession")
async def test_validate_input_cannot_connect(mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client):
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.side_effect = Exception("Connection Failed")

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result == "cannot_connect"

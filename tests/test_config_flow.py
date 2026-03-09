import sys
import importlib
import types
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

@pytest.fixture(autouse=True)
def mock_ha_environment():
    """Fixture to cleanly sandbox sys.modules and provide a real ConfigFlow base class."""
    original_modules = sys.modules.copy()

    mock_ha = MagicMock()
    mock_ha.__path__ = []

    for mod_name in [
        "homeassistant",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
        "homeassistant.exceptions",
        "homeassistant.const",
        "homeassistant.helpers.update_coordinator",
        "homeassistant.util",
        "homeassistant.components",
        "hyxi_cloud_api"
    ]:
        sys.modules[mod_name] = mock_ha

    class RealConfigFlow:
        def __init_subclass__(cls, **kwargs): pass
        def __init__(self, *args, **kwargs): pass

    mock_config = types.ModuleType("homeassistant.config_entries")
    mock_config.ConfigFlow = RealConfigFlow
    mock_config.OptionsFlow = RealConfigFlow
    mock_config.ConfigEntry = MagicMock()
    sys.modules["homeassistant.config_entries"] = mock_config

    if "custom_components.hyxi_cloud.const" in sys.modules:
        sys.modules.pop("custom_components.hyxi_cloud.const")
    if "custom_components.hyxi_cloud.config_flow" in sys.modules:
        sys.modules.pop("custom_components.hyxi_cloud.config_flow")

    # If homeassistant is imported with 'from homeassistant import config_entries',
    # it gets `config_entries` from `sys.modules["homeassistant"]`, which is a MagicMock!
    # So we must put our mock_config onto the mock_ha module explicitly.
    sys.modules["homeassistant"].config_entries = mock_config

    import custom_components.hyxi_cloud.config_flow as cf

    yield cf

    sys.modules.clear()
    sys.modules.update(original_modules)

@pytest.mark.asyncio
async def test_validate_input_cannot_connect(mock_ha_environment):
    cf = mock_ha_environment
    import custom_components.hyxi_cloud.const as const

    flow = cf.HyxiConfigFlow()
    flow.hass = MagicMock()

    with patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession"):
        class DummyClient:
            def __init__(self, *args, **kwargs):
                pass
            async def _refresh_token(self):
                raise Exception("Connection timeout")

        with patch.object(cf, "HyxiApiClient", DummyClient):
            data = {
                const.CONF_ACCESS_KEY: "test_ak",
                const.CONF_SECRET_KEY: "test_sk"
            }

            result = await flow._validate_input(data)

            assert result == "cannot_connect"

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

    # Need to override callback so it doesn't destroy the method it wraps
    def fake_callback(func):
        return func

    mock_ha.callback = fake_callback

    mock_ce = types.ModuleType("mock_ce")

    class RealConfigFlow:
        def __init_subclass__(cls, **kwargs):
            pass

        def __init__(self):
            pass

    class RealOptionsFlow:
        def __init_subclass__(cls, **kwargs):
            pass

    mock_ce.ConfigFlow = RealConfigFlow  # type: ignore[attr-defined]
    mock_ce.OptionsFlow = RealOptionsFlow  # type: ignore[attr-defined]
    mock_ce.ConfigEntry = MagicMock()  # type: ignore[attr-defined]
    mock_ce.exceptions = MagicMock()  # type: ignore[attr-defined]

    class IntentionalTermination(Exception):
        pass

    mock_ce.exceptions.IntentionalTermination = IntentionalTermination  # type: ignore[attr-defined]
    sys.modules["homeassistant.config_entries"] = mock_ce
    mock_ha.config_entries = mock_ce  # type: ignore[attr-defined]

    sys.modules["homeassistant.helpers"] = mock_ha
    sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
    sys.modules["homeassistant.helpers.update_coordinator"] = mock_ha
    sys.modules["homeassistant.helpers.restore_state"] = mock_ha
    sys.modules["homeassistant.helpers.device_registry"] = mock_ha
    sys.modules["homeassistant.helpers.entity"] = mock_ha
    mock_api = MagicMock()
    mock_api.__name__ = "hyxi_cloud_api"
    mock_api.__version__ = "1.0.4"
    sys.modules["hyxi_cloud_api"] = mock_api
    sys.modules["voluptuous"] = mock_ha

    mock_aiohttp = MagicMock()

    class ClientError(Exception):
        pass

    mock_aiohttp.ClientError = ClientError
    sys.modules["aiohttp"] = mock_aiohttp

    # Force a clean import of the module under test
    import importlib

    for m in list(sys.modules.keys()):
        if "hyxi" in m and m != "hyxi_cloud_api":
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
async def test_validate_input_success(
    mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client
):
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.return_value = True

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result is None


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient")
@patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession")
async def test_validate_input_invalid_auth(
    mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client
):
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.return_value = False

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result == "invalid_auth"


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient")
@patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession")
async def test_validate_input_cannot_connect(
    mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client
):
    from aiohttp import ClientError

    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.side_effect = ClientError("Connection Failed")

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result == "cannot_connect"


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient")
@patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession")
async def test_validate_input_timeout(
    mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client
):
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.side_effect = TimeoutError()

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result == "cannot_connect"


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient")
@patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession")
async def test_validate_input_unknown_error(
    mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client
):
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.side_effect = Exception("Unknown Error")

    with pytest.raises(Exception, match="Unknown Error"):
        await config_flow._validate_input({"access_key": "x", "secret_key": "y"})


@pytest.mark.asyncio
async def test_step_user_show_form(config_flow):
    config_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "user", "errors": {}}
    )
    result = await config_flow.async_step_user(user_input=None)

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {}


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_user_success(mock_validate_input, config_flow):
    mock_validate_input.return_value = None
    config_flow.async_set_unique_id = AsyncMock()
    config_flow._abort_if_unique_id_configured = MagicMock()
    config_flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    user_input = {"access_key": "x", "secret_key": "y"}
    result = await config_flow.async_step_user(user_input=user_input)

    assert result["type"] == "create_entry"
    config_flow.async_set_unique_id.assert_called_once_with("x")
    config_flow._abort_if_unique_id_configured.assert_called_once()


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_user_validation_error(mock_validate_input, config_flow):
    mock_validate_input.return_value = "invalid_auth"
    config_flow.async_set_unique_id = AsyncMock()
    config_flow._abort_if_unique_id_configured = MagicMock()
    config_flow.async_show_form = MagicMock(
        return_value={
            "type": "form",
            "step_id": "user",
            "errors": {"base": "invalid_auth"},
        }
    )

    user_input = {"access_key": "x", "secret_key": "y"}
    result = await config_flow.async_step_user(user_input=user_input)

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_step_reauth(config_flow):
    config_flow.context = {"entry_id": "test_entry_id"}
    config_flow.hass.config_entries.async_get_entry = MagicMock(
        return_value="mock_entry"
    )
    config_flow.async_step_reauth_confirm = AsyncMock(
        return_value={"type": "form", "step_id": "reauth_confirm"}
    )

    result = await config_flow.async_step_reauth(entry_data={})

    assert config_flow.reauth_entry == "mock_entry"
    config_flow.hass.config_entries.async_get_entry.assert_called_once_with(
        "test_entry_id"
    )
    assert result["step_id"] == "reauth_confirm"


@pytest.mark.asyncio
async def test_step_reauth_confirm_show_form(config_flow):
    config_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "reauth_confirm", "errors": {}}
    )

    result = await config_flow.async_step_reauth_confirm(user_input=None)

    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {}


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_reauth_confirm_success(mock_validate_input, config_flow):
    mock_validate_input.return_value = None
    config_flow.reauth_entry = "mock_entry"
    config_flow.async_update_reload_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reauth_successful"}
    )

    user_input = {"access_key": "x", "secret_key": "y"}
    result = await config_flow.async_step_reauth_confirm(user_input=user_input)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    config_flow.async_update_reload_and_abort.assert_called_once_with(
        "mock_entry", data=user_input
    )


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_reauth_confirm_validation_error(mock_validate_input, config_flow):
    mock_validate_input.return_value = "invalid_auth"
    config_flow.async_show_form = MagicMock(
        return_value={
            "type": "form",
            "step_id": "reauth_confirm",
            "errors": {"base": "invalid_auth"},
        }
    )

    user_input = {"access_key": "x", "secret_key": "y"}
    result = await config_flow.async_step_reauth_confirm(user_input=user_input)

    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}


def test_get_options_flow(mock_ha_environment):
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()

    # In Home Assistant, @callback does not prevent calling the method.
    # We want to call HyxiConfigFlow.async_get_options_flow directly to hit coverage
    # Since we mocked the environment, if it's a mock we can inspect its __wrapped__
    # or we can unmock the decorator
    # With the new mock we can simply call it and verify the return value
    options_flow = config_flow_mod.HyxiConfigFlow.async_get_options_flow(config_entry)

    assert isinstance(options_flow, config_flow_mod.HyxiOptionsFlowHandler)
    assert options_flow._config_entry == config_entry


@pytest.mark.asyncio
async def test_options_flow_show_form_default_fallback(mock_ha_environment):
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    config_entry.options = {}  # Empty options to trigger default fallback

    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "init"}
    )

    result = await options_flow.async_step_init(user_input=None)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    options_flow.async_show_form.assert_called_once()

    # Verify the schema is passed to async_show_form
    call_kwargs = options_flow.async_show_form.call_args.kwargs
    assert "data_schema" in call_kwargs

    # To avoid relying on inner mock calls of voluptuous which could break tests
    # depending on how exactly it's mocked or used, we just verify `async_show_form`
    # was called with a form and the right step_id.


@pytest.mark.asyncio
async def test_options_flow_show_form(mock_ha_environment):
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    config_entry.options = {"update_interval": 10}

    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "init"}
    )

    result = await options_flow.async_step_init(user_input=None)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    options_flow.async_show_form.assert_called_once()

    # Verify the schema defaults
    call_kwargs = options_flow.async_show_form.call_args.kwargs
    assert "data_schema" in call_kwargs


@pytest.mark.asyncio
async def test_options_flow_success(mock_ha_environment):
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    user_input = {"update_interval": 30}
    result = await options_flow.async_step_init(user_input=user_input)

    assert result["type"] == "create_entry"
    options_flow.async_create_entry.assert_called_once_with(title="", data=user_input)


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_user_already_configured(mock_validate_input, config_flow):
    """Test step_user when the entry is already configured (Unique ID abort)."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    mock_validate_input.return_value = None
    config_flow.async_set_unique_id = AsyncMock()
    # Trigger the abort exception
    config_flow._abort_if_unique_id_configured = MagicMock(
        side_effect=config_flow_mod.config_entries.exceptions.IntentionalTermination(
            "already_configured"
        )
    )

    user_input = {"access_key": "existing_key", "secret_key": "y"}

    with pytest.raises(
        config_flow_mod.config_entries.exceptions.IntentionalTermination
    ):
        await config_flow.async_step_user(user_input=user_input)

    config_flow.async_set_unique_id.assert_called_once_with("existing_key")
    config_flow._abort_if_unique_id_configured.assert_called_once()

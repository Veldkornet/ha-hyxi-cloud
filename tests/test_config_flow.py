"""Tests for the ConfigFlow _validate_input logic."""

import builtins
import contextlib
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
    sys.modules["homeassistant.components"] = MagicMock()
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
    mock_ce.SOURCE_RECONFIGURE = "reconfigure"  # type: ignore[attr-defined]

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
    mock_api.VPP_ACTIVE_MODES = frozenset({"13", "14", "16"})
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
    client_mock.get_all_device_data = AsyncMock(
        return_value={"data": {"SOME_SN": {}}, "attempts": 1}
    )
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
async def test_validate_input_network_error_is_not_invalid_auth(
    mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client
):
    """A network/connection failure during token refresh (client returns
    None) must be reported as cannot_connect, not invalid_auth -- otherwise
    a user with valid keys and a flaky connection is told their keys are
    wrong."""
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.return_value = None

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
    """An unexpected exception is caught, logged, and reported as the
    'unknown' error rather than propagating unhandled/unlogged."""
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.side_effect = Exception("Unknown Error")

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result == "unknown"


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient")
@patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession")
async def test_validate_input_no_devices(
    mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client
):
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.return_value = True
    mock_hyxi_client.get_all_device_data.return_value = {"data": {}, "attempts": 1}

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result == "no_devices"


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient")
@patch("custom_components.hyxi_cloud.config_flow.async_get_clientsession")
async def test_validate_input_get_all_device_data_none(
    mock_get_session, mock_api_client_class, config_flow, mock_hyxi_client
):
    mock_api_client_class.return_value = mock_hyxi_client
    mock_hyxi_client._refresh_token.return_value = True
    mock_hyxi_client.get_all_device_data.return_value = None

    result = await config_flow._validate_input({"access_key": "x", "secret_key": "y"})
    assert result == "cannot_connect"


@pytest.mark.asyncio
async def test_step_cloud_show_form(config_flow):
    config_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "cloud", "errors": {}}
    )
    result = await config_flow.async_step_cloud(user_input=None)

    assert result["type"] == "form"
    assert result["step_id"] == "cloud"
    assert result["errors"] == {}


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_cloud_success(mock_validate_input, config_flow):
    mock_validate_input.return_value = None
    config_flow.async_set_unique_id = AsyncMock()
    config_flow._abort_if_unique_id_configured = MagicMock()
    config_flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    user_input = {"access_key": "x", "secret_key": "y"}
    result = await config_flow.async_step_cloud(user_input=user_input)

    assert result["type"] == "create_entry"
    config_flow.async_set_unique_id.assert_called_once_with("x")
    config_flow._abort_if_unique_id_configured.assert_called_once()

    # No region selected -- defaults to the EU server, matching pre-existing behavior
    call_kwargs = config_flow.async_create_entry.call_args.kwargs
    assert call_kwargs["data"]["base_url"] == "https://open.hyxicloud.com"


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_cloud_selected_region_resolves_base_url(
    mock_validate_input, config_flow
):
    """The region picked in the form must resolve to that region's server,
    and that server (not the EU default) is what credentials get validated
    against -- otherwise a North America/China account would be told its
    keys are invalid instead of being pointed at the right server."""
    mock_validate_input.return_value = None
    config_flow.async_set_unique_id = AsyncMock()
    config_flow._abort_if_unique_id_configured = MagicMock()
    config_flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    user_input = {"access_key": "x", "secret_key": "y", "region": "na"}
    result = await config_flow.async_step_cloud(user_input=user_input)

    assert result["type"] == "create_entry"
    mock_validate_input.assert_called_once_with(
        user_input, "https://open-or.hyxicloud.com"
    )
    call_kwargs = config_flow.async_create_entry.call_args.kwargs
    assert call_kwargs["data"]["base_url"] == "https://open-or.hyxicloud.com"


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow._build_user_schema")
async def test_step_cloud_show_form_suggests_region_from_country(
    mock_build_schema, config_flow
):
    """The form's default region should follow Home Assistant's configured
    country so most users never have to touch the dropdown, while still
    letting them override it. voluptuous itself is mocked out in this test
    module, so we verify the resolved default is passed to the schema
    builder rather than inspecting a (mocked) vol.Schema object."""
    config_flow.hass.config.country = "US"
    config_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "cloud", "errors": {}}
    )

    await config_flow.async_step_cloud(user_input=None)

    mock_build_schema.assert_called_once_with("na")


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_cloud_validation_error(mock_validate_input, config_flow):
    mock_validate_input.return_value = "invalid_auth"
    config_flow.async_set_unique_id = AsyncMock()
    config_flow._abort_if_unique_id_configured = MagicMock()
    config_flow.async_show_form = MagicMock(
        return_value={
            "type": "form",
            "step_id": "cloud",
            "errors": {"base": "invalid_auth"},
        }
    )

    user_input = {"access_key": "x", "secret_key": "y"}
    result = await config_flow.async_step_cloud(user_input=user_input)

    assert result["type"] == "form"
    assert result["step_id"] == "cloud"
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
    """Resubmitting the same (pre-filled) region on reauth keeps the same base_url."""
    mock_validate_input.return_value = None
    mock_entry = MagicMock()
    mock_entry.data = {
        "access_key": "old",
        "secret_key": "old",
        "base_url": "https://open-or.hyxicloud.com",
        "region": "na",
    }
    config_flow.reauth_entry = mock_entry
    config_flow.async_update_reload_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reauth_successful"}
    )

    user_input = {"access_key": "x", "secret_key": "y", "region": "na"}
    result = await config_flow.async_step_reauth_confirm(user_input=user_input)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    config_flow.async_update_reload_and_abort.assert_called_once_with(
        mock_entry,
        data={
            "access_key": "x",
            "secret_key": "y",
            "region": "na",
            "base_url": "https://open-or.hyxicloud.com",
        },
    )
    mock_validate_input.assert_called_once_with(
        user_input, "https://open-or.hyxicloud.com"
    )


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_reauth_confirm_region_change(mock_validate_input, config_flow):
    """Reauth also lets the user correct the region -- e.g. the account was
    migrated to a different HYXI server, or it was picked wrong at install."""
    mock_validate_input.return_value = None
    mock_entry = MagicMock()
    mock_entry.data = {
        "access_key": "old",
        "secret_key": "old",
        "base_url": "https://open.hyxicloud.com",
        "region": "eu",
    }
    config_flow.reauth_entry = mock_entry
    config_flow.async_update_reload_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reauth_successful"}
    )

    user_input = {"access_key": "x", "secret_key": "y", "region": "cn"}
    result = await config_flow.async_step_reauth_confirm(user_input=user_input)

    assert result["type"] == "abort"
    mock_validate_input.assert_called_once_with(
        user_input, "https://open-cn.hyxicloud.com"
    )
    config_flow.async_update_reload_and_abort.assert_called_once_with(
        mock_entry,
        data={
            "access_key": "x",
            "secret_key": "y",
            "region": "cn",
            "base_url": "https://open-cn.hyxicloud.com",
        },
    )


@pytest.mark.asyncio
async def test_step_reauth_confirm_show_form_preselects_current_region(config_flow):
    """The reauth form must default the region dropdown to the entry's
    current region, not always EU -- otherwise resubmitting unchanged would
    silently move an NA/CN account back to the EU server."""
    mock_entry = MagicMock()
    mock_entry.data = {"access_key": "old", "secret_key": "old", "region": "na"}
    config_flow.reauth_entry = mock_entry

    with patch(
        "custom_components.hyxi_cloud.config_flow._build_user_schema"
    ) as mock_build_schema:
        config_flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "reauth_confirm", "errors": {}}
        )
        await config_flow.async_step_reauth_confirm(user_input=None)

    mock_build_schema.assert_called_once_with("na")


@pytest.mark.asyncio
async def test_step_reauth_confirm_show_form_falls_back_from_base_url(config_flow):
    """Entries created before region selection existed only have a stored
    base_url, no "region" key -- the form must still preselect the right
    region by reverse-mapping the base_url."""
    mock_entry = MagicMock()
    mock_entry.data = {
        "access_key": "old",
        "secret_key": "old",
        "base_url": "https://open-cn.hyxicloud.com",
    }
    config_flow.reauth_entry = mock_entry

    with patch(
        "custom_components.hyxi_cloud.config_flow._build_user_schema"
    ) as mock_build_schema:
        config_flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "reauth_confirm", "errors": {}}
        )
        await config_flow.async_step_reauth_confirm(user_input=None)

    mock_build_schema.assert_called_once_with("cn")


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_reauth_confirm_validation_error(mock_validate_input, config_flow):
    mock_validate_input.return_value = "invalid_auth"
    mock_entry = MagicMock()
    mock_entry.data = {"access_key": "old", "secret_key": "old"}
    config_flow.reauth_entry = mock_entry
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


@pytest.mark.asyncio
async def test_step_reauth_confirm_raises_when_reauth_entry_unset(config_flow):
    """Defensive guard: submitting input to reauth_confirm without a
    reauth_entry already set (i.e. async_step_reauth wasn't run first) is a
    programming error, not a recoverable user-facing state."""
    assert config_flow.reauth_entry is None
    with pytest.raises(ValueError, match="reauth_entry is not set"):
        await config_flow.async_step_reauth_confirm(user_input={"access_key": "x"})


@pytest.mark.asyncio
async def test_options_flow_updates_push_settings(mock_ha_environment):
    """Test push rate/URL are saved (rate coerced to int) when push stays on."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    config_entry.options = {config_flow_mod.CONF_ENABLE_PUSH: True}  # already on
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    user_input = {
        "update_interval": 10,
        config_flow_mod.CONF_ENABLE_PUSH: True,
        config_flow_mod.CONF_PUSH_RATE: "30",
        config_flow_mod.CONF_PUSH_URL: "https://example.com/webhook",
    }
    result = await options_flow.async_step_init(user_input=user_input)

    assert result["type"] == "create_entry"
    saved = options_flow.async_create_entry.call_args.kwargs["data"]
    assert saved[config_flow_mod.CONF_ENABLE_PUSH] is True
    assert saved[config_flow_mod.CONF_PUSH_RATE] == 30  # coerced from str to int
    assert saved[config_flow_mod.CONF_PUSH_URL] == "https://example.com/webhook"


@pytest.mark.asyncio
async def test_options_flow_enabling_em_auto_enables_battery_control(
    mock_ha_environment,
):
    """Test toggling 'enable_energy_manager' on auto-enables battery control
    (EM requires it) and routes straight to the energy_manager step."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    config_entry.options = {}
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.async_step_energy_manager = AsyncMock(
        return_value={"type": "form", "step_id": "energy_manager"}
    )

    user_input = {"update_interval": 5, "enable_energy_manager": True}
    result = await options_flow.async_step_init(user_input=user_input)

    assert result["step_id"] == "energy_manager"
    assert options_flow._options["enable_battery_control"] is True
    assert options_flow._options[config_flow_mod.CONF_EM_ENABLED] is True
    options_flow.async_step_energy_manager.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_reloads_step_when_battery_control_just_enabled(
    mock_ha_environment,
):
    """Test turning on battery control (without also deciding on EM in the
    same submit) reloads the init step so the EM toggle can appear."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    config_entry.options = {}
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = MagicMock()
    options_flow.hass.data = {}
    options_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "init"}
    )

    with patch.object(options_flow, "_has_controllable_inverter", return_value=True):
        user_input = {"update_interval": 5, "enable_battery_control": True}
        result = await options_flow.async_step_init(user_input=user_input)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    options_flow.async_show_form.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_reloads_step_when_push_just_enabled(mock_ha_environment):
    """Test turning on push reloads the init step so the rate/URL fields appear."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    config_entry.options = {}
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = MagicMock()
    options_flow.hass.data = {}
    options_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "init"}
    )

    user_input = {"update_interval": 5, config_flow_mod.CONF_ENABLE_PUSH: True}
    result = await options_flow.async_step_init(user_input=user_input)

    assert result["type"] == "form"
    options_flow.async_show_form.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_show_form_includes_push_fields_when_enabled(
    mock_ha_environment,
):
    """Test the rate/URL fields are added to the schema once push is on."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    config_entry.options = {
        config_flow_mod.CONF_ENABLE_PUSH: True,
        config_flow_mod.CONF_PUSH_RATE: 30,
    }
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = MagicMock()
    options_flow.hass.data = {}
    options_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "init"}
    )

    await options_flow.async_step_init(user_input=None)

    call_kwargs = options_flow.async_show_form.call_args.kwargs
    assert "data_schema" in call_kwargs


@pytest.mark.asyncio
async def test_options_flow_show_form_includes_em_toggle_when_eligible(
    mock_ha_environment,
):
    """Test the EM toggle field is added when battery control is already on
    and an EM-eligible (hybrid_inverter/all_in_one) device is present."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod
    from custom_components.hyxi_cloud.const import DOMAIN

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_id"
    config_entry.options = {"enable_battery_control": True}
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = {"SN_INV_1": {"device_type_code": "HYBRID_INVERTER"}}
    options_flow.hass.data = {DOMAIN: {"test_entry_id": coordinator}}
    options_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "init"}
    )

    await options_flow.async_step_init(user_input=None)

    options_flow.async_show_form.assert_called_once()
    call_kwargs = options_flow.async_show_form.call_args.kwargs
    assert "data_schema" in call_kwargs


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
    options_flow.hass = MagicMock()
    options_flow.hass.data = {}  # No coordinator data — no controllable inverters
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
    options_flow.hass = MagicMock()
    options_flow.hass.data = {}  # No coordinator data — no controllable inverters
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

    user_input = {"update_interval": 30, "enable_battery_control": True}
    result = await options_flow.async_step_init(user_input=user_input)

    assert result["type"] == "create_entry"
    call_kwargs = options_flow.async_create_entry.call_args.kwargs
    assert call_kwargs["data"]["update_interval"] == 30


def test_control_capable_excludes_micro_ess_by_default(mock_ha_environment):
    """Micro ESS is excluded from control-capable SNs by default
    (MICRO_ESS_CONTROL_SUPPORTED=False) pending HYXI control API permission."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod
    from custom_components.hyxi_cloud.const import DOMAIN

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_id"

    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = {"SN_HALO_1": {"device_type_code": "EMS"}}
    options_flow.hass.data = {DOMAIN: {"test_entry_id": coordinator}}

    assert not options_flow._get_control_capable_sns()
    assert options_flow._has_control_capable_device() is False
    # Not EM-eligible either — micro_ess devices can't run the Energy Manager
    assert not options_flow._get_controllable_sns()
    assert options_flow._has_controllable_inverter() is False


def test_control_capable_includes_micro_ess_when_supported(mock_ha_environment):
    """Once HYXI grants control API access and MICRO_ESS_CONTROL_SUPPORTED is
    flipped to True, a HALO-only install becomes control-capable (but still
    not EM-eligible)."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod
    from custom_components.hyxi_cloud.const import DOMAIN

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_id"

    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = {"SN_HALO_1": {"device_type_code": "EMS"}}
    options_flow.hass.data = {DOMAIN: {"test_entry_id": coordinator}}

    with patch(
        "custom_components.hyxi_cloud.config_flow.MICRO_ESS_CONTROL_SUPPORTED", True
    ):
        assert options_flow._get_control_capable_sns() == ["SN_HALO_1"]
        assert options_flow._has_control_capable_device() is True
    assert not options_flow._get_controllable_sns()
    assert options_flow._has_controllable_inverter() is False


def test_control_capable_includes_hybrid_inverter(mock_ha_environment):
    """A hybrid inverter is both control-capable and EM-eligible."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod
    from custom_components.hyxi_cloud.const import DOMAIN

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_id"

    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = {"SN_INV_1": {"device_type_code": "HYBRID_INVERTER"}}
    options_flow.hass.data = {DOMAIN: {"test_entry_id": coordinator}}

    assert options_flow._get_control_capable_sns() == ["SN_INV_1"]
    assert options_flow._has_control_capable_device() is True
    assert options_flow._get_controllable_sns() == ["SN_INV_1"]
    assert options_flow._has_controllable_inverter() is True


def test_get_sns_by_device_type_hass_not_set(mock_ha_environment):
    """Guard returns [] when self.hass was never assigned (fresh flow instance)."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_id"
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)

    assert not hasattr(options_flow, "hass")
    assert options_flow._get_control_capable_sns() == []
    assert options_flow._has_control_capable_device() is False
    assert options_flow._get_controllable_sns() == []
    assert options_flow._has_controllable_inverter() is False


def test_get_sns_by_device_type_hass_none(mock_ha_environment):
    """Guard returns [] when self.hass is explicitly None."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_id"
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = None

    assert options_flow._get_control_capable_sns() == []
    assert options_flow._has_control_capable_device() is False


def test_get_sns_by_device_type_missing_coordinator(mock_ha_environment):
    """Guard returns [] when hass.data has no entry for DOMAIN or entry_id."""
    import custom_components.hyxi_cloud.config_flow as config_flow_mod
    from custom_components.hyxi_cloud.const import DOMAIN

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_id"
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)

    # No DOMAIN key at all
    options_flow.hass = MagicMock()
    options_flow.hass.data = {}
    assert options_flow._get_control_capable_sns() == []
    assert options_flow._has_control_capable_device() is False

    # DOMAIN present but no coordinator for this entry_id
    options_flow.hass.data = {DOMAIN: {}}
    assert options_flow._get_control_capable_sns() == []
    assert options_flow._has_control_capable_device() is False


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._validate_input")
async def test_step_cloud_already_configured(mock_validate_input, config_flow):
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
        await config_flow.async_step_cloud(user_input=user_input)

    config_flow.async_set_unique_id.assert_called_once_with("existing_key")
    config_flow._abort_if_unique_id_configured.assert_called_once()


def test_build_em_schema_defaults(mock_ha_environment):
    """Test _build_em_schema wires option defaults and the current_sn param into
    the right fields. voluptuous itself is mocked, so we inspect the recorded
    vol.Required/vol.Optional call args rather than the returned Schema object
    (see other schema tests in this file for the same rationale)."""
    config_flow_mod = mock_ha_environment

    options = {
        config_flow_mod.CONF_EM_P1_ENTITY: "sensor.p1",
        config_flow_mod.CONF_EM_BATTERY_CAPACITY: 8000,
        config_flow_mod.CONF_EM_LOOP_INTERVAL: 30,
    }
    schema = config_flow_mod._build_em_schema(options, ["SN1", "SN2"], "SN1")

    assert schema is not None
    config_flow_mod.vol.Schema.assert_called()

    required_calls = {
        c.args[0]: c.kwargs.get("default")
        for c in config_flow_mod.vol.Required.call_args_list
    }
    optional_calls = {
        c.args[0]: c.kwargs.get("default")
        for c in config_flow_mod.vol.Optional.call_args_list
    }

    assert required_calls[config_flow_mod.CONF_EM_P1_ENTITY] == "sensor.p1"
    assert required_calls[config_flow_mod.CONF_EM_INVERTER_SN] == "SN1"
    assert optional_calls[config_flow_mod.CONF_EM_BATTERY_CAPACITY] == 8000
    assert optional_calls[config_flow_mod.CONF_EM_LOOP_INTERVAL] == 30
    assert optional_calls[config_flow_mod.CONF_EM_DRY_RUN] is False


def test_build_em_schema_fallback_defaults(mock_ha_environment):
    """Test _build_em_schema falls back to hardcoded defaults when options are unset."""
    config_flow_mod = mock_ha_environment

    schema = config_flow_mod._build_em_schema({}, [], "")

    assert schema is not None
    optional_calls = {
        c.args[0]: c.kwargs.get("default")
        for c in config_flow_mod.vol.Optional.call_args_list
    }
    assert optional_calls[config_flow_mod.CONF_EM_BATTERY_CAPACITY] == 2000
    assert optional_calls[config_flow_mod.CONF_EM_LOOP_INTERVAL] == 15
    assert optional_calls[config_flow_mod.CONF_EM_BATTERY_OVERRIDE] is False


def test_save_energy_manager_input_with_battery_override(mock_ha_environment):
    """Test _save_energy_manager_input stores every field when override is on."""
    config_flow_mod = mock_ha_environment

    config_entry = MagicMock()
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow._options = {}

    user_input = {
        config_flow_mod.CONF_EM_P1_ENTITY: "sensor.p1",
        config_flow_mod.CONF_EM_INVERTER_SN: "SN1",
        config_flow_mod.CONF_EM_BATTERY_OVERRIDE: True,
        config_flow_mod.CONF_EM_BATTERY_CAPACITY: 6000,
        config_flow_mod.CONF_EM_FORECAST_ENTITY: "sensor.forecast",
        config_flow_mod.CONF_EM_FORECAST_POWER_ENTITY: "sensor.forecast_power",
        config_flow_mod.CONF_EM_LOOP_INTERVAL: 20,
        config_flow_mod.CONF_EM_DRY_RUN: True,
    }

    options_flow._save_energy_manager_input(user_input)

    assert options_flow._options[config_flow_mod.CONF_EM_P1_ENTITY] == "sensor.p1"
    assert options_flow._options[config_flow_mod.CONF_EM_INVERTER_SN] == "SN1"
    assert options_flow._options[config_flow_mod.CONF_EM_BATTERY_OVERRIDE] is True
    assert options_flow._options[config_flow_mod.CONF_EM_BATTERY_CAPACITY] == 6000
    assert (
        options_flow._options[config_flow_mod.CONF_EM_FORECAST_ENTITY]
        == "sensor.forecast"
    )
    assert (
        options_flow._options[config_flow_mod.CONF_EM_FORECAST_POWER_ENTITY]
        == "sensor.forecast_power"
    )
    assert options_flow._options[config_flow_mod.CONF_EM_LOOP_INTERVAL] == 20
    assert options_flow._options[config_flow_mod.CONF_EM_DRY_RUN] is True


def test_save_energy_manager_input_without_battery_override_pops_capacity(
    mock_ha_environment,
):
    """Test turning battery override off removes any stored capacity value and
    that omitted optional fields (forecast entities) are left untouched rather
    than being popped."""
    config_flow_mod = mock_ha_environment

    config_entry = MagicMock()
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow._options = {config_flow_mod.CONF_EM_BATTERY_CAPACITY: 9999}

    user_input = {
        config_flow_mod.CONF_EM_P1_ENTITY: "sensor.p1",
        config_flow_mod.CONF_EM_INVERTER_SN: "SN1",
    }

    options_flow._save_energy_manager_input(user_input)

    assert config_flow_mod.CONF_EM_BATTERY_CAPACITY not in options_flow._options
    assert options_flow._options[config_flow_mod.CONF_EM_BATTERY_OVERRIDE] is False
    assert config_flow_mod.CONF_EM_FORECAST_ENTITY not in options_flow._options
    assert options_flow._options[config_flow_mod.CONF_EM_LOOP_INTERVAL] == 15
    assert options_flow._options[config_flow_mod.CONF_EM_DRY_RUN] is False


@pytest.mark.asyncio
async def test_async_step_energy_manager_with_input_creates_entry(mock_ha_environment):
    """Test providing user_input saves it and creates the config entry."""
    config_flow_mod = mock_ha_environment

    config_entry = MagicMock()
    config_entry.options = {}
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow._options = {}
    options_flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

    user_input = {
        config_flow_mod.CONF_EM_P1_ENTITY: "sensor.p1",
        config_flow_mod.CONF_EM_INVERTER_SN: "SN1",
    }

    result = await options_flow.async_step_energy_manager(user_input=user_input)

    assert result == {"type": "create_entry"}
    options_flow.async_create_entry.assert_called_once_with(
        title="", data=options_flow._options
    )
    assert options_flow._options[config_flow_mod.CONF_EM_P1_ENTITY] == "sensor.p1"


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow._build_em_schema")
async def test_async_step_energy_manager_autoselects_single_sn(
    mock_build_schema, mock_ha_environment
):
    """Test that with exactly one controllable inverter and no prior selection,
    it's preselected as the schema's current_sn default."""
    config_flow_mod = mock_ha_environment
    from custom_components.hyxi_cloud.const import DOMAIN

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_id"
    config_entry.options = {}
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = {"SN_ONLY": {"device_type_code": "HYBRID_INVERTER"}}
    options_flow.hass.data = {DOMAIN: {"test_entry_id": coordinator}}
    options_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "energy_manager"}
    )

    result = await options_flow.async_step_energy_manager(user_input=None)

    assert result["step_id"] == "energy_manager"
    mock_build_schema.assert_called_once_with(
        config_entry.options, ["SN_ONLY"], "SN_ONLY"
    )


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.config_flow._build_em_schema")
async def test_async_step_energy_manager_keeps_existing_sn_selection(
    mock_build_schema, mock_ha_environment
):
    """Test that an already-configured inverter SN is preserved as the default
    even when multiple controllable inverters are available."""
    config_flow_mod = mock_ha_environment
    from custom_components.hyxi_cloud.const import DOMAIN

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_id"
    config_entry.options = {config_flow_mod.CONF_EM_INVERTER_SN: "SN_B"}
    options_flow = config_flow_mod.HyxiOptionsFlowHandler(config_entry)
    options_flow.hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = {
        "SN_A": {"device_type_code": "HYBRID_INVERTER"},
        "SN_B": {"device_type_code": "HYBRID_INVERTER"},
    }
    options_flow.hass.data = {DOMAIN: {"test_entry_id": coordinator}}
    options_flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "energy_manager"}
    )

    await options_flow.async_step_energy_manager(user_input=None)

    args, _kwargs = mock_build_schema.call_args
    assert sorted(args[1]) == ["SN_A", "SN_B"]
    assert args[2] == "SN_B"


# --- Transport selection and the local Modbus branch -----------------------


@pytest.fixture
def modbus_flow(config_flow):
    """A config flow with the unique-id guards stubbed out."""
    # A real ConfigFlow always has these from __init__; the fake harness's
    # stub __init__ is a no-op, so they must be set explicitly for anything
    # that reads them (self.context.get("source"), and
    # hass.config_entries.async_entry_for_domain_unique_id(self.handler, ...)
    # in _validate_modbus's own-entry-aware uniqueness check).
    config_flow.context = {}
    config_flow.handler = "hyxi_cloud"
    config_flow.async_set_unique_id = AsyncMock()
    config_flow._abort_if_unique_id_configured = MagicMock()
    # No existing entry at this address by default -- config_flow.hass is a
    # bare MagicMock, and an un-configured call on one returns a truthy
    # MagicMock, which would make every test look like a collision.
    config_flow.hass.config_entries.async_entry_for_domain_unique_id = MagicMock(
        return_value=None
    )
    config_flow.async_show_form = MagicMock(
        side_effect=lambda **kw: {"type": "form", **kw}
    )
    config_flow.async_create_entry = MagicMock(
        side_effect=lambda **kw: {"type": "entry", **kw}
    )
    return config_flow


class _FakeModbusError(Exception):
    """Stands in for modbus_connection's exception-response error."""


class _FakeModbusTimeout(Exception):
    """Stands in for modbus_connection's timeout error."""


def _fake_modbus_modules(read_side_effect=None):
    """Build stand-ins for modbus_connection and its tmodbus backend.

    The real package is an optional runtime dependency that CI does not
    install (tests.yml syncs only the test extra), and config_flow imports
    it lazily, so the probe is exercised against these instead.
    """
    unit = MagicMock()
    unit.read_input_registers = AsyncMock(side_effect=read_side_effect)
    unit.read_holding_registers = AsyncMock(side_effect=read_side_effect)

    connection = MagicMock()
    connection.for_unit = MagicMock(return_value=unit)
    connection.close = AsyncMock()

    root = types.ModuleType("modbus_connection")
    root.ModbusExceptionError = _FakeModbusError  # type: ignore[attr-defined]
    root.ModbusTimeoutError = _FakeModbusTimeout  # type: ignore[attr-defined]
    root.ModbusSerialParams = lambda **kw: ("serial", kw)  # type: ignore[attr-defined]
    root.ModbusTcpParams = lambda **kw: ("tcp", kw)  # type: ignore[attr-defined]

    backend = types.ModuleType("modbus_connection.tmodbus")
    backend.ModbusConnection = MagicMock(  # type: ignore[attr-defined]
        return_value=connection
    )

    return types.SimpleNamespace(
        root=root, backend=backend, connection=connection, unit=unit
    )


@contextlib.contextmanager
def _install_modbus(root, backend):
    """Temporarily place the fake modbus modules on sys.modules."""
    saved = {
        k: sys.modules.get(k)
        for k in ("modbus_connection", "modbus_connection.tmodbus")
    }
    sys.modules["modbus_connection"] = root
    sys.modules["modbus_connection.tmodbus"] = backend
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


@pytest.mark.asyncio
async def test_step_user_shows_transport_chooser(modbus_flow):
    result = await modbus_flow.async_step_user(user_input=None)

    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_step_user_cloud_choice_goes_to_credentials(modbus_flow):
    modbus_flow.async_step_cloud = AsyncMock(return_value={"type": "cloud"})

    result = await modbus_flow.async_step_user(user_input={"transport": "cloud"})

    assert result == {"type": "cloud"}


@pytest.mark.asyncio
async def test_step_user_modbus_choice_goes_to_modbus(modbus_flow):
    modbus_flow.async_step_modbus = AsyncMock(return_value={"type": "modbus"})

    result = await modbus_flow.async_step_user(user_input={"transport": "modbus"})

    assert result == {"type": "modbus"}


@pytest.mark.asyncio
async def test_step_modbus_shows_connection_type_form(modbus_flow):
    result = await modbus_flow.async_step_modbus(user_input=None)

    assert result["step_id"] == "modbus"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("modbus_type", "expected_step"),
    [("tcp", "modbus_tcp"), ("serial", "modbus_serial")],
)
async def test_step_modbus_routes_by_connection_type(
    modbus_flow, modbus_type, expected_step
):
    result = await modbus_flow.async_step_modbus(
        user_input={"modbus_type": modbus_type}
    )

    assert result["step_id"] == expected_step
    assert modbus_flow._modbus_type == modbus_type


@pytest.mark.asyncio
async def test_modbus_tcp_creates_entry_when_device_answers(modbus_flow):
    fake = _fake_modbus_modules()
    modbus_flow._modbus_type = "tcp"

    with _install_modbus(fake.root, fake.backend):
        result = await modbus_flow.async_step_modbus_tcp(
            user_input={
                "modbus_host": "192.168.1.50",
                "modbus_port": 502,
                "modbus_unit": 1,
            }
        )

    assert result["type"] == "entry"
    assert result["title"] == "HYXI Modbus (192.168.1.50)"
    assert result["data"]["transport"] == "modbus"
    assert result["data"]["modbus_host"] == "192.168.1.50"
    assert result["data"]["modbus_unit"] == 1
    # The title is a form field, not entry data.
    assert "_title" not in result["data"]
    modbus_flow.async_set_unique_id.assert_awaited_once_with(
        "192.168.1.50:502:1", raise_on_progress=False
    )
    fake.connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_modbus_serial_creates_entry_and_coerces_types(modbus_flow):
    fake = _fake_modbus_modules()
    modbus_flow._modbus_type = "serial"

    with _install_modbus(fake.root, fake.backend):
        result = await modbus_flow.async_step_modbus_serial(
            user_input={
                "modbus_device": "/dev/ttyUSB0",
                # Selectors hand back strings; the entry must store ints.
                "modbus_baudrate": "115200",
                "modbus_unit": "1",
            }
        )

    assert result["type"] == "entry"
    assert result["data"]["modbus_baudrate"] == 115200
    assert result["data"]["modbus_unit"] == 1
    assert result["data"]["modbus_type"] == "serial"
    modbus_flow.async_set_unique_id.assert_awaited_once_with(
        "/dev/ttyUSB0:1", raise_on_progress=False
    )


@pytest.mark.asyncio
async def test_modbus_all_probe_points_timing_out_reports_no_device(modbus_flow):
    fake = _fake_modbus_modules()
    fake.unit.read_input_registers = AsyncMock(side_effect=_FakeModbusTimeout())
    fake.unit.read_holding_registers = AsyncMock(side_effect=_FakeModbusTimeout())
    modbus_flow._modbus_type = "tcp"

    with _install_modbus(fake.root, fake.backend):
        result = await modbus_flow.async_step_modbus_tcp(
            user_input={"modbus_host": "h", "modbus_port": 502, "modbus_unit": 1}
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "no_device"}


@pytest.mark.asyncio
async def test_modbus_connection_failure_reports_cannot_connect(modbus_flow):
    from custom_components.hyxi_cloud.const import MODBUS_TCP_FRAMERS

    fake = _fake_modbus_modules()
    fake.unit.read_input_registers = AsyncMock(side_effect=OSError("no route to host"))
    modbus_flow._modbus_type = "tcp"

    with _install_modbus(fake.root, fake.backend):
        result = await modbus_flow.async_step_modbus_tcp(
            user_input={"modbus_host": "h", "modbus_port": 502, "modbus_unit": 1}
        )

    assert result["errors"] == {"base": "cannot_connect"}
    # A connection-level failure retries under the other framer before
    # giving up -- one connection (and one close()) per framer tried.
    assert fake.connection.close.await_count == len(MODBUS_TCP_FRAMERS)


@pytest.mark.asyncio
async def test_modbus_reports_clearly_when_library_is_missing(modbus_flow):
    """The probe is lazily imported, so a broken install must not traceback."""
    fake = _fake_modbus_modules()
    modbus_flow._modbus_type = "tcp"

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "modbus_connection.tmodbus":
            raise ImportError("no backend")
        return real_import(name, *args, **kwargs)

    with (
        _install_modbus(fake.root, fake.backend),
        patch.object(builtins, "__import__", blocked),
    ):
        result = await modbus_flow.async_step_modbus_tcp(
            user_input={"modbus_host": "h", "modbus_port": 502, "modbus_unit": 1}
        )

    assert result["errors"] == {"base": "modbus_unavailable"}


@pytest.mark.asyncio
async def test_modbus_shows_form_before_any_input(modbus_flow):
    result = await modbus_flow.async_step_modbus_tcp(user_input=None)

    assert result["step_id"] == "modbus_tcp"
    assert result["errors"] == {}


# --- Family detection: does a value at either signature register pick the
# right client, given the two documents' address ranges don't overlap -----


@pytest.mark.asyncio
async def test_modbus_detects_hybrid_family_from_a_real_value(modbus_flow):
    """A value at register 0 -- the hybrid's protocol version -- is treated
    as this being a hybrid inverter, without needing to try register 4980."""
    fake = _fake_modbus_modules()

    async def read(address, _count):
        if address == 0:
            return [42]
        raise AssertionError(f"should not read {address} once 0 succeeded")

    fake.unit.read_input_registers = AsyncMock(side_effect=read)
    modbus_flow._modbus_type = "tcp"

    with _install_modbus(fake.root, fake.backend):
        result = await modbus_flow.async_step_modbus_tcp(
            user_input={"modbus_host": "h", "modbus_port": 502, "modbus_unit": 1}
        )

    assert result["data"]["modbus_family"] == "hybrid"


@pytest.mark.asyncio
async def test_modbus_detects_halo_family_when_only_its_signature_answers(
    modbus_flow,
):
    """Hybrid's register 0 raises (not this family); HALO's 4980 returns a
    value. Both signatures must be tried, not just the first."""
    fake = _fake_modbus_modules()

    async def read(address, _count):
        if address == 0:
            raise _FakeModbusError()
        if address == 4980:
            return [780]
        raise AssertionError(f"unexpected address {address}")

    fake.unit.read_input_registers = AsyncMock(side_effect=read)
    modbus_flow._modbus_type = "tcp"

    with _install_modbus(fake.root, fake.backend):
        result = await modbus_flow.async_step_modbus_tcp(
            user_input={"modbus_host": "h", "modbus_port": 502, "modbus_unit": 1}
        )

    assert result["data"]["modbus_family"] == "halo"


@pytest.mark.asyncio
async def test_modbus_refuses_to_guess_family_when_unidentified(modbus_flow, caplog):
    """A device that answers only with exceptions is confirmed present, but
    neither signature identifies it -- these two registers are the only
    thing distinguishing "definitely a HALO or hybrid inverter" from "some
    other Modbus device on this bus", so guessing wrong here would create
    an entry reading (and writing) another device's registers under the
    wrong map. Refuses rather than defaulting."""
    fake = _fake_modbus_modules()
    fake.unit.read_input_registers = AsyncMock(side_effect=_FakeModbusError())
    modbus_flow._modbus_type = "tcp"

    with _install_modbus(fake.root, fake.backend):
        result = await modbus_flow.async_step_modbus_tcp(
            user_input={"modbus_host": "h", "modbus_port": 502, "modbus_unit": 1}
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "unidentified_family"}
    assert "refusing to guess" in caplog.text


# --- Wire-framing detection: does a TCP gateway that only answers under the
# other framer still get identified, without asking the user to know which
# one their gateway speaks ------------------------------------------------


@pytest.mark.asyncio
async def test_modbus_tcp_accepts_first_framer_that_succeeds(modbus_flow):
    """rtu (tried first) works -- no need to also try socket."""
    probe = AsyncMock(return_value=(None, "halo"))
    modbus_flow._probe_and_detect_modbus = probe

    error, family, framer = await modbus_flow._probe_and_detect_modbus_tcp("h", 502, 1)

    assert (error, family, framer) == (None, "halo", "rtu")
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_modbus_tcp_falls_back_to_the_other_framer(modbus_flow):
    """rtu answers nothing at all -- exactly what a gateway actually
    speaking native Modbus-TCP/MBAP framing looks like from here -- so the
    probe is retried under socket, which is what the device is on."""
    probe = AsyncMock(side_effect=[("no_device", "hybrid"), (None, "hybrid")])
    modbus_flow._probe_and_detect_modbus = probe

    error, family, framer = await modbus_flow._probe_and_detect_modbus_tcp("h", 502, 1)

    assert (error, family, framer) == (None, "hybrid", "socket")
    assert probe.await_count == 2


@pytest.mark.asyncio
async def test_modbus_tcp_reports_the_last_error_when_every_framer_fails(
    modbus_flow,
):
    """Neither framing gets an answer -- nothing left to try, so the second
    attempt's error is what's shown (both are "unreachable", just phrased
    from whichever framer was tried last)."""
    probe = AsyncMock(
        side_effect=[("no_device", "hybrid"), ("cannot_connect", "hybrid")]
    )
    modbus_flow._probe_and_detect_modbus = probe

    error, family, _framer = await modbus_flow._probe_and_detect_modbus_tcp("h", 502, 1)

    assert error == "cannot_connect"
    assert family == "hybrid"
    assert probe.await_count == 2


@pytest.mark.asyncio
async def test_modbus_tcp_does_not_retry_a_missing_library(modbus_flow):
    """modbus_unavailable means the dependency itself is missing -- trying
    the other framer would just fail the exact same way."""
    probe = AsyncMock(return_value=("modbus_unavailable", "hybrid"))
    modbus_flow._probe_and_detect_modbus = probe

    error, _family, _framer = await modbus_flow._probe_and_detect_modbus_tcp(
        "h", 502, 1
    )

    assert error == "modbus_unavailable"
    probe.assert_awaited_once()


def test_transport_schema_defaults_to_cloud(mock_ha_environment):
    """The chooser must preselect cloud -- it is what almost every existing
    user wants, and it is the only reason this is a select and not a menu.
    voluptuous is mocked here, so the recorded vol.Required call args are
    inspected rather than the returned Schema (same rationale as the other
    schema tests in this file)."""
    config_flow_mod = mock_ha_environment
    config_flow_mod.vol.Required.reset_mock()

    config_flow_mod._build_transport_schema()

    defaults = {
        c.args[0]: c.kwargs.get("default")
        for c in config_flow_mod.vol.Required.call_args_list
    }
    assert defaults[config_flow_mod.CONF_TRANSPORT] == config_flow_mod.TRANSPORT_CLOUD
    assert [o["value"] for o in config_flow_mod.TRANSPORT_OPTIONS] == [
        config_flow_mod.TRANSPORT_CLOUD,
        config_flow_mod.TRANSPORT_MODBUS,
    ]


def test_modbus_serial_schema_defaults_to_the_documented_baud_rate(
    mock_ha_environment,
):
    """115200 is what the Micro Storage RS485 document fixes the HALO at, so
    it is the default even though the hybrid units are unconfirmed.

    Baud rate default is asserted as a string: the SelectSelector's option
    values are strings ("115200", not 115200), and a default of the wrong
    type simply fails to preselect anything in the form.
    """
    config_flow_mod = mock_ha_environment
    config_flow_mod.vol.Required.reset_mock()

    config_flow_mod._build_modbus_serial_schema()

    defaults = {
        c.args[0]: c.kwargs.get("default")
        for c in config_flow_mod.vol.Required.call_args_list
    }
    assert defaults[config_flow_mod.CONF_MODBUS_BAUDRATE] == "115200"
    assert defaults[config_flow_mod.CONF_MODBUS_DEVICE] == "/dev/ttyUSB0"
    assert defaults[config_flow_mod.CONF_MODBUS_UNIT] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "cloud_fields_visible"),
    [("cloud", True), ("modbus", False)],
)
async def test_options_hide_cloud_only_settings_for_modbus_entries(
    mock_ha_environment, transport, cloud_fields_visible
):
    """Alarm discovery and real-time push are cloud services with no local
    equivalent, so a Modbus entry must not offer them."""
    config_flow_mod = mock_ha_environment
    config_flow_mod.vol.Optional.reset_mock()

    entry = MagicMock()
    entry.data = {"transport": transport}
    entry.options = {"update_interval": 5}
    entry.entry_id = "abc"

    handler = config_flow_mod.HyxiOptionsFlowHandler(entry)
    handler.hass = None
    handler.async_show_form = MagicMock(side_effect=lambda **kw: {"type": "form", **kw})

    await handler.async_step_init(user_input=None)

    offered = {c.args[0] for c in config_flow_mod.vol.Optional.call_args_list}
    assert (config_flow_mod.CONF_ENABLE_PUSH in offered) is cloud_fields_visible
    assert (config_flow_mod.CONF_BACK_DISCOVERY in offered) is cloud_fields_visible

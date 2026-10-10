"""Integration tests for offering push during the initial cloud setup."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.hyxi_cloud.const import (
    CONF_ACCESS_KEY,
    CONF_ENABLE_PUSH,
    CONF_PUSH_URL,
    CONF_SECRET_KEY,
    CONF_TRANSPORT,
    DOMAIN,
    TRANSPORT_CLOUD,
)
from tests.integration.entries import cloud_entry, serve_devices

_DEVICES = {"SN_A": {"device_type_code": "HYBRID_INVERTER", "metrics": {}}}


@pytest.fixture
def clients():
    """Mocked clients for the config flow's key check and the entry setup."""
    with (
        patch("custom_components.hyxi_cloud.config_flow.HyxiApiClient") as flow_cls,
        patch("custom_components.hyxi_cloud.HyxiApiClient") as entry_cls,
    ):
        flow_client = AsyncMock()
        flow_client.get_all_device_data.return_value = {"data": _DEVICES, "attempts": 1}
        flow_cls.return_value = flow_client
        entry_client = AsyncMock()
        entry_client.compute_derived_metrics = MagicMock(return_value={})
        entry_client.list_subscriptions.return_value = []
        entry_client.subscribe_real_time_data.return_value = {
            "success": True,
            "data": {"subscribeCode": "new-data"},
        }
        entry_client.subscribe_alarm.return_value = {
            "success": True,
            "data": {"subscribeCode": "new-alarm"},
        }
        serve_devices(entry_client, _DEVICES)
        entry_cls.return_value = entry_client
        yield entry_client


async def _cloud_form(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRANSPORT: TRANSPORT_CLOUD}
    )


async def _submit_credentials(hass: HomeAssistant, push: bool) -> dict:
    form = await _cloud_form(hass)
    assert form["step_id"] == "cloud"
    assert CONF_ENABLE_PUSH in form["data_schema"].schema
    assert CONF_PUSH_URL not in form["data_schema"].schema
    return await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk", CONF_ENABLE_PUSH: push},
    )


@pytest.mark.asyncio
async def test_leaving_push_off_creates_the_entry_without_a_push_step(
    hass: HomeAssistant, clients
):
    """With push left off, the credentials page creates the entry, which
    does not subscribe."""
    form = await _cloud_form(hass)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"}
    )
    await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options == {CONF_ENABLE_PUSH: False}
    assert CONF_ENABLE_PUSH not in entry.data
    clients.subscribe_real_time_data.assert_not_called()


@pytest.mark.asyncio
async def test_turning_push_on_asks_for_the_callback_url_next(
    hass: HomeAssistant, clients
):
    """With push turned on, a second page asks for the callback URL; left
    empty, the entry uses Home Assistant's external HTTPS URL and
    subscribes with it."""
    hass.config.external_url = "https://ha.example.com"

    push_page = await _submit_credentials(hass, push=True)
    assert push_page["step_id"] == "cloud_push"
    result = await hass.config_entries.flow.async_configure(push_page["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options == {CONF_ENABLE_PUSH: True}
    url = clients.subscribe_real_time_data.await_args.args[0]
    assert url.startswith("https://ha.example.com/api/webhook/")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("external_url", "cloud_active", "push_url", "error"),
    [
        (None, False, "", "no_public_url"),
        ("http://ha.example.com", False, "", "no_public_url"),
        (None, True, "", None),
        (None, False, " https://proxy.example.com ", None),
        (None, False, "http://proxy.example.com", "no_public_url"),
        (None, False, "https://", "no_public_url"),
    ],
    ids=[
        "no-url",
        "plain-http",
        "home-assistant-cloud",
        "custom-https",
        "custom-http",
        "custom-without-host",
    ],
)
async def test_the_push_page_needs_a_public_https_url(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    hass: HomeAssistant, clients, external_url, cloud_active, push_url, error
):
    """The push page is accepted only when HYXI's cloud could reach Home
    Assistant: through a custom HTTPS URL, Home Assistant Cloud, or an
    external HTTPS URL. A custom URL is kept in the entry's options."""
    hass.config.internal_url = "http://192.168.1.10:8123"
    hass.config.external_url = external_url
    if cloud_active:
        hass.config.components.add("cloud")
    push_page = await _submit_credentials(hass, push=True)

    cloud = MagicMock(
        async_active_subscription=MagicMock(return_value=True),
        async_get_or_create_cloudhook=AsyncMock(
            return_value="https://hooks.nabu.casa/hook"
        ),
    )
    with patch.dict(sys.modules, {"homeassistant.components.cloud": cloud}):
        result = await hass.config_entries.flow.async_configure(
            push_page["flow_id"], {CONF_PUSH_URL: push_url}
        )
        await hass.async_block_till_done()

    if error:
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "cloud_push"
        assert result["errors"] == {"base": error}
        # What was typed is kept, to correct rather than retype.
        suggested = {
            str(key): key.description.get("suggested_value")
            for key in result["data_schema"].schema
            if key.description
        }
        assert suggested == {CONF_PUSH_URL: push_url}
    else:
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        options = hass.config_entries.async_entries(DOMAIN)[0].options
        assert options.get(CONF_PUSH_URL) == (push_url.strip() or None)
        callback = clients.subscribe_real_time_data.await_args.args[0]
        expected = push_url.strip() or "https://hooks.nabu.casa/hook"
        assert callback.startswith(expected)


@pytest.mark.asyncio
async def test_push_is_not_subscribed_to_an_internal_url(hass: HomeAssistant, clients):
    """An entry with push on but only an internal, plain-HTTP address does
    not subscribe HYXI to it: HYXI's cloud could never reach it."""
    hass.config.internal_url = "http://192.168.1.10:8123"
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_ENABLE_PUSH: True})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    clients.subscribe_real_time_data.assert_not_called()
    assert hass.data[DOMAIN][entry.entry_id].push_status == "error"


@pytest.mark.asyncio
async def test_reauth_does_not_offer_push(hass: HomeAssistant, clients):
    """Re-entering keys is not the place to change push."""
    entry = cloud_entry(hass)

    result = await entry.start_reauth_flow(hass)

    assert result["step_id"] == "reauth_confirm"
    assert CONF_ENABLE_PUSH not in result["data_schema"].schema


async def _turn_push_on_in_options(hass: HomeAssistant, entry) -> dict:
    """Open the options, turn push on and return the form that then shows
    the push rate and URL."""
    form = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        form["flow_id"], form["data_schema"]({CONF_ENABLE_PUSH: True})
    )
    assert CONF_PUSH_URL in form["data_schema"].schema
    return form


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("push_url", "error"),
    [
        ("", "no_public_url"),
        (" https://proxy.example.com ", None),
        ("http://proxy.example.com", "no_public_url"),
    ],
    ids=["no-url", "custom-https", "custom-http"],
)
async def test_the_options_check_push_the_same_way(
    hass: HomeAssistant, clients, push_url, error
):
    """Turning push on in the options is refused, keeping the form open,
    unless HYXI's cloud could reach the callback; a custom HTTPS URL is
    saved without surrounding spaces."""
    hass.config.internal_url = "http://192.168.1.10:8123"
    entry = cloud_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    form = await _turn_push_on_in_options(hass, entry)

    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        form["data_schema"]({CONF_ENABLE_PUSH: True, CONF_PUSH_URL: push_url}),
    )
    await hass.async_block_till_done()

    if error:
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == {"base": error}
        assert not entry.options.get(CONF_ENABLE_PUSH)
    else:
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_ENABLE_PUSH] is True
        assert entry.options[CONF_PUSH_URL] == push_url.strip()

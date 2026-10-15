"""Real-hass tests for how rejected HYXI API keys are reported.

These drive the real HyxiApiClient, answering its HTTP requests with
aioclient_mock, so they exercise the client's own error contract rather than
a stand-in for it.
"""

from unittest.mock import patch

import pytest
from aiohttp import ClientError
from homeassistant import config_entries, data_entry_flow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.hyxi_cloud.const import (
    BASE_URL_DEFAULT,
    CONF_ACCESS_KEY,
    CONF_SECRET_KEY,
    CONF_TRANSPORT,
    DOMAIN,
    TRANSPORT_CLOUD,
)
from tests.integration.entries import cloud_entry, seed_cache

TOKEN_URL = f"{BASE_URL_DEFAULT}/api/authorization/v1/token"
# A000005 (signature verification failed) is what a wrong secret key gets.
REJECTED = {"success": False, "code": "A000005", "msg": "Signature verification failed"}


def _token(authorization_type) -> dict:
    """A token answer reporting the keys' authorization type, as HYXI sends
    it (an integer), or none at all for None."""
    data = {"token": "abc", "expiresIn": 3600}
    if authorization_type is not None:
        data["authorizationType"] = authorization_type
    return {"success": True, "data": data}


TOKEN_OK = _token(None)


async def _enter_cloud_keys(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRANSPORT: TRANSPORT_CLOUD}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"}
    )


def _mock_one_device_account(aioclient_mock: AiohttpClientMocker) -> None:
    """Answer discovery and polling for an account with one inverter."""
    aioclient_mock.post(
        f"{BASE_URL_DEFAULT}/api/plant/v1/page",
        json={"success": True, "data": {"list": [{"plantId": "P1"}]}},
    )
    aioclient_mock.post(
        f"{BASE_URL_DEFAULT}/api/plant/v1/devicePage",
        json={
            "success": True,
            "data": {
                "deviceList": [{"deviceSn": "SN1", "deviceType": "HYBRID_INVERTER"}]
            },
        },
    )
    aioclient_mock.post(
        f"{BASE_URL_DEFAULT}/api/device/v1/getSubDevicePage",
        json={"success": True, "data": {"childDevice": []}},
    )
    aioclient_mock.post(
        f"{BASE_URL_DEFAULT}/api/alarm/v1/plantAlarmPage",
        json={"success": True, "data": {"pageData": []}},
    )
    aioclient_mock.get(
        f"{BASE_URL_DEFAULT}/api/device/v1/queryDeviceInfo",
        json={"success": True, "data": {}},
    )
    aioclient_mock.get(
        f"{BASE_URL_DEFAULT}/api/device/v2/queryDeviceData",
        json={"success": True, "data": []},
    )


@pytest.mark.parametrize(
    "token_answer, error",
    [({"json": REJECTED}, "invalid_auth"), ({"exc": ClientError()}, "cannot_connect")],
    ids=["keys-rejected", "cloud-unreachable"],
)
async def test_config_flow_reports_token_failures(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_answer, error
):
    """Rejected keys are reported as invalid_auth, and an unreachable cloud as
    cannot_connect, rather than one being mistaken for the other."""
    aioclient_mock.post(TOKEN_URL, **token_answer)

    result = await _enter_cloud_keys(hass)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": error}


@patch("custom_components.hyxi_cloud.coordinator.AUTH_RECHECK_DELAY", 0)
async def test_setup_with_rejected_keys_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """Keys that are still rejected on the re-check fail setup and start a
    reauth flow."""
    aioclient_mock.post(TOKEN_URL, json=REJECTED)
    entry = cloud_entry(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [TOKEN_URL] * 2


@patch("custom_components.hyxi_cloud.coordinator.AUTH_RECHECK_DELAY", 0)
async def test_one_off_key_rejection_does_not_start_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """A single rejection that the re-check does not repeat loads normally,
    without asking the user to re-enter their keys."""
    answers = [REJECTED]

    async def token(method, url, data):
        return AiohttpClientMockResponse(
            method, url, json=answers.pop(0) if answers else TOKEN_OK
        )

    aioclient_mock.post(TOKEN_URL, side_effect=token)
    _mock_one_device_account(aioclient_mock)
    entry = cloud_entry(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


@pytest.mark.parametrize(
    ("authorization_type", "error"),
    [(1, "vpp_keys"), (2, "plant_keys")],
    ids=["vpp", "plant"],
)
async def test_config_flow_refuses_keys_of_another_kind(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, authorization_type, error
):
    """VPP or Plant Authorization keys are refused, saying which kind they
    are, before any device is read."""
    aioclient_mock.post(TOKEN_URL, json=_token(authorization_type))

    result = await _enter_cloud_keys(hass)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [TOKEN_URL]


@pytest.mark.parametrize(
    "authorization_type", [0, "0", None], ids=["user", "user-as-text", "not-reported"]
)
async def test_config_flow_accepts_user_keys(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, authorization_type
):
    """User Authorization keys are accepted, and so are keys whose type
    HYXI doesn't report."""
    aioclient_mock.post(TOKEN_URL, json=_token(authorization_type))
    _mock_one_device_account(aioclient_mock)

    result = await _enter_cloud_keys(hass)

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


async def test_reauth_refuses_keys_of_another_kind(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
):
    """Reauthentication refuses VPP Authorization keys the same way."""
    aioclient_mock.post(TOKEN_URL, json=_token(1))
    entry = cloud_entry(hass)
    result = await entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"}
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "vpp_keys"}


@pytest.mark.parametrize(
    ("authorization_type", "devices_readable"),
    [(1, False), (1, True), (2, False)],
    ids=["vpp-unreadable", "vpp-readable", "plant"],
)
async def test_setup_with_keys_of_another_kind_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    authorization_type,
    devices_readable,
):
    """An entry whose keys turn out to be VPP or Plant Authorization keys
    fails setup and asks for new keys, whether or not the devices could be
    read, rather than retrying setup; no device is read with them."""
    aioclient_mock.post(TOKEN_URL, json=_token(authorization_type))
    if devices_readable:
        _mock_one_device_account(aioclient_mock)
    entry = cloud_entry(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [TOKEN_URL]


@pytest.mark.parametrize("authorization_type", [0, None], ids=["user", "not-reported"])
async def test_setup_with_user_keys_loads(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, authorization_type
):
    """An entry with User Authorization keys, or keys whose kind HYXI
    doesn't report, loads as before."""
    aioclient_mock.post(TOKEN_URL, json=_token(authorization_type))
    _mock_one_device_account(aioclient_mock)
    entry = cloud_entry(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED


async def test_keys_of_another_kind_found_after_a_cached_start_start_reauth(
    hass: HomeAssistant, hass_storage, aioclient_mock: AiohttpClientMocker
):
    """An entry that loaded from cached devices while HYXI was unreachable
    asks for new keys once the next discovery learns they are VPP
    Authorization keys."""
    entry = cloud_entry(hass)
    seed_cache(
        hass_storage,
        entry,
        {"SN1": {"device_type_code": "HYBRID_INVERTER", "metrics": {}}},
    )
    unreachable = [ClientError(), ClientError()]

    async def token(method, url, data):
        if unreachable:
            raise unreachable.pop(0)
        return AiohttpClientMockResponse(method, url, json=_token(1))

    aioclient_mock.post(TOKEN_URL, side_effect=token)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    await entry.runtime_data.discovery.async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]

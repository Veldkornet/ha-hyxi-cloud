"""Integration tests for the config entry diagnostics, downloaded through
Home Assistant's diagnostics endpoint."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from hyxi_cloud_api import Subscription, SubscriptionType
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.hyxi_cloud.const import CONF_ENABLE_PUSH, CONF_PUSH_URL
from tests.integration.entries import accept_subscriptions, cloud_entry, serve_devices
from tests.integration.test_modbus import _modbus_entry, _seeded_connection

SERIAL = "10201234567810"
BATTERY_SERIAL = "4213571357"
_DEVICES = {
    SERIAL: {
        "device_name": "Smith Garden Inverter",
        "device_type_code": "HYBRID_INVERTER",
        "metrics": {
            "batSoc": "50",
            "deviceSn": SERIAL,
            "batSn": BATTERY_SERIAL,
            "plantId": "plant-123456",
        },
    }
}
_LISTED = Subscription(
    subscribe_code="code-listed",
    subscribe_type=SubscriptionType.REAL_TIME_DATA,
    callback_url="https://listed.example/api/webhook/listed-hook",
    create_time="2026-09-27 18:20:11",
    devices=(SERIAL,),
)


@pytest.fixture
def client():
    """The mocked HyxiApiClient the cloud entry gets, with push accepted."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        serve_devices(mock_client, _DEVICES)
        accept_subscriptions(mock_client)
        mock_client.list_subscriptions.return_value = [_LISTED]
        mock_client.cancel_subscription.return_value = {"success": True}
        client_class.return_value = mock_client
        yield mock_client


def _assert_absent(diagnostics: dict, *secrets: str) -> None:
    """None of `secrets` appears anywhere in the diagnostics."""
    text = json.dumps(diagnostics)
    for secret in secrets:
        assert secret not in text, secret


@pytest.mark.asyncio
async def test_cloud_diagnostics_hide_keys_codes_and_serials(
    hass: HomeAssistant, hass_client, client
):
    """A cloud entry's diagnostics show its settings, status, push state,
    subscriptions and devices, without its API keys, callback URLs, webhook
    IDs, subscription codes, plant ID, device name or serial numbers;
    serials appear as placeholders."""
    entry = cloud_entry(hass, "access-key-1234")
    hass.config_entries.async_update_entry(
        entry,
        options={CONF_ENABLE_PUSH: True, CONF_PUSH_URL: "https://my-home.example"},
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator.push_error = (
        f"HYXI refused {coordinator.push_url} for {SERIAL} (code-push, access-key-1234)"
    )

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["push"]["error"] == (
        f"HYXI refused {REDACTED} for SN_1 ({REDACTED}, {REDACTED})"
    )
    _assert_absent(
        diagnostics,
        "access-key-1234",
        "my-home.example",
        "listed.example",
        "code-listed",
        "code-push",
        "code-alarm",
        coordinator.webhook_id,
        coordinator.alarm_webhook_id,
        "plant-123456",
        "Smith Garden",
        SERIAL,
        BATTERY_SERIAL,
    )
    assert diagnostics["entry"]["options"][CONF_ENABLE_PUSH] is True
    assert diagnostics["push"]["status"] == "active"
    assert (
        diagnostics["status"]["api_status"] == coordinator.hyxi_metadata["api_status"]
    )
    [listed] = diagnostics["subscriptions"]
    assert listed["devices"] == ["SN_1"]
    assert listed["subscribe_type"] == SubscriptionType.REAL_TIME_DATA
    device = diagnostics["devices"]["SN_1"]
    assert device["metrics"]["batSoc"] == "50"
    assert diagnostics["energy_manager"] is None


@pytest.mark.asyncio
async def test_diagnostics_show_the_energy_manager(
    hass: HomeAssistant, hass_client, client
):
    """With an Energy Manager, the diagnostics show what it is doing."""
    entry = cloud_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.engine = SimpleNamespace(
        is_managing=True, decision="solar_charge", current_mode="charge"
    )
    try:
        diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    finally:
        entry.runtime_data.engine = None

    assert diagnostics["energy_manager"] == {
        "managing": True,
        "decision": "solar_charge",
        "current_mode": "charge",
    }


@pytest.mark.asyncio
async def test_modbus_diagnostics_hide_the_address(hass: HomeAssistant, hass_client):
    """A Modbus entry's diagnostics hide its address, in its settings and in
    its title, but not a longer address that merely starts with it; and the
    Energy Manager's inverter serial even when it isn't among the current
    devices."""
    entry = _modbus_entry(
        hass,
        modbus_family="halo",
        modbus_host="192.168.1.5",
        options={"em_inverter_sn": "99990000111122"},
    )
    hass.config_entries.async_update_entry(entry, title="HYXI Modbus (192.168.1.5)")
    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entry.runtime_data.hyxi_metadata["last_error"] = "No reply from 192.168.1.50"

        diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    _assert_absent(diagnostics, "99990000111122")
    assert diagnostics["entry"]["data"]["modbus_host"] == REDACTED
    assert diagnostics["status"]["last_error"] == "No reply from 192.168.1.50"
    assert diagnostics["entry"]["options"]["em_inverter_sn"] == REDACTED
    assert diagnostics["entry"]["title"] == f"HYXI Modbus ({REDACTED})"
    assert diagnostics["entry"]["data"]["modbus_port"] == 502
    assert diagnostics["subscriptions"] is None
    assert diagnostics["devices"]


@pytest.mark.asyncio
async def test_diagnostics_of_an_entry_retrying_setup(
    hass: HomeAssistant, hass_client, client
):
    """An entry that isn't loaded still gives its settings, without a
    coordinator to report on."""
    client.discover_devices.side_effect = TimeoutError("unreachable")
    entry = cloud_entry(hass, "access-key-1234")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    _assert_absent(diagnostics, "access-key-1234")
    assert diagnostics["entry"]["state"] == "setup_retry"
    assert "devices" not in diagnostics

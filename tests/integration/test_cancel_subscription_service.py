"""Integration tests for the admin-only hyxi_cloud.cancel_subscription
service and the entry it cancels with."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.setup import async_setup_component
from hyxi_cloud_api import Subscription, SubscriptionType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyxi_cloud.const import DOMAIN
from tests.integration.entries import cloud_entry, serve_devices
from tests.integration.test_modbus import _modbus_entry, _seeded_connection

_DEVICE = {
    "SN_A": {
        "device_name": "Inverter",
        "device_type_code": "HYBRID_INVERTER",
        "metrics": {"batSoc": "50", "deviceSn": "SN_A"},
    }
}

# A subscription HYXI lists for an entry's API keys.
_HELD = Subscription("code-x", SubscriptionType.ALARM, "", "", ())


def _client() -> AsyncMock:
    client = AsyncMock()
    client.compute_derived_metrics = MagicMock(return_value={})
    serve_devices(client, _DEVICE)
    client.list_subscriptions.return_value = []
    client.cancel_subscription.return_value = {"success": True}
    return client


@pytest.fixture
def clients():
    """Two mocked HyxiApiClients, one for each entry set up, in order."""
    first, second = _client(), _client()
    with patch(
        "custom_components.hyxi_cloud.HyxiApiClient", side_effect=[first, second]
    ):
        yield first, second


async def _add_entry(hass: HomeAssistant, key: str) -> MockConfigEntry:
    entry = cloud_entry(hass, key)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _cancel(hass: HomeAssistant, context: Context | None = None, **data):
    await hass.services.async_call(
        DOMAIN,
        "cancel_subscription",
        {"subscribe_code": "code-x", **data},
        blocking=True,
        context=context,
    )


@pytest.mark.asyncio
async def test_the_service_is_registered_without_entries(hass: HomeAssistant):
    """The services are registered when the integration loads, before any
    entry is set up."""
    assert await async_setup_component(hass, DOMAIN, {})

    assert hass.services.has_service(DOMAIN, "cancel_subscription")
    assert hass.services.has_service(DOMAIN, "set_battery_mode")


@pytest.mark.asyncio
async def test_only_admins_can_cancel(hass: HomeAssistant, clients, hass_admin_user):
    """A non-admin user may not cancel a subscription; an admin may."""
    await _add_entry(hass, "ak1")
    user = await hass.auth.async_create_user("Viewer", group_ids=["system-users"])

    with pytest.raises(Unauthorized):
        await _cancel(hass, Context(user_id=user.id))
    clients[0].cancel_subscription.assert_not_awaited()

    await _cancel(hass, Context(user_id=hass_admin_user.id))
    clients[0].cancel_subscription.assert_awaited_once_with("code-x")


@pytest.mark.asyncio
async def test_the_named_entry_cancels(hass: HomeAssistant, clients):
    """A named entry cancels with its own API keys, even when another
    entry's subscription list holds the code."""
    clients[0].list_subscriptions.return_value = [_HELD]
    await _add_entry(hass, "ak1")
    second = await _add_entry(hass, "ak2")

    await _cancel(hass, config_entry_id=second.entry_id)

    clients[1].cancel_subscription.assert_awaited_once_with("code-x")
    clients[0].cancel_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_entry_that_is_not_loaded_is_refused(hass: HomeAssistant, clients):
    """Naming an entry that isn't a loaded HYXI Cloud entry is refused."""
    await _add_entry(hass, "ak1")

    with pytest.raises(ServiceValidationError, match="not a loaded HYXI Cloud"):
        await _cancel(hass, config_entry_id="not-an-entry")
    clients[0].cancel_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_naming_a_modbus_entry_is_refused(hass: HomeAssistant, clients):
    """Naming a loaded Modbus entry is refused as one: subscriptions belong
    to HYXI Cloud entries."""
    await _add_entry(hass, "ak1")
    modbus = _modbus_entry(hass, modbus_family="halo")
    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(modbus.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(ServiceValidationError, match="is a Modbus entry"):
            await _cancel(hass, config_entry_id=modbus.entry_id)
    clients[0].cancel_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_services_stay_after_the_last_entry_unloads(hass: HomeAssistant, clients):
    """The services stay registered after the last entry unloads, as
    services registered by the integration rather than an entry."""
    entry = await _add_entry(hass, "ak1")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "cancel_subscription")
    assert hass.services.has_service(DOMAIN, "set_battery_mode")


@pytest.mark.asyncio
async def test_without_a_name_the_holding_entry_cancels(hass: HomeAssistant, clients):
    """Without a named entry, the entry whose HYXI list holds the code
    cancels it."""
    clients[1].list_subscriptions.return_value = [_HELD]
    await _add_entry(hass, "ak1")
    await _add_entry(hass, "ak2")

    await _cancel(hass)

    clients[1].cancel_subscription.assert_awaited_once_with("code-x")


@pytest.mark.asyncio
async def test_an_unlisted_code_with_several_entries_needs_a_name(
    hass: HomeAssistant, clients
):
    """With several HYXI Cloud entries and none listing the code, the call
    asks for the entry rather than guessing one."""
    await _add_entry(hass, "ak1")
    await _add_entry(hass, "ak2")

    with pytest.raises(ServiceValidationError, match="Choose the entry"):
        await _cancel(hass)
    clients[0].cancel_subscription.assert_not_awaited()
    clients[1].cancel_subscription.assert_not_awaited()

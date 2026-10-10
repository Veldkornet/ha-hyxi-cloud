"""Integration tests for listing the push subscriptions HYXI holds on the
Subscription Status sensor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from hyxi_cloud_api import Subscription, SubscriptionType

from custom_components.hyxi_cloud.const import (
    CONF_ENABLE_PUSH,
    CONF_PUSH_URL,
    DOMAIN,
)
from tests.integration.entries import cloud_entry, serve_devices

_DEVICE = {
    "SN_A": {
        "device_name": "Inverter",
        "device_type_code": "HYBRID_INVERTER",
        "metrics": {"batSoc": "50", "deviceSn": "SN_A"},
    }
}

_ALARM = Subscription(
    subscribe_code="code-alarm",
    subscribe_type=SubscriptionType.ALARM,
    callback_url="https://other.example/alarm",
    create_time="2026-09-27 18:20:11",
    devices=(),
)
_NEWER_TYPE = Subscription(
    subscribe_code="code-new",
    subscribe_type=9,
    callback_url="https://other.example/new",
    create_time="2026-09-28 10:30:00",
    devices=("SN_A",),
)


@pytest.fixture
def client():
    """The mocked HyxiApiClient the entry gets."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        serve_devices(mock_client, _DEVICE)
        mock_client.list_subscriptions.return_value = [_ALARM, _NEWER_TYPE]
        mock_client.cancel_subscription.return_value = {"success": True}
        client_class.return_value = mock_client
        yield mock_client


def _status_attributes(hass: HomeAssistant, entry) -> dict:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_realtime_subscription_status"
    )
    return dict(hass.states.get(entity_id).attributes)


@pytest.mark.asyncio
async def test_status_sensor_lists_the_subscriptions_hyxi_holds(
    hass: HomeAssistant, client
):
    """The Subscription Status sensor shows every subscription HYXI holds,
    with its code, type, callback URL, creation time and devices."""
    entry = cloud_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _status_attributes(hass, entry)["subscriptions"] == [
        {
            "subscribe_code": "code-alarm",
            "type": "alarm",
            "callback_url": "https://other.example/alarm",
            "created": "2026-09-27 18:20:11",
            "devices": [],
        },
        {
            "subscribe_code": "code-new",
            "type": 9,
            "callback_url": "https://other.example/new",
            "created": "2026-09-28 10:30:00",
            "devices": ["SN_A"],
        },
    ]
    assert "known_subscription_codes" not in _status_attributes(hass, entry)


@pytest.mark.asyncio
async def test_cancelling_a_subscription_refreshes_the_list(
    hass: HomeAssistant, client
):
    """Cancelling through the service lists HYXI's subscriptions again, so
    the sensor stops showing the cancelled one."""
    entry = cloud_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client.list_subscriptions.return_value = [_NEWER_TYPE]
    await hass.services.async_call(
        DOMAIN, "cancel_subscription", {"subscribe_code": "code-alarm"}, blocking=True
    )
    await hass.async_block_till_done()

    client.cancel_subscription.assert_awaited_once_with("code-alarm")
    codes = [
        s["subscribe_code"] for s in _status_attributes(hass, entry)["subscriptions"]
    ]
    assert codes == ["code-new"]


@pytest.mark.asyncio
async def test_an_unreachable_list_keeps_the_last_one(hass: HomeAssistant, client):
    """When HYXI's list can't be fetched, the last known list stays."""
    entry = cloud_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client.list_subscriptions.side_effect = TimeoutError("unreachable")
    await hass.services.async_call(
        DOMAIN, "cancel_subscription", {"subscribe_code": "code-alarm"}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(_status_attributes(hass, entry)["subscriptions"]) == 2


@pytest.mark.asyncio
async def test_setup_removes_the_purge_button_and_old_code_store(
    hass: HomeAssistant, hass_storage, client
):
    """The Purge Old Subscriptions button and the subscription code list
    earlier versions kept are removed."""
    entry = cloud_entry(hass)
    registry = er.async_get(hass)
    purge = registry.async_get_or_create(
        "button",
        DOMAIN,
        f"{entry.entry_id}_purge_old_subscriptions",
        config_entry=entry,
    )
    hass_storage["hyxi_cloud_subscriptions"] = {
        "version": 1,
        "minor_version": 1,
        "key": "hyxi_cloud_subscriptions",
        "data": {"codes": ["code-alarm"]},
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(purge.entity_id) is None
    assert "hyxi_cloud_subscriptions" not in hass_storage


@pytest.mark.asyncio
async def test_the_list_is_refreshed_with_each_discovery(hass: HomeAssistant, client):
    """HYXI's list is fetched again whenever devices are rediscovered, so
    the sensor follows subscriptions made or cancelled elsewhere."""
    entry = cloud_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client.list_subscriptions.return_value = [_NEWER_TYPE]
    await hass.data[DOMAIN][entry.entry_id].discovery.async_refresh()
    await hass.async_block_till_done()

    codes = [
        s["subscribe_code"] for s in _status_attributes(hass, entry)["subscriptions"]
    ]
    assert codes == ["code-new"]


@pytest.mark.asyncio
async def test_a_rejected_listing_is_logged_as_a_warning(
    hass: HomeAssistant, client, caplog
):
    """HYXI rejecting the list request is worth a warning, unlike being
    unreachable; the sensor then lists nothing."""
    client.list_subscriptions.side_effect = RuntimeError("request failed")
    entry = cloud_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "HYXI did not list the push subscriptions" in caplog.text
    assert _status_attributes(hass, entry)["subscriptions"] == []


def _subscription(code, subscribe_type, devices, url="https://old.example/cb"):
    return Subscription(code, subscribe_type, url, "2026-09-01 00:00:00", devices)


async def _setup_with_push(hass: HomeAssistant, client, held):
    client.list_subscriptions.return_value = held
    client.subscribe_real_time_data.return_value = {
        "success": True,
        "data": {"subscribeCode": "new-data"},
    }
    client.subscribe_alarm.return_value = {
        "success": True,
        "data": {"subscribeCode": "new-alarm"},
    }
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={CONF_ENABLE_PUSH: True, CONF_PUSH_URL: "https://ha.example"},
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.asyncio
async def test_enabling_push_cancels_subscriptions_that_would_block_it(
    hass: HomeAssistant, client, caplog
):
    """Before subscribing, existing subscriptions of the same type for this
    entry's devices -- or for no devices in particular -- are cancelled,
    since HYXI rejects a second one; others are left alone."""
    held = [
        _subscription("old-data", SubscriptionType.REAL_TIME_DATA, ("SN_A",)),
        _subscription("old-alarm", SubscriptionType.ALARM, ()),
        _subscription("elsewhere", SubscriptionType.REAL_TIME_DATA, ("SN_OTHER",)),
    ]

    await _setup_with_push(hass, client, held)

    cancelled = {call.args[0] for call in client.cancel_subscription.await_args_list}
    assert cancelled == {"old-data", "old-alarm"}
    client.subscribe_real_time_data.assert_awaited_once()
    client.subscribe_alarm.assert_awaited_once()
    assert caplog.text.count("cancelling existing subscription") == 2


@pytest.mark.asyncio
async def test_a_blocking_subscription_that_cannot_be_cancelled_is_reported(
    hass: HomeAssistant, client, caplog
):
    """A failed cancel is logged and the new subscription is still tried."""
    client.cancel_subscription.return_value = {"success": False, "msg": "busy"}

    await _setup_with_push(
        hass,
        client,
        [_subscription("old-data", SubscriptionType.REAL_TIME_DATA, ("SN_A",))],
    )

    assert "could not cancel subscription" in caplog.text
    client.subscribe_real_time_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_renewing_cancels_each_own_subscription_once(
    hass: HomeAssistant, client, caplog
):
    """Renew cancels the entry's own subscriptions once each before
    subscribing again, rather than once more as blocking subscriptions."""
    entry = await _setup_with_push(hass, client, [])
    held = [
        _subscription("new-data", SubscriptionType.REAL_TIME_DATA, ("SN_A",)),
        _subscription("new-alarm", SubscriptionType.ALARM, ()),
    ]
    client.list_subscriptions.return_value = held
    await hass.data[DOMAIN][entry.entry_id].discovery.async_refresh()
    await hass.async_block_till_done()
    client.cancel_subscription.reset_mock()

    renew = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_renew_realtime_subscription"
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": renew}, blocking=True
    )

    cancelled = [call.args[0] for call in client.cancel_subscription.await_args_list]
    assert sorted(cancelled) == ["new-alarm", "new-data"]
    assert "cancelling existing subscription" not in caplog.text

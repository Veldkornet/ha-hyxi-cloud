"""Integration tests for the entry's random push and alarm webhook IDs."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from hyxi_cloud_api import Subscription, SubscriptionType

from custom_components.hyxi_cloud import _compute_subscription_fingerprint
from custom_components.hyxi_cloud.const import (
    CONF_ENABLE_PUSH,
    CONF_PUSH_URL,
    DEFAULT_PUSH_RATE,
)
from tests.integration.entries import accept_subscriptions, cloud_entry, serve_devices

BASE_URL = "https://hyxi.example"

_DEVICE = {
    "SN_A": {
        "device_name": "Inverter",
        "device_type_code": "HYBRID_INVERTER",
        "metrics": {"batSoc": "50", "deviceSn": "SN_A"},
    }
}


@pytest.fixture
def client():
    """The mocked HyxiApiClient the entry gets, accepting subscriptions."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.access_key = "ak"
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        mock_client.process_push_data = MagicMock(return_value={})
        serve_devices(mock_client, _DEVICE)
        mock_client.list_subscriptions.return_value = []
        mock_client.cancel_subscription.return_value = {"success": True}
        accept_subscriptions(mock_client)
        client_class.return_value = mock_client
        yield mock_client


@pytest.fixture
def cloud():
    """Home Assistant Cloud, set up, with its cloudhook calls mocked."""
    module = MagicMock(async_delete_cloudhook=AsyncMock())
    with patch.dict(sys.modules, {"homeassistant.components.cloud": module}):
        yield module


def _push_entry(hass: HomeAssistant):
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PUSH: True, CONF_PUSH_URL: BASE_URL}
    )
    return entry


async def _setup(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _subscribed_url(mock_call) -> str:
    return mock_call.call_args.args[0]


def _held(code: str, subscribe_type: SubscriptionType) -> Subscription:
    """A subscription HYXI lists for the entry's key pair."""
    return Subscription(code, subscribe_type, "", "", ("SN_A",))


@pytest.mark.asyncio
async def test_push_webhooks_use_random_ids(
    hass: HomeAssistant, client, hass_client_no_auth
):
    """Push and alarm subscriptions get callback URLs with random webhook
    IDs that don't contain the entry ID, and pushes to them are handled."""
    entry = _push_entry(hass)

    await _setup(hass, entry)

    push_id = entry.data["webhook_id"]
    alarm_id = entry.data["alarm_webhook_id"]
    assert push_id != alarm_id
    for webhook_id in (push_id, alarm_id):
        assert len(webhook_id) >= 32
        assert entry.entry_id not in webhook_id
    assert _subscribed_url(client.subscribe_real_time_data) == (
        f"{BASE_URL}/api/webhook/{push_id}"
    )
    assert _subscribed_url(client.subscribe_alarm) == (
        f"{BASE_URL}/api/webhook/{alarm_id}"
    )

    http = await hass_client_no_auth()
    response = await http.post(
        f"/api/webhook/{push_id}", json={"dataList": []}, headers={"accessKey": "ak"}
    )
    assert response.status == 200
    client.process_push_data.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_ids_are_kept_across_reloads(hass: HomeAssistant, client):
    """A reload keeps the entry's webhook IDs, so its subscriptions are
    reused rather than made again."""
    entry = _push_entry(hass)
    await _setup(hass, entry)
    ids = (entry.data["webhook_id"], entry.data["alarm_webhook_id"])
    client.list_subscriptions.return_value = [
        _held("code-push", SubscriptionType.REAL_TIME_DATA),
        _held("code-alarm", SubscriptionType.ALARM),
    ]

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert (entry.data["webhook_id"], entry.data["alarm_webhook_id"]) == ids
    client.subscribe_real_time_data.assert_awaited_once()
    client.subscribe_alarm.assert_awaited_once()


def _upgraded_entry(hass: HomeAssistant, client, *, subscribed: bool = True):
    """A push entry as an earlier version left it: no random webhook IDs,
    and (if subscribed) push and alarm subscriptions HYXI holds, made with
    the webhook IDs derived from the entry ID. Returns the entry and those
    IDs."""
    hass.config.components.add("cloud")
    entry = _push_entry(hass)
    old_ids = {
        "push": f"hyxi_cloud_{entry.entry_id}",
        "alarm": f"hyxi_cloud_{entry.entry_id}_alarm",
    }
    if subscribed:
        data = {}
        for kind, old_id in old_ids.items():
            data[f"{kind}_subscribe_code"] = f"old-{kind}"
            data[f"{kind}_subscribe_fingerprint"] = _compute_subscription_fingerprint(
                f"{BASE_URL}/api/webhook/{old_id}",
                ["SN_A"],
                DEFAULT_PUSH_RATE * 1000,
            )
        hass.config_entries.async_update_entry(entry, data={**entry.data, **data})
        client.list_subscriptions.return_value = [
            _held("old-push", SubscriptionType.REAL_TIME_DATA),
            _held("old-alarm", SubscriptionType.ALARM),
        ]
    return entry, old_ids


def _deleted_cloudhooks(cloud) -> set[str]:
    return {c.args[1] for c in cloud.async_delete_cloudhook.await_args_list}


def _refuse_subscriptions(client) -> None:
    """HYXI refuses to cancel the old subscriptions, and so refuses new
    ones for the same devices."""
    client.cancel_subscription.return_value = {"success": False, "msg": "busy"}
    refused = {"success": False, "msg": "repeatedly"}
    client.subscribe_real_time_data.return_value = refused
    client.subscribe_alarm.return_value = refused


@pytest.mark.asyncio
async def test_an_upgraded_entry_moves_to_random_webhook_ids(
    hass: HomeAssistant, client, cloud
):
    """An entry subscribed with the webhook IDs earlier versions derived
    from the entry ID cancels those subscriptions, subscribes with random
    IDs and then deletes the old cloudhooks."""
    entry, old_ids = _upgraded_entry(hass, client)

    await _setup(hass, entry)

    cancelled = {c.args[0] for c in client.cancel_subscription.await_args_list}
    assert cancelled >= {"old-push", "old-alarm"}
    assert entry.data["push_subscribe_code"] == "code-push"
    assert entry.data["alarm_subscribe_code"] == "code-alarm"
    assert entry.entry_id not in _subscribed_url(client.subscribe_real_time_data)
    assert _deleted_cloudhooks(cloud) == set(old_ids.values())
    assert "legacy_webhooks" not in entry.data


@pytest.mark.asyncio
async def test_old_webhook_ids_keep_working_until_resubscribed(
    hass: HomeAssistant, client, cloud, hass_client_no_auth
):
    """While HYXI keeps the subscriptions made with the old webhook IDs,
    pushes to those IDs are still handled and their cloudhooks are kept.
    Once a later setup replaces the subscriptions, they are retired."""
    entry, old_ids = _upgraded_entry(hass, client)
    _refuse_subscriptions(client)

    await _setup(hass, entry)

    assert entry.data["push_subscribe_code"] == "old-push"
    assert not _deleted_cloudhooks(cloud)
    http = await hass_client_no_auth()
    response = await http.post(
        f"/api/webhook/{old_ids['push']}",
        json={"dataList": []},
        headers={"accessKey": "ak"},
    )
    assert response.status == 200
    client.process_push_data.assert_called_once()

    client.cancel_subscription.return_value = {"success": True}
    accept_subscriptions(client)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _deleted_cloudhooks(cloud) == set(old_ids.values())
    assert "legacy_webhooks" not in entry.data


@pytest.mark.asyncio
async def test_each_old_webhook_id_is_retired_on_its_own(
    hass: HomeAssistant, client, cloud
):
    """When only the alarm subscription could be replaced, only the old
    alarm webhook ID is retired; the old push one stays in use."""
    entry, old_ids = _upgraded_entry(hass, client)
    client.cancel_subscription.side_effect = lambda code: {
        "success": code != "old-push",
        "msg": "busy",
    }
    client.subscribe_real_time_data.return_value = {
        "success": False,
        "msg": "repeatedly",
    }

    await _setup(hass, entry)

    assert entry.data["push_subscribe_code"] == "old-push"
    assert _deleted_cloudhooks(cloud) == {old_ids["alarm"]}
    assert entry.data["legacy_webhooks"] == {"webhook_id": "old-push"}


@pytest.mark.parametrize(
    "delete_error",
    [None, ValueError("Hook is not enabled for the cloud.")],
    ids=["cloudhook-deleted", "no-cloudhook"],
)
@pytest.mark.asyncio
async def test_old_webhook_ids_without_subscriptions_are_retired(
    hass: HomeAssistant, client, cloud, delete_error
):
    """An upgraded entry without subscriptions, for example with push
    turned off, retires the old webhook IDs at setup, whether or not they
    had a cloudhook."""
    entry, old_ids = _upgraded_entry(hass, client, subscribed=False)
    cloud.async_delete_cloudhook.side_effect = delete_error
    hass.config_entries.async_update_entry(entry, options={CONF_ENABLE_PUSH: False})

    await _setup(hass, entry)

    assert entry.data["webhook_id"]
    assert _deleted_cloudhooks(cloud) == set(old_ids.values())
    assert "legacy_webhooks" not in entry.data


@pytest.mark.asyncio
async def test_an_undeletable_old_cloudhook_is_retried(
    hass: HomeAssistant, client, cloud
):
    """An old cloudhook that can't be deleted (Home Assistant Cloud not
    reachable) keeps its webhook ID until a later setup deletes it."""
    entry, _old_ids = _upgraded_entry(hass, client, subscribed=False)
    cloud.async_delete_cloudhook.side_effect = ConnectionError("cloud offline")

    await _setup(hass, entry)

    assert set(entry.data["legacy_webhooks"]) == {"webhook_id", "alarm_webhook_id"}

    cloud.async_delete_cloudhook.side_effect = None
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert "legacy_webhooks" not in entry.data


@pytest.mark.asyncio
async def test_removing_the_entry_deletes_its_cloudhooks(
    hass: HomeAssistant, client, cloud
):
    """Removing the entry deletes the cloudhooks of its webhook IDs,
    including old ones still in use."""
    entry, old_ids = _upgraded_entry(hass, client)
    _refuse_subscriptions(client)
    await _setup(hass, entry)
    ids = {entry.data["webhook_id"], entry.data["alarm_webhook_id"]}

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _deleted_cloudhooks(cloud) == ids | set(old_ids.values())

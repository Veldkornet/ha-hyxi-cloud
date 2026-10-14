"""Integration tests for clearing leftover push subscriptions when HYXI's
subscription list couldn't be fetched at startup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from hyxi_cloud_api import Subscription, SubscriptionType

from custom_components.hyxi_cloud.const import CONF_ENABLE_PUSH, CONF_PUSH_URL, DOMAIN
from tests.integration.entries import accept_subscriptions, cloud_entry, serve_devices

_DEVICES = {
    "SN_A": {
        "device_name": "Inverter",
        "device_type_code": "HYBRID_INVERTER",
        "metrics": {"batSoc": "50"},
    }
}
_LEFTOVERS = [
    Subscription("old-push", SubscriptionType.REAL_TIME_DATA, "", "", ("SN_A",)),
    Subscription("old-alarm", SubscriptionType.ALARM, "", "", ()),
]


@pytest.fixture
def client():
    """The mocked HyxiApiClient the entry gets, accepting subscriptions."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        serve_devices(mock_client, _DEVICES)
        accept_subscriptions(mock_client)
        mock_client.cancel_subscription.return_value = {"success": True}
        client_class.return_value = mock_client
        yield mock_client


def _list_after_failures(failures: int):
    """list_subscriptions failing `failures` times, then listing the
    leftover subscriptions."""
    calls = 0

    async def list_subscriptions():
        nonlocal calls
        calls += 1
        if calls <= failures:
            raise TimeoutError("unreachable")
        return list(_LEFTOVERS)

    return list_subscriptions


async def _setup(hass: HomeAssistant):
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PUSH: True, CONF_PUSH_URL: "https://hyxi.example"}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _cancelled(client) -> set[str]:
    return {c.args[0] for c in client.cancel_subscription.await_args_list}


@pytest.mark.asyncio
async def test_leftovers_are_cleared_once_the_list_can_be_fetched(
    hass: HomeAssistant, client
):
    """HYXI's list couldn't be fetched at startup; it's fetched again before
    subscribing, so leftover real-time and alarm subscriptions are
    cancelled and the new ones made."""
    client.list_subscriptions.side_effect = _list_after_failures(1)

    entry = await _setup(hass)

    assert _cancelled(client) >= {"old-push", "old-alarm"}
    client.subscribe_real_time_data.assert_awaited_once()
    client.subscribe_alarm.assert_awaited_once()
    assert entry.data["push_subscribe_code"] == "code-push"
    assert hass.data[DOMAIN][entry.entry_id].push_status == "active"


@pytest.mark.asyncio
async def test_nothing_is_cancelled_while_the_list_stays_unknown(
    hass: HomeAssistant, client
):
    """If HYXI's list still can't be fetched, nothing is cancelled blind."""
    client.list_subscriptions.side_effect = _list_after_failures(10)

    await _setup(hass)

    client.cancel_subscription.assert_not_awaited()
    client.subscribe_real_time_data.assert_awaited_once()

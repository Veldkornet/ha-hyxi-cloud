"""Integration tests for the cloud entry's separate device discovery: it
runs before the first poll and then hourly, and reloads the entry when the
discovered devices change."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from hyxi_cloud_api import DiscoveryResult
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hyxi_cloud.const import CONF_BACK_DISCOVERY, DOMAIN
from custom_components.hyxi_cloud.coordinator import (
    DISCOVERY_INTERVAL,
    device_store_key,
)
from tests.integration.entries import cloud_entry, serve_devices


def _inverter(sn: str) -> dict:
    return {
        "device_name": f"Inverter {sn}",
        "device_type_code": "HYBRID_INVERTER",
        "metrics": {"batSoc": "50", "deviceSn": sn},
    }


def _devices(*sns: str) -> dict:
    return {sn: _inverter(sn) for sn in sns}


@pytest.fixture
def client():
    """The mocked HyxiApiClient every setup of the entry gets."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        # Synchronous on the real client; the coordinator calls it when it
        # merges a poll into existing data.
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        client_class.return_value = mock_client
        yield mock_client


async def _setup(hass: HomeAssistant, entry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _registered(hass: HomeAssistant, entry, sn: str) -> bool:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, sn), entry.entry_id
    )
    return device is not None


@pytest.mark.asyncio
async def test_devices_are_discovered_before_the_first_poll(
    hass: HomeAssistant, client
):
    """Setup discovers the devices, honouring the back-discovery option,
    and only then polls them."""
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_BACK_DISCOVERY: True})
    serve_devices(client, _devices("SN_A"))

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert _registered(hass, entry, "SN_A")
    calls = [name for name, *_ in client.mock_calls]
    assert calls.index("discover_devices") < calls.index("poll_devices")
    client.discover_devices.assert_awaited_once_with(allow_back_discovery=True)


@pytest.mark.asyncio
async def test_discovery_runs_again_hourly(hass: HomeAssistant, client):
    """Discovery is scheduled on its own hourly interval after setup."""
    entry = cloud_entry(hass)
    serve_devices(client, _devices("SN_A"))
    await _setup(hass, entry)

    async_fire_time_changed(
        hass, dt_util.utcnow() + DISCOVERY_INTERVAL + timedelta(seconds=1)
    )
    await hass.async_block_till_done()

    assert client.discover_devices.await_count == 2


@pytest.mark.asyncio
async def test_a_discovered_device_reloads_the_entry(hass: HomeAssistant, client):
    """A device that a later discovery finds gets its entities through a
    reload of the entry."""
    entry = cloud_entry(hass)
    serve_devices(client, _devices("SN_A"))
    await _setup(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    serve_devices(client, _devices("SN_A", "SN_B"))
    await coordinator.discovery.async_refresh()
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN][entry.entry_id] is not coordinator
    assert _registered(hass, entry, "SN_B")


@pytest.mark.parametrize(
    "rediscovered",
    [_devices("SN_A", "SN_B"), _devices("SN_A"), {}],
    ids=["unchanged", "device-removed", "nothing-discovered"],
)
@pytest.mark.asyncio
async def test_a_discovery_without_new_devices_keeps_the_entry(
    hass: HomeAssistant, client, rediscovered
):
    """Only new devices need a reload: a device that disappears drops out of
    the polled data on its own, and finding none at all is an account
    problem the poll reports."""
    entry = cloud_entry(hass)
    serve_devices(client, _devices("SN_A", "SN_B"))
    await _setup(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    serve_devices(client, rediscovered)
    await coordinator.discovery.async_refresh()
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id] is coordinator


@pytest.mark.asyncio
async def test_failed_startup_discovery_uses_cached_devices(
    hass: HomeAssistant, hass_storage, client
):
    """If discovery fails at startup, cached devices stand in for them and
    the entry still loads; nothing is polled until discovery succeeds."""
    entry = cloud_entry(hass)
    key = device_store_key(entry.entry_id)
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {
            "cached_at": dt_util.utcnow().isoformat(),
            "devices": _devices("SN_A"),
        },
    }
    client.discover_devices.side_effect = TimeoutError("unreachable")

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert _registered(hass, entry, "SN_A")
    client.poll_devices.assert_not_awaited()

    # Once discovery succeeds, polling starts without waiting for its
    # interval, and the entry is not reloaded for the same devices.
    coordinator = hass.data[DOMAIN][entry.entry_id]
    client.discover_devices.side_effect = None
    serve_devices(client, _devices("SN_A"))
    await coordinator.discovery.async_refresh()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id] is coordinator
    client.poll_devices.assert_awaited()
    assert not coordinator.hyxi_metadata["cache_active"]


@pytest.mark.asyncio
async def test_failed_startup_discovery_without_a_cache_retries_setup(
    hass: HomeAssistant, client
):
    """Without cached devices, a failed startup discovery retries setup."""
    entry = cloud_entry(hass)
    client.discover_devices.side_effect = TimeoutError("unreachable")

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "unreachable" in entry.reason


@pytest.mark.asyncio
async def test_a_startup_discovery_that_lists_nothing_retries_setup(
    hass: HomeAssistant, client
):
    """A discovery that could not list any device (for example a rejected
    plant list) retries setup rather than loading with no devices."""
    entry = cloud_entry(hass)
    client.discover_devices.return_value = DiscoveryResult(devices={}, complete=False)

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    client.poll_devices.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_startup_discovery_still_sets_up(hass: HomeAssistant, client):
    """An incomplete discovery is a success with the devices it kept, so
    setup goes ahead with them."""
    entry = cloud_entry(hass)
    serve_devices(client, _devices("SN_A"))
    client.discover_devices.return_value = DiscoveryResult(
        devices={"SN_A": {}}, complete=False
    )

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert _registered(hass, entry, "SN_A")


@pytest.mark.asyncio
async def test_a_failed_rediscovery_keeps_the_entry(hass: HomeAssistant, client):
    """A rediscovery that fails reports no devices, so it does not reload
    the entry."""
    entry = cloud_entry(hass)
    serve_devices(client, _devices("SN_A"))
    await _setup(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    client.discover_devices.side_effect = TimeoutError("unreachable")
    await coordinator.discovery.async_refresh()
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id] is coordinator

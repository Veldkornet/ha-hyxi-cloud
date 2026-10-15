"""Integration tests for the cloud entry's separate device discovery: it
runs before the first poll and then hourly, and a device it finds gets its
device and entities once a poll returns it, without reloading the entry."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from hyxi_cloud_api import DiscoveryResult
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hyxi_cloud.const import (
    CONF_BACK_DISCOVERY,
    CONF_ENABLE_PUSH,
    CONF_PUSH_URL,
    DOMAIN,
    entry_stable_key,
)
from custom_components.hyxi_cloud.coordinator import (
    DISCOVERY_INTERVAL,
)
from tests.integration.entries import (
    accept_subscriptions,
    cloud_entry,
    lookup_entity_id,
    seed_cache,
    serve_devices,
)

# Past the coordinator's request-refresh debounce, so a requested poll runs.
PAST_REFRESH_DEBOUNCE = timedelta(seconds=11)


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


def _three_phase(sn: str) -> dict:
    """A hybrid inverter whose model identifies it as three-phase."""
    return {**_inverter(sn), "model": "HYX-H10K-HT"}


def _micro(sn: str) -> dict:
    return {
        "device_name": f"Micro {sn}",
        "device_type_code": "MICRO_INVERTER",
        "metrics": {"acP": "120", "deviceSn": sn},
    }


async def _discover(hass: HomeAssistant, client, coordinator, devices: dict) -> None:
    """Discover `devices` and let the poll that asks for run."""
    serve_devices(client, devices)
    await coordinator.discovery.async_refresh()
    async_fire_time_changed(hass, dt_util.utcnow() + PAST_REFRESH_DEBOUNCE)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_a_discovered_device_is_added_without_a_reload(
    hass: HomeAssistant, client
):
    """A device that a later discovery finds gets its device and entities
    once a poll returns it, without reloading the entry."""
    entry = cloud_entry(hass)
    serve_devices(client, _devices("SN_A"))
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    assert lookup_entity_id(hass, "sensor", "SN_B", "batSoc") is None

    await _discover(hass, client, coordinator, _devices("SN_A", "SN_B"))

    assert entry.runtime_data is coordinator
    assert _registered(hass, entry, "SN_B")
    entity_id = lookup_entity_id(hass, "sensor", "SN_B", "batSoc")
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "50"
    assert lookup_entity_id(hass, "button", "SN_B", "clear_alarms") is not None


@pytest.mark.asyncio
async def test_a_new_device_is_linked_to_its_parent(hass: HomeAssistant, client):
    """A new device reporting a parent already on the entry is linked to it,
    as at setup."""
    entry = cloud_entry(hass)
    serve_devices(client, _devices("SN_A"))
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    child = _micro("M1")
    child["metrics"]["parentSn"] = "SN_A"

    await _discover(hass, client, coordinator, {**_devices("SN_A"), "M1": child})

    registry = dr.async_get(hass)
    parent = registry.async_get_device_by_identifier((DOMAIN, "SN_A"), entry.entry_id)
    device = registry.async_get_device_by_identifier((DOMAIN, "M1"), entry.entry_id)
    assert device.via_device_id == parent.id


@pytest.mark.asyncio
@pytest.mark.usefixtures("cloud_battery_control")
async def test_a_new_controllable_device_gets_battery_protection(
    hass: HomeAssistant, client
):
    """With battery control on, a new controllable device gets battery
    protection without a reload; the existing device keeps its controller."""
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={"enable_battery_control": True}
    )
    serve_devices(client, {"SN_A": _three_phase("SN_A")})
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    first = coordinator.protection_controllers["SN_A"]

    await _discover(
        hass,
        client,
        coordinator,
        {"SN_A": _three_phase("SN_A"), "SN_B": _three_phase("SN_B")},
    )

    assert entry.runtime_data is coordinator
    assert coordinator.protection_controllers["SN_A"] is first
    assert "SN_B" in coordinator.protection_controllers
    assert lookup_entity_id(hass, "sensor", "SN_B", "last_sent_mode") is not None


@pytest.mark.asyncio
@pytest.mark.usefixtures("cloud_battery_control")
async def test_the_energy_managers_inverter_turning_up_reloads_the_entry(
    hass: HomeAssistant, client
):
    """The Energy Manager starts with the entry, so its inverter turning up
    only now reloads the entry; other new devices don't."""
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            "enable_battery_control": True,
            "em_enabled": True,
            "em_inverter_sn": "SN_C",
        },
    )
    serve_devices(client, _devices("SN_A"))
    await _setup(hass, entry)
    coordinator = entry.runtime_data

    await _discover(hass, client, coordinator, _devices("SN_A", "SN_B"))
    assert entry.runtime_data is coordinator

    await _discover(hass, client, coordinator, _devices("SN_A", "SN_B", "SN_C"))
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not coordinator
    assert _registered(hass, entry, "SN_C")


@pytest.mark.asyncio
async def test_the_first_microinverter_adds_the_totals(hass: HomeAssistant, client):
    """The totals summed across microinverters appear with the first
    microinverter, and only once."""
    entry = cloud_entry(hass)
    serve_devices(client, _devices("SN_A"))
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    total_id = f"{entry_stable_key(entry)}_micro_ac_power_total"
    assert registry.async_get_entity_id("sensor", DOMAIN, total_id) is None

    await _discover(hass, client, coordinator, {**_devices("SN_A"), "M1": _micro("M1")})
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, total_id)
    assert entity_id is not None
    await _discover(
        hass,
        client,
        coordinator,
        {**_devices("SN_A"), "M1": _micro("M1"), "M2": _micro("M2")},
    )

    assert registry.async_get_entity_id("sensor", DOMAIN, total_id) == entity_id
    assert float(hass.states.get(entity_id).state) == 240


@pytest.mark.asyncio
async def test_a_new_device_renews_the_push_subscription(hass: HomeAssistant, client):
    """With push on, a new device renews the push subscription so it covers
    the new device too."""
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={CONF_ENABLE_PUSH: True, CONF_PUSH_URL: "https://hyxi.example"},
    )
    serve_devices(client, _devices("SN_A"))
    accept_subscriptions(client)
    client.list_subscriptions.return_value = []
    client.cancel_subscription.return_value = {"success": True}
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    assert client.subscribe_real_time_data.await_args.args[1] == ["SN_A"]

    await _discover(hass, client, coordinator, _devices("SN_A", "SN_B"))

    assert sorted(client.subscribe_real_time_data.await_args.args[1]) == [
        "SN_A",
        "SN_B",
    ]
    assert coordinator.push_status == "active"


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
    coordinator = entry.runtime_data

    serve_devices(client, rediscovered)
    await coordinator.discovery.async_refresh()
    await hass.async_block_till_done()

    assert entry.runtime_data is coordinator


@pytest.mark.asyncio
async def test_failed_startup_discovery_uses_cached_devices(
    hass: HomeAssistant, hass_storage, client
):
    """If discovery fails at startup, cached devices stand in for them and
    the entry still loads; nothing is polled until discovery succeeds."""
    entry = cloud_entry(hass)
    seed_cache(hass_storage, entry, _devices("SN_A"))
    client.discover_devices.side_effect = TimeoutError("unreachable")

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert _registered(hass, entry, "SN_A")
    client.poll_devices.assert_not_awaited()

    # Once discovery succeeds, polling starts without waiting for its
    # interval, and the entry is not reloaded for the same devices.
    coordinator = entry.runtime_data
    client.discover_devices.side_effect = None
    serve_devices(client, _devices("SN_A"))
    await coordinator.discovery.async_refresh()
    async_fire_time_changed(hass, dt_util.utcnow() + PAST_REFRESH_DEBOUNCE)
    await hass.async_block_till_done()

    assert entry.runtime_data is coordinator
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
    coordinator = entry.runtime_data

    client.discover_devices.side_effect = TimeoutError("unreachable")
    await coordinator.discovery.async_refresh()
    await hass.async_block_till_done()

    assert entry.runtime_data is coordinator


@pytest.mark.asyncio
async def test_a_device_whose_first_poll_failed_is_added_once_polled(
    hass: HomeAssistant, hass_storage, client
):
    """A device discovered at startup whose first poll failed (so the entry
    loaded from a cache without it) gets its device and entities once a poll
    returns it. Rediscovering it asks for that poll; cached data standing in
    while polling keeps failing doesn't add it."""
    entry = cloud_entry(hass)
    seed_cache(hass_storage, entry, _devices("SN_A"))
    serve_devices(client, _devices("SN_A", "SN_B"))
    client.poll_devices.side_effect = TimeoutError("unreachable")
    with patch("custom_components.hyxi_cloud.coordinator.POLL_RETRY_DELAY", 0):
        await _setup(hass, entry)
        coordinator = entry.runtime_data
        assert not _registered(hass, entry, "SN_B")

        polls = client.poll_devices.await_count
        await coordinator.discovery.async_refresh()
        await hass.async_block_till_done()
        assert client.poll_devices.await_count > polls
        assert not _registered(hass, entry, "SN_B")

        client.poll_devices.side_effect = None
        await _discover(hass, client, coordinator, _devices("SN_A", "SN_B"))

    assert entry.runtime_data is coordinator
    assert _registered(hass, entry, "SN_B")
    assert lookup_entity_id(hass, "sensor", "SN_B", "batSoc") is not None

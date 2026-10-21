"""Integration tests for the Modbus entries' own unique_id prefix: a device
set up over both the cloud and Modbus gets each entry's entities, and an
existing Modbus entry's entities move to the prefix without losing their
entity_ids."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.hyxi_cloud.const import CONFIG_VERSION, DOMAIN
from tests.integration.entries import cloud_entry, lookup_entity_id, serve_devices
from tests.integration.test_modbus import _modbus_entry, _seeded_connection

# The serial the seeded HALO connection identifies as.
SN = "10201234567810"
CLOUD_HALO = {
    "device_name": "HALO",
    "device_type_code": "MICRO_STORAGE_ALL_IN_ONE",
    "metrics": {"batSoc": "80"},
}


@pytest.fixture
def cloud_client():
    """The mocked HyxiApiClient a cloud entry gets."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        mock_client.list_subscriptions.return_value = []
        client_class.return_value = mock_client
        yield mock_client


async def _setup(hass: HomeAssistant, entry) -> None:
    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_a_device_on_a_cloud_and_a_modbus_entry_gets_both_entries_entities(
    hass: HomeAssistant, cloud_client
):
    """The same HALO set up over the cloud and over Modbus gets a battery
    SOC sensor from each entry, each showing its own entry's reading."""
    cloud = cloud_entry(hass)
    serve_devices(cloud_client, {SN: CLOUD_HALO})
    modbus = _modbus_entry(hass, modbus_family="halo")
    # Setting up one entry sets up the integration's others with it.
    await _setup(hass, cloud)
    assert modbus.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    cloud_soc = lookup_entity_id(hass, cloud, "sensor", SN, "batSoc")
    modbus_soc = lookup_entity_id(hass, modbus, "sensor", SN, "batSoc")
    assert cloud_soc is not None
    assert modbus_soc is not None
    assert cloud_soc != modbus_soc
    assert hass.states.get(cloud_soc).state == "80"
    assert hass.states.get(modbus_soc).state == "78"
    assert registry.async_get(cloud_soc).config_entry_id == cloud.entry_id
    assert registry.async_get(modbus_soc).config_entry_id == modbus.entry_id


@pytest.mark.asyncio
async def test_a_modbus_entry_moves_its_entities_to_the_modbus_prefix(
    hass: HomeAssistant,
):
    """An entry from before the prefix keeps each device entity's entity_id
    under the new unique_id, leaves its entry-scoped ones alone, and is
    marked as migrated."""
    entry = _modbus_entry(hass, modbus_family="halo")
    assert entry.version < CONFIG_VERSION
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, SN)}
    )
    registry = er.async_get(hass)
    soc = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{SN}_batSoc",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="halo_battery_soc",
    ).entity_id
    connection = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_modbus_connection_type",
        config_entry=entry,
    ).entity_id

    await _setup(hass, entry)

    assert registry.async_get(soc).unique_id == f"hyxi_modbus_{SN}_batSoc"
    assert hass.states.get(soc).state == "78"
    assert (
        registry.async_get(connection).unique_id
        == f"{entry.entry_id}_modbus_connection_type"
    )
    assert entry.version == CONFIG_VERSION


@pytest.mark.asyncio
async def test_a_migrated_modbus_entry_is_not_moved_again(hass: HomeAssistant):
    """An entry already on the prefix keeps its unique_ids as they are,
    even one whose serial itself starts with "modbus_"."""
    entry = _modbus_entry(hass, modbus_family="halo")
    hass.config_entries.async_update_entry(entry, version=CONFIG_VERSION)
    registry = er.async_get(hass)
    fallback = registry.async_get_or_create(
        "sensor", DOMAIN, "hyxi_modbus_1_batSoc", config_entry=entry
    ).entity_id

    await _setup(hass, entry)

    assert registry.async_get(fallback).unique_id == "hyxi_modbus_1_batSoc"


@pytest.mark.asyncio
async def test_a_cloud_entry_keeps_its_unique_ids(hass: HomeAssistant, cloud_client):
    """A cloud entry's entities keep the plain prefix through the
    migration."""
    entry = cloud_entry(hass)
    serve_devices(cloud_client, {SN: CLOUD_HALO})
    registry = er.async_get(hass)
    soc = registry.async_get_or_create(
        "sensor", DOMAIN, f"hyxi_{SN}_batSoc", config_entry=entry
    ).entity_id

    await _setup(hass, entry)

    assert registry.async_get(soc).unique_id == f"hyxi_{SN}_batSoc"
    assert entry.version == CONFIG_VERSION


@pytest.mark.asyncio
async def test_a_shared_row_goes_back_to_the_cloud_entry(
    hass: HomeAssistant, cloud_client
):
    """A row both entries registered, which the Modbus entry ended up
    owning, goes to the cloud entry with its entity_id; the Modbus entry
    creates its own SOC sensor."""
    cloud = cloud_entry(hass)
    serve_devices(cloud_client, {SN: CLOUD_HALO})
    modbus = _modbus_entry(hass, modbus_family="halo")
    devices = dr.async_get(hass)
    cloud_device = devices.async_get_or_create(
        config_entry_id=cloud.entry_id, identifiers={(DOMAIN, SN)}
    )
    modbus_device = devices.async_get_or_create(
        config_entry_id=modbus.entry_id, identifiers={(DOMAIN, SN)}
    )
    registry = er.async_get(hass)
    shared = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{SN}_batSoc",
        config_entry=modbus,
        device_id=modbus_device.id,
        suggested_object_id="halo_battery_soc",
    ).entity_id

    await _setup(hass, cloud)

    row = registry.async_get(shared)
    assert row.unique_id == f"hyxi_{SN}_batSoc"
    assert row.config_entry_id == cloud.entry_id
    assert row.device_id == cloud_device.id
    assert hass.states.get(shared).state == "80"
    modbus_soc = lookup_entity_id(hass, modbus, "sensor", SN, "batSoc")
    assert hass.states.get(modbus_soc).state == "78"


@pytest.mark.asyncio
async def test_a_row_already_on_the_modbus_prefix_gives_way(hass: HomeAssistant):
    """When the Modbus unique_id is already taken, the entry still loads:
    the older row takes the id and keeps its entity_id."""
    entry = _modbus_entry(hass, modbus_family="halo")
    registry = er.async_get(hass)
    older = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{SN}_batSoc",
        config_entry=entry,
        suggested_object_id="halo_battery_soc",
    ).entity_id
    newer = registry.async_get_or_create(
        "sensor", DOMAIN, f"hyxi_modbus_{SN}_batSoc", config_entry=entry
    ).entity_id

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert registry.async_get(older).unique_id == f"hyxi_modbus_{SN}_batSoc"
    assert registry.async_get(newer) is None

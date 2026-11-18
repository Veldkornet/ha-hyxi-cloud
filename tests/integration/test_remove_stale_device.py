"""Integration tests for deleting a device the entry no longer reports."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from custom_components.hyxi_cloud import async_remove_config_entry_device
from custom_components.hyxi_cloud.const import (
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    DOMAIN,
    entry_stable_key,
)
from tests.integration.test_modbus import _modbus_entry, _seeded_connection

SN = "10201234567810"


async def _loaded_entry(hass: HomeAssistant, **options):
    entry = _modbus_entry(hass, modbus_family="halo", options=options)
    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


def _device(hass: HomeAssistant, entry, identifier: str) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, identifier)}
    )


@pytest.mark.asyncio
async def test_a_device_the_entry_no_longer_reports_can_be_deleted(
    hass: HomeAssistant,
):
    entry = await _loaded_entry(hass)

    stale = _device(hass, entry, "modbus_1")

    assert await async_remove_config_entry_device(hass, entry, stale)


@pytest.mark.asyncio
async def test_the_reported_inverter_and_the_service_device_stay_protected(
    hass: HomeAssistant,
):
    entry = await _loaded_entry(hass)
    registry = dr.async_get(hass)

    inverter = registry.async_get_device_by_identifier((DOMAIN, SN), entry.entry_id)
    service = _device(hass, entry, entry.entry_id)

    assert not await async_remove_config_entry_device(hass, entry, inverter)
    assert not await async_remove_config_entry_device(hass, entry, service)


@pytest.mark.asyncio
async def test_the_enabled_energy_manager_stays_protected(hass: HomeAssistant):
    entry = await _loaded_entry(
        hass, **{CONF_EM_ENABLED: True, CONF_EM_INVERTER_SN: SN}
    )

    energy_manager = _device(hass, entry, f"{SN}_energy_manager")

    assert not await async_remove_config_entry_device(hass, entry, energy_manager)


@pytest.mark.asyncio
async def test_a_disabled_energy_managers_device_can_be_deleted(hass: HomeAssistant):
    """Only the configured, enabled Energy Manager keeps its device, not one
    per reported serial."""
    entry = await _loaded_entry(hass)

    assert await async_remove_config_entry_device(
        hass, entry, _device(hass, entry, f"{SN}_energy_manager")
    )


@pytest.mark.asyncio
async def test_the_microinverter_summary_is_protected_only_with_microinverters(
    hass: HomeAssistant,
):
    entry = await _loaded_entry(hass)
    summary = _device(hass, entry, f"{entry_stable_key(entry)}_microinverters_summary")

    assert await async_remove_config_entry_device(hass, entry, summary)

    entry.runtime_data.data["MICRO1"] = {
        "device_type_code": "MICRO_INVERTER",
        "metrics": {},
    }
    assert not await async_remove_config_entry_device(hass, entry, summary)


@pytest.mark.asyncio
async def test_the_energy_managers_inverter_stays_protected_when_not_reported(
    hass: HomeAssistant,
):
    entry = await _loaded_entry(
        hass, **{CONF_EM_ENABLED: True, CONF_EM_INVERTER_SN: "EM_INVERTER"}
    )

    em_inverter = _device(hass, entry, "EM_INVERTER")

    assert not await async_remove_config_entry_device(hass, entry, em_inverter)


@pytest.mark.asyncio
async def test_a_batterys_device_stays_protected(hass: HomeAssistant):
    entry = await _loaded_entry(hass)
    coordinator = entry.runtime_data
    coordinator.data[SN]["metrics"]["batSn"] = "BAT0001"
    coordinator.data[SN]["metrics"]["parentSn"] = "COLLECTOR1"

    battery = _device(hass, entry, "BAT0001")
    collector = _device(hass, entry, "COLLECTOR1")

    assert not await async_remove_config_entry_device(hass, entry, battery)
    assert not await async_remove_config_entry_device(hass, entry, collector)


@pytest.mark.asyncio
async def test_a_device_with_a_live_serial_prefix_is_still_deletable(
    hass: HomeAssistant,
):
    """Only the identifiers the entry builds are protected, not anything that
    merely starts with a live serial."""
    entry = await _loaded_entry(hass)

    assert await async_remove_config_entry_device(
        hass, entry, _device(hass, entry, f"{SN}_old_battery")
    )


@pytest.mark.asyncio
async def test_a_device_without_metrics_does_not_break_the_check(hass: HomeAssistant):
    entry = await _loaded_entry(hass)
    entry.runtime_data.data[SN]["metrics"] = None

    assert await async_remove_config_entry_device(
        hass, entry, _device(hass, entry, "modbus_1")
    )


@pytest.mark.asyncio
async def test_an_entry_that_is_not_loaded_deletes_nothing(hass: HomeAssistant):
    entry = _modbus_entry(hass, modbus_family="halo")

    assert not await async_remove_config_entry_device(
        hass, entry, _device(hass, entry, "modbus_1")
    )


@pytest.mark.asyncio
async def test_home_assistant_deletes_a_stale_device_but_not_a_live_one(
    hass: HomeAssistant, hass_ws_client
):
    """Through HA's own remove-device command, which only offers a device the
    integration's hook allows."""
    assert await async_setup_component(hass, "config", {})
    entry = await _loaded_entry(hass)
    registry = dr.async_get(hass)
    stale = _device(hass, entry, "modbus_1")
    live = registry.async_get_device_by_identifier((DOMAIN, SN), entry.entry_id)
    client = await hass_ws_client(hass)

    async def remove(device_id: str) -> dict:
        await client.send_json_auto_id(
            {
                "type": "config/device_registry/remove_config_entry",
                "config_entry_id": entry.entry_id,
                "device_id": device_id,
            }
        )
        return await client.receive_json()

    refused = await remove(live.id)
    allowed = await remove(stale.id)

    assert not refused["success"]
    assert registry.async_get(live.id) is not None
    assert allowed["success"]
    assert registry.async_get(stale.id) is None


@pytest.mark.asyncio
async def test_no_device_is_deletable_before_the_entry_has_data(hass: HomeAssistant):
    entry = await _loaded_entry(hass)
    entry.runtime_data.data = None

    assert not await async_remove_config_entry_device(
        hass, entry, _device(hass, entry, "modbus_1")
    )

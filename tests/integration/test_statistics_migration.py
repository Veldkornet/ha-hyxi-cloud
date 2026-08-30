"""Integration tests for the unique_id migrations that keep long-term
statistics attached across restarts and remove-and-re-add.

Battery sensors used to be keyed off the battery serial (runtime telemetry
that isn't always present when the entity is built) and the microinverter
aggregates off ``entry.entry_id`` (regenerated on a re-add). Both flipped
the unique_id and orphaned the old entity's statistics. sensor.py now keys
off stable identifiers; these tests exercise the real registry migration
that carries existing installs forward.
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyxi_cloud.const import (
    CONF_ACCESS_KEY,
    CONF_SECRET_KEY,
    DOMAIN,
)


def _cloud_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"},
        options={},
        unique_id="ak",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup_with_devices(hass: HomeAssistant, entry: MockConfigEntry, data: dict):
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client._refresh_token.return_value = True
        mock_client.get_all_device_data.return_value = {"data": data, "attempts": 1}
        mock_client_class.return_value = mock_client
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_battery_sensor_rekeyed_from_batsn_to_inverter_serial(
    hass: HomeAssistant,
):
    """A batSn-keyed battery sensor is moved onto the inverter serial, keeping
    its entity_id (so the recorder statistics stay attached)."""
    inv_sn, bat_sn = "INV_1000", "BAT_2000"
    entry = _cloud_entry(hass)

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, inv_sn)}, name="Inverter"
    )
    battery = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, bat_sn)},
        name=f"Battery {bat_sn}",
        via_device=(DOMAIN, inv_sn),
    )

    entity_registry = er.async_get(hass)
    legacy = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{bat_sn}_batSoc",
        config_entry=entry,
        device_id=battery.id,
        suggested_object_id=f"hyxi_{bat_sn}_batsoc",
    )

    await _setup_with_devices(
        hass,
        entry,
        {
            inv_sn: {
                "device_name": "Inverter",
                "model": "HYX-H10K-HT",
                "device_type_code": "HYBRID_INVERTER",
                "metrics": {"batSn": bat_sn, "batSoc": "50", "deviceSn": inv_sn},
            }
        },
    )

    moved = entity_registry.async_get(legacy.entity_id)
    assert moved is not None
    assert moved.unique_id == f"hyxi_{inv_sn}_batSoc"
    assert moved.entity_id == legacy.entity_id
    # The platform's freshly built entity resolved to the same registry row,
    # not a second _2 entity.
    assert (
        entity_registry.async_get_entity_id("sensor", DOMAIN, f"hyxi_{inv_sn}_batSoc")
        == legacy.entity_id
    )


@pytest.mark.asyncio
async def test_battery_sensor_rekeyed_when_current_poll_has_no_batsn(
    hass: HomeAssistant,
):
    """The migration still fires when telemetry carries no batSn this run --
    it resolves the inverter via the battery device's via_device link."""
    inv_sn, bat_sn = "INV_1100", "BAT_2100"
    entry = _cloud_entry(hass)

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, inv_sn)}, name="Inverter"
    )
    battery = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, bat_sn)},
        name=f"Battery {bat_sn}",
        via_device=(DOMAIN, inv_sn),
    )
    entity_registry = er.async_get(hass)
    legacy = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{bat_sn}_batSoh",
        config_entry=entry,
        device_id=battery.id,
        suggested_object_id=f"hyxi_{bat_sn}_batsoh",
    )

    await _setup_with_devices(
        hass,
        entry,
        {
            inv_sn: {
                "device_name": "Inverter",
                "model": "HYX-H10K-HT",
                "device_type_code": "HYBRID_INVERTER",
                "metrics": {"batSoh": "97", "deviceSn": inv_sn},  # no batSn
            }
        },
    )

    moved = entity_registry.async_get(legacy.entity_id)
    assert moved.unique_id == f"hyxi_{inv_sn}_batSoh"
    assert moved.entity_id == legacy.entity_id


@pytest.mark.asyncio
async def test_first_class_battery_device_sensors_are_left_alone(hass: HomeAssistant):
    """A battery that is its own device already keys its sensors off its own
    serial -- the migration must not touch those."""
    inv_sn, bat_sn = "INV_1200", "BAT_2200"
    entry = _cloud_entry(hass)

    entity_registry = er.async_get(hass)
    own = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{bat_sn}_batSoc",
        config_entry=entry,
        suggested_object_id=f"hyxi_{bat_sn}_batsoc",
    )

    await _setup_with_devices(
        hass,
        entry,
        {
            inv_sn: {
                "device_name": "Inverter",
                "device_type_code": "HYBRID_INVERTER",
                "metrics": {"batSn": bat_sn, "deviceSn": inv_sn},
            },
            bat_sn: {
                "device_name": "Battery",
                "device_type_code": "ENERGY_STORAGE_BATTERY",
                "metrics": {"batSoc": "60", "deviceSn": bat_sn},
            },
        },
    )

    assert entity_registry.async_get(own.entity_id).unique_id == f"hyxi_{bat_sn}_batSoc"


@pytest.mark.asyncio
async def test_microinverter_sum_rekeyed_off_entry_id(hass: HomeAssistant):
    """The microinverter aggregate entity and its device move from an
    entry_id-derived key to entry_stable_key()."""
    entry = _cloud_entry(hass)
    micro_sn = "MICRO_1"

    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_microinverters_summary")},
        name="Microinverters Summary",
    )
    entity_registry = er.async_get(hass)
    legacy = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_micro_ac_power_total",
        config_entry=entry,
        device_id=old_device.id,
        suggested_object_id="microinverters_summary_ac_power_total",
    )

    await _setup_with_devices(
        hass,
        entry,
        {
            micro_sn: {
                "device_name": "Micro",
                "device_type_code": "MICRO_INVERTER",
                "metrics": {"acP": "120.0", "efpv": "3.4", "deviceSn": micro_sn},
            }
        },
    )

    moved = entity_registry.async_get(legacy.entity_id)
    assert moved.unique_id == "ak_micro_ac_power_total"
    assert moved.entity_id == legacy.entity_id
    assert device_registry.async_get_device(
        identifiers={(DOMAIN, "ak_microinverters_summary")}
    ) == device_registry.async_get(old_device.id)
    assert (
        device_registry.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}_microinverters_summary")}
        )
        is None
    )

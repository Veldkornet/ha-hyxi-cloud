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
    entry_stable_key,
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
    inverter = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, inv_sn)}, name="Inverter"
    )
    battery = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, bat_sn)},
        name=f"Battery {bat_sn}",
        via_device_id=inverter.id,
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
    inverter = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, inv_sn)}, name="Inverter"
    )
    battery = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, bat_sn)},
        name=f"Battery {bat_sn}",
        via_device_id=inverter.id,
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
async def test_first_class_battery_device_and_inverter_batsoc_do_not_collide(
    hass: HomeAssistant,
):
    """When HYXI exposes the battery as its own device, both it and the
    inverter can carry a batSoc reading. The migration leaves the
    battery-device sensor on its own serial and the inverter's battery
    sensor keys off the inverter serial -- two distinct entities, no
    unique_id collision."""
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
                "metrics": {"batSn": bat_sn, "batSoc": "55", "deviceSn": inv_sn},
            },
            bat_sn: {
                "device_name": "Battery",
                "device_type_code": "ENERGY_STORAGE_BATTERY",
                "metrics": {"batSoc": "60", "deviceSn": bat_sn},
            },
        },
    )

    # First-class battery device: untouched, still on its own serial.
    assert entity_registry.async_get(own.entity_id).unique_id == f"hyxi_{bat_sn}_batSoc"
    # Inverter's battery sensor: a separate entity on the inverter serial.
    inv_batsoc = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"hyxi_{inv_sn}_batSoc"
    )
    assert inv_batsoc is not None
    assert inv_batsoc != own.entity_id
    assert hass.states.get(own.entity_id).state == "60"
    assert hass.states.get(inv_batsoc).state == "55"


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

    sk = entry_stable_key(entry)
    assert sk != "ak"  # raw access key must never land in an identifier
    moved = entity_registry.async_get(legacy.entity_id)
    assert moved.unique_id == f"{sk}_micro_ac_power_total"
    assert moved.entity_id == legacy.entity_id
    assert device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{sk}_microinverters_summary"), entry.entry_id
    ) == device_registry.async_get(old_device.id)
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{entry.entry_id}_microinverters_summary"), entry.entry_id
        )
        is None
    )


@pytest.mark.asyncio
async def test_microinverter_sum_rekeyed_when_stable_device_already_exists(
    hass: HomeAssistant,
):
    """A flip-flopped install can carry both the legacy entry_id-keyed
    summary device and the stable-keyed one. The aggregates move onto the
    stable device and keep their entity_ids -- the legacy device is removed
    without taking the just-re-keyed entities (and their statistics) down
    with it."""
    entry = _cloud_entry(hass)
    micro_sn = "MICRO_2"
    sk = entry_stable_key(entry)

    device_registry = dr.async_get(hass)
    legacy_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_microinverters_summary")},
        name="Microinverters Summary",
    )
    stable_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{sk}_microinverters_summary")},
        name="Microinverters Summary",
    )

    entity_registry = er.async_get(hass)
    legacy_entities = {
        key: entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{entry.entry_id}_{key}",
            config_entry=entry,
            device_id=legacy_device.id,
            suggested_object_id=f"microinverters_summary_{key}",
        )
        for key in ("micro_ac_power_total", "micro_daily_yield_total")
    }

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

    for key, legacy in legacy_entities.items():
        moved = entity_registry.async_get(legacy.entity_id)
        assert moved is not None, f"{key} entity was deleted by the migration"
        assert moved.entity_id == legacy.entity_id
        assert moved.unique_id == f"{sk}_{key}"
        assert moved.device_id == stable_device.id

    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{entry.entry_id}_microinverters_summary"), entry.entry_id
        )
        is None
    )
    surviving = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{sk}_microinverters_summary"), entry.entry_id
    )
    assert surviving is not None
    assert surviving.id == stable_device.id


@pytest.mark.asyncio
async def test_microinverter_sum_migration_noop_without_stable_key(hass: HomeAssistant):
    """An entry with no unique_id has no stable key to move to -- the
    entry_id-keyed aggregate is left exactly where it is."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"},
        options={},
    )
    entry.add_to_hass(hass)
    assert entry_stable_key(entry) == entry.entry_id

    entity_registry = er.async_get(hass)
    legacy = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_micro_ac_power_total",
        config_entry=entry,
        suggested_object_id="microinverters_summary_ac_power_total",
    )

    await _setup_with_devices(
        hass,
        entry,
        {
            "MICRO_3": {
                "device_name": "Micro",
                "device_type_code": "MICRO_INVERTER",
                "metrics": {"acP": "120.0", "efpv": "3.4", "deviceSn": "MICRO_3"},
            }
        },
    )

    kept = entity_registry.async_get(legacy.entity_id)
    assert kept is not None
    assert kept.unique_id == f"{entry.entry_id}_micro_ac_power_total"


@pytest.mark.asyncio
async def test_duplicate_energy_sensor_removed_when_canonical_exists(
    hass: HomeAssistant,
):
    """totalEchg/batCharge are dropped when bat_charge_total already exists,
    the row wired into the Energy dashboard."""
    inv_sn = "INV_DUP_1"
    entry = _cloud_entry(hass)

    registry = er.async_get(hass)
    canonical = registry.async_get_or_create(
        "sensor", DOMAIN, f"hyxi_{inv_sn}_bat_charge_total", config_entry=entry
    )
    dupe = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{inv_sn}_totalEchg",
        config_entry=entry,
        suggested_object_id=f"hyxi_{inv_sn}_totalechg",
    )

    await _setup_with_devices(
        hass,
        entry,
        {
            inv_sn: {
                "device_name": "Inverter",
                "device_type_code": "HYBRID_INVERTER",
                "metrics": {"deviceSn": inv_sn, "bat_charge_total": "1500.5"},
            }
        },
    )

    assert registry.async_get(dupe.entity_id) is None
    assert registry.async_get(canonical.entity_id) is not None


@pytest.mark.asyncio
async def test_duplicate_energy_sensor_promoted_when_canonical_missing(
    hass: HomeAssistant,
):
    """A lone batDisCharge row (e.g. bat_discharge_total was disabled and
    never built) is re-keyed, keeping its entity_id and history."""
    inv_sn = "INV_DUP_2"
    entry = _cloud_entry(hass)

    registry = er.async_get(hass)
    dupe = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{inv_sn}_batDisCharge",
        config_entry=entry,
        suggested_object_id=f"hyxi_{inv_sn}_batdischarge",
    )

    await _setup_with_devices(
        hass,
        entry,
        {
            inv_sn: {
                "device_name": "Inverter",
                "device_type_code": "HYBRID_INVERTER",
                "metrics": {"deviceSn": inv_sn, "bat_discharge_total": "1200.2"},
            }
        },
    )

    moved = registry.async_get(dupe.entity_id)
    assert moved is not None
    assert moved.unique_id == f"hyxi_{inv_sn}_bat_discharge_total"

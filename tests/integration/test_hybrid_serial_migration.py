"""Integration tests for the hybrid Modbus serial-decoding migration.

client_hybrid.py fixed two decoding bugs: the inverter's own serial
(register 1007, H64) was read as a plain decimal integer instead of the
hex-formatted value the document specifies, and the battery serial
(register 1015, S20 string) had its two per-register bytes read in the
wrong order. Both feed a Home Assistant device registry identifier
directly, and the inverter's also feeds every entity's unique_id -- fixing
the decoding alone would silently orphan every already-onboarded hybrid
Modbus install's devices and entity history. These tests exercise the
real device/entity registry migration that carries existing installs
forward, the same way test_statistics_migration.py does for the
batSn/entry_id rekeying migrations.
"""

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyxi_cloud.__init__ import (
    _async_register_devices,
    _migrate_energy_manager_inverter_sn,
    _migrate_hybrid_battery_serial,
    _migrate_hybrid_inverter_serial,
    _migrate_hybrid_serial_decoding,
    _reconstruct_pre_fix_battery_serial,
)
from custom_components.hyxi_cloud.const import (
    CONF_EM_INVERTER_SN,
    CONF_TRANSPORT,
    DOMAIN,
    TRANSPORT_MODBUS,
)

# The exact values used throughout this session's investigation: a real
# user's inverter/battery, and the client_hybrid.py fix's own worked
# example -- see docs/modbus-provenance.md and client_hybrid.py's
# _fix_battery_serial_byte_order docstring.
OLD_INVERTER_SN = "4538862186952720"  # str(int) of the raw register bits
NEW_INVERTER_SN = "10201234567810"  # _hex_identifier() of the same bits
OLD_BATTERY_SN_EVEN = "2143658709"  # byte-swapped "1234567890"
NEW_BATTERY_SN_EVEN = "1234567890"
NEW_BATTERY_SN_ODD = "BAT13571357"  # the odd-length, embedded-null case


def _modbus_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_TRANSPORT: TRANSPORT_MODBUS},
        options={},
        unique_id="modbus-serial-migration-test",
    )
    entry.add_to_hass(hass)
    return entry


def _hybrid_devices(sn: str, bat_sn: str | None) -> dict:
    metrics = {"batSn": bat_sn} if bat_sn else {}
    return {
        sn: {
            "device_name": "HYXI Hybrid Inverter",
            "device_type_code": "HYBRID_INVERTER",
            "metrics": metrics,
        }
    }


def test_reconstruct_pre_fix_battery_serial_matches_the_real_bug():
    """The reconstruction must match what the pre-fix code actually stored,
    not just be "a" plausible-looking scrambled string -- verified against
    both the even-length case (a clean pair swap) and the odd-length case
    (an embedded null the swap alone would miss)."""
    assert (
        _reconstruct_pre_fix_battery_serial(NEW_BATTERY_SN_EVEN) == OLD_BATTERY_SN_EVEN
    )
    assert _reconstruct_pre_fix_battery_serial(NEW_BATTERY_SN_ODD) == "AB1T531753\x007"
    assert _reconstruct_pre_fix_battery_serial("") is None


@pytest.mark.asyncio
async def test_inverter_device_and_entities_are_renamed_in_place(
    hass: HomeAssistant,
):
    """A hybrid inverter registered under the old decimal-misread serial
    is renamed to the corrected hex one, keeping the same device row and
    every entity's entity_id (only unique_id changes, so recorder
    statistics keyed on entity_id stay attached)."""
    entry = _modbus_entry(hass)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, OLD_INVERTER_SN)},
        name="Old Inverter",
        serial_number=OLD_INVERTER_SN,
    )

    entity_registry = er.async_get(hass)
    legacy = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{OLD_INVERTER_SN}_totalE",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id=f"hyxi_{OLD_INVERTER_SN}_totale",
    )

    _migrate_hybrid_serial_decoding(hass, entry, _hybrid_devices(NEW_INVERTER_SN, None))

    # Same device row, new identifier -- not a second, duplicate device.
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, OLD_INVERTER_SN), entry.entry_id
        )
        is None
    )
    migrated_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, NEW_INVERTER_SN), entry.entry_id
    )
    assert migrated_device is not None
    assert migrated_device.id == device.id
    assert migrated_device.serial_number == NEW_INVERTER_SN

    moved = entity_registry.async_get(legacy.entity_id)
    assert moved is not None
    assert moved.unique_id == f"hyxi_{NEW_INVERTER_SN}_totalE"
    assert moved.entity_id == legacy.entity_id


@pytest.mark.asyncio
async def test_inverter_keyed_entities_attached_to_the_battery_device_are_still_rekeyed(
    hass: HomeAssistant,
):
    """Regression test: HyxiBatteryEnergyPeriodSensor (and any other
    entity like it) keys its unique_id on the inverter's sn but attaches
    to the *battery's* device via device_info, not the inverter's own
    device_id -- e.g. sensor.hyxi_<inverter_sn>_bat_charge_month. A
    device_id-scoped entity scan misses these entirely; the migration
    must scan every entity on the config entry instead."""
    entry = _modbus_entry(hass)

    device_registry = dr.async_get(hass)
    inverter = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, OLD_INVERTER_SN)},
        name="Old Inverter",
    )
    battery = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, OLD_BATTERY_SN_EVEN)},
        name=f"Battery {OLD_BATTERY_SN_EVEN}",
        via_device_id=inverter.id,
    )

    entity_registry = er.async_get(hass)
    battery_period_sensor = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{OLD_INVERTER_SN}_bat_charge_month",
        config_entry=entry,
        device_id=battery.id,
        suggested_object_id=f"hyxi_{OLD_INVERTER_SN}_bat_charge_month",
    )

    _migrate_hybrid_serial_decoding(
        hass, entry, _hybrid_devices(NEW_INVERTER_SN, NEW_BATTERY_SN_EVEN)
    )

    moved = entity_registry.async_get(battery_period_sensor.entity_id)
    assert moved is not None
    assert moved.unique_id == f"hyxi_{NEW_INVERTER_SN}_bat_charge_month"
    assert moved.entity_id == battery_period_sensor.entity_id
    # Still attached to the (now also renamed) battery device, unmoved.
    assert moved.device_id == battery.id


@pytest.mark.asyncio
async def test_stranded_entity_is_still_rekeyed_after_the_device_was_already_renamed(
    hass: HomeAssistant,
):
    """Regression test for a real-world sequence: an earlier, narrower
    version of this migration already renamed the inverter device but
    missed a battery-attached entity (the exact scenario the test above
    guards going forward). On a *later* restart there is no longer any
    device registered under the old identifier -- it was already renamed
    -- so the migration must not use "found an old-identified device" as
    the signal for whether to also scan for stranded entities. Without
    this, a user who already hit the narrower bug once could never
    recover without manually removing the entity."""
    entry = _modbus_entry(hass)

    device_registry = dr.async_get(hass)
    inverter = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, NEW_INVERTER_SN)},  # already renamed
        name="Inverter",
    )
    battery = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, NEW_BATTERY_SN_EVEN)},  # already renamed too
        name=f"Battery {NEW_BATTERY_SN_EVEN}",
        via_device_id=inverter.id,
    )

    entity_registry = er.async_get(hass)
    stranded = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{OLD_INVERTER_SN}_bat_charge_month",  # never got rekeyed
        config_entry=entry,
        device_id=battery.id,
        suggested_object_id=f"hyxi_{OLD_INVERTER_SN}_bat_charge_month",
    )

    _migrate_hybrid_serial_decoding(
        hass, entry, _hybrid_devices(NEW_INVERTER_SN, NEW_BATTERY_SN_EVEN)
    )

    moved = entity_registry.async_get(stranded.entity_id)
    assert moved is not None
    assert moved.unique_id == f"hyxi_{NEW_INVERTER_SN}_bat_charge_month"
    assert moved.entity_id == stranded.entity_id
    # Exactly one inverter and one battery device -- the fix must not have
    # tried (and failed, or duplicated anything) to rename an already-
    # correct device.
    assert len(dr.async_entries_for_config_entry(device_registry, entry.entry_id)) == 2


@pytest.mark.asyncio
async def test_inverter_migration_running_before_registration_avoids_a_duplicate_device(
    hass: HomeAssistant,
):
    """Regression test for the ordering requirement itself: if the
    migration ran *after* _async_register_devices instead of before it,
    async_get_or_create would already have created a second device under
    the new identifier by the time the migration looked for one to rename,
    leaving both the orphaned old device and a fresh, history-less new
    one. Calling them in the real async_setup_entry order must produce
    exactly one device."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, OLD_INVERTER_SN)},
        name="Old Inverter",
        serial_number=OLD_INVERTER_SN,
    )

    devices = _hybrid_devices(NEW_INVERTER_SN, None)
    coordinator = MagicMock(data=devices)

    _migrate_hybrid_serial_decoding(hass, entry, devices)
    _async_register_devices(hass, entry, coordinator)

    all_devices_for_entry = dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    )
    assert len(all_devices_for_entry) == 1
    assert all_devices_for_entry[0].identifiers == {(DOMAIN, NEW_INVERTER_SN)}


@pytest.mark.asyncio
async def test_battery_device_is_renamed_for_an_even_length_serial(
    hass: HomeAssistant,
):
    """The common case: a battery serial whose length divides evenly into
    2-character register pairs, no embedded-null complication."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, OLD_BATTERY_SN_EVEN)},
        name=f"Battery {OLD_BATTERY_SN_EVEN}",
        serial_number=OLD_BATTERY_SN_EVEN,
    )

    _migrate_hybrid_serial_decoding(
        hass, entry, _hybrid_devices(NEW_INVERTER_SN, NEW_BATTERY_SN_EVEN)
    )

    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, OLD_BATTERY_SN_EVEN), entry.entry_id
        )
        is None
    )
    migrated = device_registry.async_get_device_by_identifier(
        (DOMAIN, NEW_BATTERY_SN_EVEN), entry.entry_id
    )
    assert migrated is not None
    assert migrated.serial_number == NEW_BATTERY_SN_EVEN


@pytest.mark.asyncio
async def test_battery_device_is_renamed_for_an_odd_length_serial(
    hass: HomeAssistant,
):
    """The trickier case this fix's own test fixture uses: an odd-length
    serial whose last register held one real character plus a null pad
    byte, which decode_string()'s big-endian read placed *before* that
    character rather than after it -- a naive double-swap reconstruction
    of the old identifier would miss this and never find the old device."""
    entry = _modbus_entry(hass)
    old_bat_sn = "AB1T531753\x007"
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, old_bat_sn)},
        name="Old Battery",
        serial_number=old_bat_sn,
    )

    _migrate_hybrid_serial_decoding(
        hass, entry, _hybrid_devices(NEW_INVERTER_SN, NEW_BATTERY_SN_ODD)
    )

    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, old_bat_sn), entry.entry_id
        )
        is None
    )
    migrated = device_registry.async_get_device_by_identifier(
        (DOMAIN, NEW_BATTERY_SN_ODD), entry.entry_id
    )
    assert migrated is not None


@pytest.mark.asyncio
async def test_no_op_when_nothing_was_ever_registered_under_the_old_identifier(
    hass: HomeAssistant,
):
    """A fresh install (or one already migrated) has no old-style device to
    find -- must not crash, and must not create anything new itself."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)

    _migrate_hybrid_serial_decoding(
        hass, entry, _hybrid_devices(NEW_INVERTER_SN, NEW_BATTERY_SN_EVEN)
    )

    assert dr.async_entries_for_config_entry(device_registry, entry.entry_id) == []


@pytest.mark.asyncio
async def test_entity_unique_id_collision_drops_the_legacy_duplicate(
    hass: HomeAssistant,
):
    """A previous migration attempt already renamed one entity but not the
    device (or was interrupted): both the legacy and the new-sn unique_id
    exist for the same entity_id's slot. async_update_entity would raise
    ValueError on the collision and abort the whole config entry setup --
    the migration must instead keep the already-renamed entity and drop
    the now-redundant legacy one, mirroring
    _migrate_vpp_dispatch_to_work_mode's own collision handling."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, OLD_INVERTER_SN)},
        name="Old Inverter",
    )

    entity_registry = er.async_get(hass)
    legacy = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{OLD_INVERTER_SN}_totalE",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id=f"hyxi_{OLD_INVERTER_SN}_totale",
    )
    already_renamed = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{NEW_INVERTER_SN}_totalE",
        config_entry=entry,
        suggested_object_id=f"hyxi_{NEW_INVERTER_SN}_totale",
    )

    _migrate_hybrid_serial_decoding(hass, entry, _hybrid_devices(NEW_INVERTER_SN, None))

    assert entity_registry.async_get(legacy.entity_id) is None
    assert entity_registry.async_get(already_renamed.entity_id) is not None


@pytest.mark.asyncio
async def test_inverter_migration_is_a_no_op_for_a_non_hex_serial(hass: HomeAssistant):
    """sn can be the "modbus_{unit_id}" fallback used when identity was
    unreadable at setup -- not a hex string at all, so there is nothing to
    reconstruct an old identifier from. Must not raise."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    _migrate_hybrid_inverter_serial(device_registry, entity_registry, entry, "modbus_1")

    assert dr.async_entries_for_config_entry(device_registry, entry.entry_id) == []


@pytest.mark.asyncio
async def test_inverter_migration_is_a_no_op_when_old_and_new_sn_coincide(
    hass: HomeAssistant,
):
    """A single hex digit reads back as the same string in decimal too
    (e.g. "5" -> int("5", 16) == 5 -> str(5) == "5") -- guards against
    treating that as something to rename."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "5")}
    )

    _migrate_hybrid_inverter_serial(device_registry, entity_registry, entry, "5")

    # Untouched: still exactly the one device, under its original identifier.
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert len(devices) == 1
    assert devices[0].identifiers == {(DOMAIN, "5")}


@pytest.mark.asyncio
async def test_inverter_migration_only_rekeys_entities_whose_unique_id_embeds_the_old_sn(
    hass: HomeAssistant,
):
    """A device-level diagnostic entity (unique_id keyed on entry_id, not
    sn -- e.g. HyxiModbusConnectionTypeSensor) attached to the same device
    must be left alone."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, OLD_INVERTER_SN)}
    )
    entity_registry = er.async_get(hass)
    unrelated = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_modbus_connection_type",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="modbus_connection_type",
    )

    _migrate_hybrid_serial_decoding(hass, entry, _hybrid_devices(NEW_INVERTER_SN, None))

    unchanged = entity_registry.async_get(unrelated.entity_id)
    assert unchanged is not None
    assert unchanged.unique_id == f"{entry.entry_id}_modbus_connection_type"


@pytest.mark.asyncio
async def test_battery_migration_is_a_no_op_for_an_empty_serial(hass: HomeAssistant):
    """bat_sn can't actually be empty by the time _migrate_hybrid_serial_decoding
    calls this (it's gated on `if bat_sn:`), but _migrate_hybrid_battery_serial
    itself must still degrade gracefully if ever called with one directly."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    _migrate_hybrid_battery_serial(device_registry, entity_registry, entry, "")

    assert dr.async_entries_for_config_entry(device_registry, entry.entry_id) == []


@pytest.mark.asyncio
async def test_inverter_collision_merges_the_legacy_device_instead_of_raising(
    hass: HomeAssistant,
):
    """Regression test: if a device already exists under the corrected
    identifier (e.g. a setup that ran once between the decode fix landing
    and this migration reaching it, registering a fresh device before the
    legacy one could be renamed), device_registry.async_update_device
    would raise DeviceIdentifierCollisionError rather than silently
    overwriting it. The migration must instead move any entities still on
    the legacy device onto the correct one and remove the legacy device,
    without that exception ever reaching setup."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)
    legacy_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, OLD_INVERTER_SN)},
        name="Old Inverter",
    )
    correct_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, NEW_INVERTER_SN)},
        name="Correct Inverter",
    )

    entity_registry = er.async_get(hass)
    stranded = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"hyxi_{OLD_INVERTER_SN}_totalE",
        config_entry=entry,
        device_id=legacy_device.id,
        suggested_object_id=f"hyxi_{OLD_INVERTER_SN}_totale",
    )

    _migrate_hybrid_serial_decoding(hass, entry, _hybrid_devices(NEW_INVERTER_SN, None))

    # Exactly one device left, the pre-existing correct one -- not a third.
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert len(devices) == 1
    assert devices[0].id == correct_device.id

    # The entity that was stranded on the legacy device now lives on the
    # correct one, unique_id rekeyed, entity_id (and so its history) intact.
    moved = entity_registry.async_get(stranded.entity_id)
    assert moved is not None
    assert moved.device_id == correct_device.id
    assert moved.unique_id == f"hyxi_{NEW_INVERTER_SN}_totalE"


@pytest.mark.asyncio
async def test_battery_collision_merges_the_legacy_device_instead_of_raising(
    hass: HomeAssistant,
):
    """Same collision-handling requirement, for the battery device."""
    entry = _modbus_entry(hass)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, OLD_BATTERY_SN_EVEN)},
        name="Old Battery",
    )
    correct_battery = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, NEW_BATTERY_SN_EVEN)},
        name="Correct Battery",
    )

    _migrate_hybrid_serial_decoding(
        hass, entry, _hybrid_devices(NEW_INVERTER_SN, NEW_BATTERY_SN_EVEN)
    )

    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert len(devices) == 1
    assert devices[0].id == correct_battery.id


def test_unique_id_rekey_does_not_corrupt_an_entry_id_that_contains_the_old_sn():
    """Regression test for a reviewer-flagged edge case: a naive
    `unique_id.replace(old_sn, sn)` would also corrupt the entry_id prefix
    on unique_ids like f"{entry.entry_id}_{sn}_work_mode" if entry_id ever
    happened to contain old_sn's digits as a bare substring. Delimiting the
    match as f"_{old_sn}_" avoids this, since entry_id is a contiguous
    random id with no underscores of its own to create a false boundary."""
    old_sn = "123"
    sn = "7b1"
    fake_entry_id_containing_old_sn = f"abc{old_sn}def"
    unique_id = f"{fake_entry_id_containing_old_sn}_{old_sn}_work_mode"

    delimited_old = f"_{old_sn}_"
    delimited_new = f"_{sn}_"
    assert delimited_old in unique_id
    new_unique_id = unique_id.replace(delimited_old, delimited_new)

    assert new_unique_id == f"{fake_entry_id_containing_old_sn}_{sn}_work_mode"
    assert old_sn in new_unique_id  # only inside the untouched entry_id prefix
    assert fake_entry_id_containing_old_sn in new_unique_id


@pytest.mark.asyncio
async def test_energy_manager_option_is_repointed_at_the_corrected_serial(
    hass: HomeAssistant,
):
    """A user with the Energy Manager configured against a hybrid inverter
    must not have EM silently disable itself after the upgrade --
    _async_setup_energy_manager's `em_sn not in coordinator.data` guard
    would otherwise never match again, since coordinator.data is now keyed
    on the corrected sn. Also confirms EM's own virtual device
    ({sn}_energy_manager, a different identifier from the inverter's own)
    is renamed alongside the option -- entities alone re-keying via the
    generic scan wouldn't rescue this otherwise-orphaned device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_TRANSPORT: TRANSPORT_MODBUS},
        options={"enable_battery_control": True, CONF_EM_INVERTER_SN: OLD_INVERTER_SN},
        unique_id="modbus-em-migration-test",
    )
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{OLD_INVERTER_SN}_energy_manager")},
        name="Energy Manager",
    )

    _migrate_energy_manager_inverter_sn(hass, entry, OLD_INVERTER_SN, NEW_INVERTER_SN)

    assert entry.options[CONF_EM_INVERTER_SN] == NEW_INVERTER_SN
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{OLD_INVERTER_SN}_energy_manager"), entry.entry_id
        )
        is None
    )
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{NEW_INVERTER_SN}_energy_manager"), entry.entry_id
        )
        is not None
    )


@pytest.mark.asyncio
async def test_energy_manager_option_is_untouched_when_it_points_elsewhere(
    hass: HomeAssistant,
):
    """Only repoint the option when it actually matches the sn being
    migrated -- an EM configured against a different (unaffected) device
    must be left alone."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_TRANSPORT: TRANSPORT_MODBUS},
        options={"enable_battery_control": True, CONF_EM_INVERTER_SN: "SOME_OTHER_SN"},
        unique_id="modbus-em-migration-test-2",
    )
    entry.add_to_hass(hass)

    _migrate_energy_manager_inverter_sn(hass, entry, OLD_INVERTER_SN, NEW_INVERTER_SN)

    assert entry.options[CONF_EM_INVERTER_SN] == "SOME_OTHER_SN"

"""One-off registry migrations and clean-ups run when a HYXI entry is set up."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import (
    BATTERY_SENSORS,
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    DOMAIN,
    EM_OPTION_KEYS,
    ENERGY_STORAGE_DEVICE_TYPES,
    device_type_of,
    entry_stable_key,
    get_raw_device_code,
    is_battery_control_enabled,
    is_control_capable_device_type,
    is_modbus_entry,
    mask_sn,
    normalize_device_type,
    offers_device_control,
)
from .coordinator import HyxiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _reconstruct_pre_fix_battery_serial(value: str) -> str | None:
    """Rebuild the exact identifier a hybrid battery would have been
    registered under before client_hybrid.py's byte-order fix, from the
    now-correct value.

    Mirrors the bug precisely -- see
    client_hybrid._fix_battery_serial_byte_order's own docstring for the
    forward direction. Full register pairs come out swapped, but for an
    odd-length serial, the final (partially null-padded) register's high
    byte was the pad, not a character: decode_string()'s big-endian read
    then puts that null *before* the last real character rather than
    after it, so decode_string()'s own rstrip never saw a trailing null to
    strip. A straight double-swap misses this and reconstructs the wrong
    identifier -- verified against the exact bytes the fix's own test
    fixture uses.
    """
    if not value:
        return None
    odd = len(value) % 2 == 1
    full_pairs = value[:-1] if odd else value
    swapped = "".join(full_pairs[i : i + 2][::-1] for i in range(0, len(full_pairs), 2))
    return f"{swapped}\x00{value[-1]}" if odd else swapped


def _mask_serials_in(text: str, *serials: str) -> str:
    """Replace every occurrence of any of `serials` in `text` with its
    mask_sn() hash, for logging a compound string (an entity_id or
    unique_id) that might embed more than one of them."""
    for serial in serials:
        text = text.replace(serial, mask_sn(serial))
    return text


def _rename_or_merge_device_identifier(
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    entry: ConfigEntry,
    old_identifier: str,
    new_identifier: str,
) -> dr.DeviceEntry | None:
    """Rename a device from old_identifier to new_identifier in place.

    If a device already exists under new_identifier -- e.g. a setup that
    ran once between the decode fix landing and this migration reaching
    it, registering a fresh device under the corrected identifier before
    the legacy one could be renamed onto it -- async_update_device would
    raise DeviceIdentifierCollisionError rather than silently overwriting
    it. Move any entities still on the legacy device onto the correct one
    instead, then remove the now-empty legacy device.

    Returns the resulting device (old, renamed, or the pre-existing new
    one after the merge), or None if there was no legacy device to do
    anything with.
    """
    old_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, old_identifier), entry.entry_id
    )
    if old_device is None:
        return None
    new_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, new_identifier), entry.entry_id
    )
    if new_device is not None and new_device.id != old_device.id:
        _LOGGER.debug(
            "Merging legacy device %s into %s; a device already exists "
            "under the corrected identifier",
            mask_sn(old_identifier),
            mask_sn(new_identifier),
        )
        for reg_entry in er.async_entries_for_device(
            entity_registry, old_device.id, include_disabled_entities=True
        ):
            entity_registry.async_update_entity(
                reg_entry.entity_id, device_id=new_device.id
            )
        device_registry.async_remove_device(old_device.id)
        return new_device
    _LOGGER.debug(
        "Migrating hybrid device %s -> %s",
        mask_sn(old_identifier),
        mask_sn(new_identifier),
    )
    return device_registry.async_update_device(
        old_device.id,
        new_identifiers={(DOMAIN, new_identifier)},
        serial_number=new_identifier,
    )


def _migrate_hybrid_inverter_serial(
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    entry: ConfigEntry,
    sn: str,
) -> str | None:
    """Rename one hybrid inverter's device, and every entity's unique_id,
    from the old decimal-misread serial to the corrected hex one.

    Scans every entity on the config entry rather than just the ones
    attached to the inverter's own device_id: HyxiBatteryEnergyPeriodSensor
    (and potentially others) keys its unique_id on the inverter's sn but
    attaches to the *battery's* device via device_info, so a device_id-
    scoped scan silently misses it.

    Deliberately does *not* gate the entity rekey on a device actually
    needing renaming: an install that already went through an earlier,
    narrower version of this migration (device renamed, but a
    battery-attached entity like this one missed) has no old-identified
    device left to find on a later run, yet still has that entity stranded
    on its stale unique_id. Both parts must run independently so a later
    version of this migration can still finish the job.

    Returns the old sn whenever it's computable, so the caller can also
    fix up anything else keyed on it (see the CONF_EM_INVERTER_SN check
    below) even when there was no device left to rename -- or None if sn
    isn't a hex string to begin with.
    """
    try:
        old_sn = str(int(sn, 16))
    except ValueError:
        # sn isn't a hex string -- e.g. the "modbus_{unit_id}" fallback
        # used when identity was unreadable. Nothing to migrate from.
        return None
    if old_sn == sn:
        return None
    _rename_or_merge_device_identifier(
        device_registry, entity_registry, entry, old_sn, sn
    )
    delimited_old = f"_{old_sn}_"
    delimited_new = f"_{sn}_"
    for reg_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if delimited_old not in reg_entry.unique_id:
            continue
        new_unique_id = reg_entry.unique_id.replace(delimited_old, delimited_new)
        try:
            entity_registry.async_update_entity(
                reg_entry.entity_id, new_unique_id=new_unique_id
            )
        except ValueError:
            # Both the legacy and the renamed unique_id already exist for
            # this entity (e.g. a previous migration attempt partially
            # completed) -- keep the already-renamed one and drop the
            # dangling legacy duplicate, same rationale as
            # _migrate_vpp_dispatch_to_work_mode below. entity_id and
            # unique_id can each embed *either* serial -- the entry_id
            # prefix this rekey deliberately preserves untouched can itself
            # coincidentally contain old_sn's digits (see
            # test_unique_id_rekey_does_not_corrupt_an_entry_id_that_contains_the_old_sn) --
            # so mask both, not just whichever one each string is expected
            # to carry.
            _LOGGER.debug(
                "Removing orphaned legacy entity %s; %s already exists",
                _mask_serials_in(reg_entry.entity_id, old_sn, sn),
                _mask_serials_in(new_unique_id, old_sn, sn),
            )
            entity_registry.async_remove(reg_entry.entity_id)
    return old_sn


def _migrate_hybrid_battery_serial(
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    entry: ConfigEntry,
    bat_sn: str,
) -> None:
    """Rename one hybrid battery's device from the old byte-swapped serial
    to the corrected one.

    No entity unique_id embeds bat_sn -- every entity is keyed on the
    inverter's own sn regardless of which device it displays under (see
    _battery_device_info) -- so only the device registry entry itself
    needs renaming.
    """
    old_bat_sn = _reconstruct_pre_fix_battery_serial(bat_sn)
    if not old_bat_sn or old_bat_sn == bat_sn:
        return
    _rename_or_merge_device_identifier(
        device_registry, entity_registry, entry, old_bat_sn, bat_sn
    )


def _migrate_energy_manager_inverter_sn(
    hass: HomeAssistant, entry: ConfigEntry, old_sn: str, sn: str
) -> None:
    """Point the Energy Manager at the corrected inverter serial if it was
    configured against the pre-fix one.

    Two independent things would otherwise stay on the old sn: the
    CONF_EM_INVERTER_SN option itself -- _async_setup_energy_manager's own
    `em_sn not in coordinator.data` guard would silently disable EM once
    coordinator.data is keyed on the corrected sn instead, with nothing
    surfacing that to a user -- and EM's own virtual device, registered
    under identifiers={(DOMAIN, f"{em_sn}_energy_manager")} (see
    binary_sensor.py/number.py/sensor.py/switch.py's EM device_info). Its
    entities are already covered by _migrate_hybrid_inverter_serial's
    generic unique_id scan (they're all hyxi_{sn}_em_*, matching that
    scan's delimited pattern), but the device identifier itself is not, so
    it's handled here the same way as the inverter/battery devices.

    The option and the device are migrated independently, not gated behind
    one shared check: an install that already went through the option-only
    version of this migration has CONF_EM_INVERTER_SN == sn already, with
    the EM device still stranded under old_sn -- gating the device rename
    on `option == old_sn` would make it permanently unreachable for
    exactly the installs this fix is for, the same lesson as
    _migrate_hybrid_inverter_serial's entity-rekey/device-rename split.
    """
    current = entry.options.get(CONF_EM_INVERTER_SN)
    if current == old_sn:
        _LOGGER.debug(
            "Migrating Energy Manager inverter_sn option %s -> %s",
            mask_sn(old_sn),
            mask_sn(sn),
        )
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_EM_INVERTER_SN: sn}
        )
    elif current != sn:
        # EM isn't configured for this device at all -- nothing to migrate.
        return
    _rename_or_merge_device_identifier(
        dr.async_get(hass),
        er.async_get(hass),
        entry,
        f"{old_sn}_energy_manager",
        f"{sn}_energy_manager",
    )


def _migrate_hybrid_serial_decoding(
    hass: HomeAssistant, entry: ConfigEntry, devices: dict
) -> None:
    """Rename hybrid Modbus devices onboarded under two now-fixed decoding
    bugs in client_hybrid.py, so they keep their history and entity_ids
    instead of silently reappearing as brand-new devices.

    The inverter's own serial (register 1007, H64) used to be read as a
    plain decimal integer instead of the hex-formatted value the document
    specifies; the battery serial (register 1015, S20 string) used to have
    its two per-register bytes read in the wrong order. Both feed a device
    registry identifier (`identifiers={(DOMAIN, sn)}` /
    `{(DOMAIN, bat_sn)}`) directly, so fixing the decoding alone would
    silently orphan every already-onboarded install's inverter and battery
    devices.

    Must run before _async_register_devices: that call's own
    async_get_or_create only finds an existing device by its *current*
    identifier, so it would create a second, duplicate device under the
    new identifier if this hasn't already renamed the old one in place.
    Cheap and safe to run on every setup: a no-op once the old identifiers
    no longer exist. HALO devices and Cloud entries are unaffected -- both
    are correctly decoded already -- so this only touches hybrid inverters.
    """
    if not is_modbus_entry(entry):
        return
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for sn, dev_data in devices.items():
        if normalize_device_type(get_raw_device_code(dev_data)) != "hybrid_inverter":
            continue
        old_sn = _migrate_hybrid_inverter_serial(
            device_registry, entity_registry, entry, sn
        )
        if old_sn is not None:
            _migrate_energy_manager_inverter_sn(hass, entry, old_sn, sn)
        bat_sn = (dev_data.get("metrics") or {}).get("batSn")
        if bat_sn:
            _migrate_hybrid_battery_serial(
                device_registry, entity_registry, entry, bat_sn
            )


def _remove_purge_subscriptions_button(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the "Purge Old Subscriptions" button: the Subscription Status
    sensor now lists what HYXI holds, to cancel through the service."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_purge_old_subscriptions"
    )
    if entity_id is not None:
        _LOGGER.debug("Removing obsolete HYXI button %s", entity_id)
        registry.async_remove(entity_id)


def _remove_legacy_select_entities(hass: HomeAssistant, devices: dict) -> None:
    """Remove obsolete select entities replaced by stateless buttons."""
    registry = er.async_get(hass)
    for sn in devices:
        for unique_id in (
            f"hyxi_{sn}_operating_mode",
            f"hyxi_{sn}_peak_shaving",
        ):
            entity_id = registry.async_get_entity_id("select", DOMAIN, unique_id)
            if entity_id is not None:
                _LOGGER.debug("Removing legacy HYXI select entity %s", entity_id)
                registry.async_remove(entity_id)


def _migrate_vpp_dispatch_to_work_mode(
    hass: HomeAssistant, entry: ConfigEntry, devices: dict
) -> None:
    """Migrate HyxiVppDispatchSensor's unique_id after its rename to
    HyxiWorkModeSensor.

    The entity was renamed from "vpp_dispatch" to "work_mode" to match the
    metric key it actually reads (the cloud API has no dedicated VPP
    field). Without this, every existing install would get a fresh,
    history-less entity at a new entity_id on upgrade, while the old one
    goes permanently unavailable in the registry -- dangling, not gone,
    until a user notices and deletes it by hand. Cheap and safe to run on
    every setup: a no-op once the old unique_id no longer exists.
    """
    registry = er.async_get(hass)
    for sn in devices:
        old_unique_id = f"{entry.entry_id}_{sn}_vpp_dispatch"
        entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, old_unique_id)
        if entity_id is None:
            continue
        new_unique_id = f"{entry.entry_id}_{sn}_work_mode"
        if (
            registry.async_get_entity_id("binary_sensor", DOMAIN, new_unique_id)
            is not None
        ):
            # Both the legacy and the renamed entity already exist for this
            # device (e.g. a previous migration attempt partially
            # completed). async_update_entity would raise ValueError on the
            # unique_id collision and abort the whole config entry setup --
            # instead keep the work_mode entity (already the live one, with
            # whatever history it has) and drop the now-redundant legacy
            # duplicate.
            _LOGGER.debug(
                "Removing orphaned legacy vpp_dispatch entity %s; %s already exists",
                entity_id,
                new_unique_id,
            )
            registry.async_remove(entity_id)
            continue
        _LOGGER.debug(
            "Migrating %s from vpp_dispatch to work_mode unique_id", entity_id
        )
        registry.async_update_entity(entity_id, new_unique_id=new_unique_id)


def _rekey_registry_entity(
    registry: er.EntityRegistry, domain: str, old_unique_id: str, new_unique_id: str
) -> None:
    """Move a registry entry from old_unique_id to new_unique_id, in place.

    The entity_id is left untouched, so the recorder's long-term statistics
    (keyed by entity_id) stay attached. A no-op when old_unique_id isn't
    registered. If something already holds new_unique_id -- a prior partial
    migration, or the id having flip-flopped so both survive -- the
    migration-source entry wins (it is the older keying scheme, so almost
    always the longer-history row) and the clashing entry is dropped; this
    is logged at warning level since the dropped row's statistics, if any,
    are then orphaned and a user may want to reconcile them by hand.
    """
    if old_unique_id == new_unique_id:
        return
    old_entity_id = registry.async_get_entity_id(domain, DOMAIN, old_unique_id)
    if old_entity_id is None:
        return
    clash = registry.async_get_entity_id(domain, DOMAIN, new_unique_id)
    if clash is None:
        _LOGGER.debug(
            "Re-keying %s unique_id %s -> %s",
            old_entity_id,
            old_unique_id,
            new_unique_id,
        )
    else:
        _LOGGER.warning(
            "Re-keying %s to unique_id %s but %s already holds it; dropping %s "
            "(any statistics it accumulated are now orphaned -- reconcile via "
            "Developer Tools > Statistics if needed)",
            old_entity_id,
            new_unique_id,
            clash,
            clash,
        )
        registry.async_remove(clash)
    registry.async_update_entity(old_entity_id, new_unique_id=new_unique_id)


def _split_battery_unique_id(
    unique_id: str, keys_longest_first: list[str]
) -> tuple[str, str] | None:
    """Split a ``hyxi_{sn}_{battery-key}`` unique_id into ``(sn, key)``.

    Returns None if it isn't one. Serials and some keys both contain
    underscores, so the key is matched as a suffix, longest first, rather
    than splitting on ``_`` (e.g. so ``bat_charge_total`` wins and its sn
    isn't left with a trailing ``_charge``).
    """
    if not unique_id.startswith("hyxi_"):
        return None
    body = unique_id[len("hyxi_") :]
    for key in keys_longest_first:
        suffix = f"_{key}"
        if body.endswith(suffix):
            return body[: -len(suffix)], key
    return None


def _inverter_sn_via_device(
    device_registry: dr.DeviceRegistry, device_id: str | None, devices: dict[str, dict]
) -> str | None:
    """Resolve the inverter sn a battery device hangs off, via its via_device.

    The fallback for when current telemetry carries no batSn to build the
    battery->inverter map from directly: the battery device and its
    via_device link were registered on an earlier run and persist in the
    device registry regardless.
    """
    if device_id is None:
        return None
    device = device_registry.async_get(device_id)
    # From HA 2026.9 async_get is typed to also return a ChildDeviceEntry,
    # which carries no via_device_id; getattr keeps the lookup total.
    via_id = getattr(device, "via_device_id", None)
    if via_id is None:
        return None
    parent = device_registry.async_get(via_id)
    if parent is None:
        return None
    # sorted() so a parent that somehow carries more than one matching
    # identifier resolves the same way on every run.
    return next(
        (
            ident
            for domain, ident in sorted(parent.identifiers)
            if domain == DOMAIN and ident in devices
        ),
        None,
    )


def _battery_serial_to_inverter(devices: dict[str, dict]) -> dict[str, str]:
    """Map each usable ``batSn`` in current telemetry to its inverter sn.

    Excluded: a battery that is itself a first-class device (its own
    coordinator.data entry already keys its sensors off its own sn), and a
    serial reported by more than one inverter -- there's no way to tell
    which inverter an existing batSn-keyed row belonged to, so leave those
    alone rather than move their history to an arbitrary one.
    """
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for sn, dev_data in devices.items():
        bat_sn = (dev_data.get("metrics") or {}).get("batSn")
        if not isinstance(bat_sn, str) or not bat_sn.strip():
            continue
        if bat_sn == sn or bat_sn in devices:
            continue
        if mapping.setdefault(bat_sn, sn) != sn:
            ambiguous.add(bat_sn)
    return {b: i for b, i in mapping.items() if b not in ambiguous}


def _migrate_battery_sensor_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, devices: dict[str, dict]
) -> None:
    """Re-key battery sensors from the battery serial to the inverter serial.

    Battery sensors were keyed ``hyxi_{batSn}_{key}`` before 1.7.0. batSn
    comes from runtime telemetry that isn't reliably present when the
    entity is built (absent from the cloud poll -- push only; a Modbus
    string register that can read blank), so the unique_id flipped between
    the battery and the inverter serial across restarts, and every flip
    orphaned the old entity's long-term statistics. sensor.py now keys off
    the inverter serial (the coordinator data key -- present every time);
    this moves any existing batSn-keyed registry entries onto the new id so
    their history carries over. Cheap and idempotent: a no-op once every
    battery sensor is inverter-serial-keyed.

    Migration shim added 2026-08 (1.7.0). Safe to delete once installs are
    reasonably expected to have run a >=1.7.0 setup at least once.
    """
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    bat_to_inverter = _battery_serial_to_inverter(devices)
    keys_longest_first = sorted(BATTERY_SENSORS, key=len, reverse=True)

    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain != "sensor":
            continue
        parsed = _split_battery_unique_id(reg_entry.unique_id, keys_longest_first)
        if parsed is None:
            continue
        id_sn, key = parsed
        if id_sn in devices:
            continue  # already inverter-keyed (or a first-class battery device)
        inverter_sn = bat_to_inverter.get(id_sn) or _inverter_sn_via_device(
            device_registry, reg_entry.device_id, devices
        )
        if inverter_sn is None:
            continue
        _rekey_registry_entity(
            registry, "sensor", reg_entry.unique_id, f"hyxi_{inverter_sn}_{key}"
        )


#: Legacy battery-energy keys and the canonical key each duplicates. HYXI
#: sends one lifetime counter under a poll name, a push name and the SDK
#: alias; only the alias keeps an entity.
_DUPLICATE_ENERGY_KEYS = {
    "totalEchg": "bat_charge_total",
    "batCharge": "bat_charge_total",
    "totalEdchg": "bat_discharge_total",
    "batDisCharge": "bat_discharge_total",
}


def _merge_duplicate_battery_energy_sensors(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Collapse the triplicate lifetime battery-energy sensors to one pair.

    ``totalEchg``/``batCharge`` (and the discharge equivalents) always held
    the exact value of ``bat_charge_total``/``bat_discharge_total``. Only the
    canonical key gets an entity now: keep the canonical registry row (the
    one wired into the Energy dashboard) and drop the duplicates, or promote
    a duplicate if the canonical was never created. Idempotent, and
    independent of the old batSn-vs-inverter keying since it matches on the
    suffix.

    Removed entities take their history with them -- a dashboard card
    pointing at a ``..._totalechg`` / ``..._batcharge`` sensor needs
    repointing to the ``..._bat_charge_total`` pair.
    """
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain != "sensor" or not reg_entry.unique_id.startswith("hyxi_"):
            continue
        for dupe_key, canonical_key in _DUPLICATE_ENERGY_KEYS.items():
            if not reg_entry.unique_id.endswith(f"_{dupe_key}"):
                continue
            canonical_uid = reg_entry.unique_id[: -len(dupe_key)] + canonical_key
            if registry.async_get_entity_id("sensor", DOMAIN, canonical_uid):
                _LOGGER.debug(
                    "Removing duplicate battery-energy sensor %s (%s exists)",
                    reg_entry.entity_id,
                    canonical_uid,
                )
                registry.async_remove(reg_entry.entity_id)
            else:
                _LOGGER.debug("Re-keying %s -> %s", reg_entry.unique_id, canonical_uid)
                registry.async_update_entity(
                    reg_entry.entity_id, new_unique_id=canonical_uid
                )
            break


def _migrate_microinverter_sum_identifiers(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Move the microinverter aggregate entity + device off entry_id keying.

    Their unique_id and device identifier embedded ``entry.entry_id`` before
    1.7.0, which is regenerated on a remove-and-re-add and stranded the
    aggregates' long-term statistics. sensor.py now keys them off
    ``entry_stable_key(entry)``; this moves an existing entry_id-keyed
    entity and device forward. A no-op once moved, or when there's no
    stable key to move to (an entry with no unique_id, where
    entry_stable_key() returns entry_id).

    Migration shim added 2026-08 (1.7.0). Safe to delete once installs are
    reasonably expected to have run a >=1.7.0 setup at least once.
    """
    stable_key = entry_stable_key(entry)
    if stable_key == entry.entry_id:
        return

    registry = er.async_get(hass)
    # The two aggregate keys that existed when this shim was written -- a
    # migration only ever needs the ids that shipped before it.
    aggregate_keys = ("micro_ac_power_total", "micro_daily_yield_total")
    for key in aggregate_keys:
        _rekey_registry_entity(
            registry, "sensor", f"{entry.entry_id}_{key}", f"{stable_key}_{key}"
        )

    device_registry = dr.async_get(hass)
    stable_identifier = (DOMAIN, f"{stable_key}_microinverters_summary")
    stable_identifiers = {stable_identifier}
    old_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_microinverters_summary"), entry.entry_id
    )
    if old_device is None:
        return
    stable_device = device_registry.async_get_device_by_identifier(
        stable_identifier, entry.entry_id
    )
    if stable_device is None:
        device_registry.async_update_device(
            old_device.id, new_identifiers=stable_identifiers
        )
    else:
        # The stable-keyed summary device already exists (a prior partial
        # migration). Re-home the aggregates onto it before dropping the
        # legacy device: removing a device also removes every entity still
        # pointing at it, which would take the entries re-keyed above -- and
        # the statistics this shim exists to save -- down with it.
        for key in aggregate_keys:
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{stable_key}_{key}"
            )
            if entity_id is not None:
                registry.async_update_entity(entity_id, device_id=stable_device.id)
        device_registry.async_remove_device(old_device.id)


def _remove_work_mode_sensor_for_modbus(
    hass: HomeAssistant, entry: ConfigEntry, devices: dict
) -> None:
    """Remove HyxiWorkModeSensor's registry entry for Modbus entries.

    The sensor reports an active VPP dispatch via workMode, which neither
    Modbus client can back with a verified register -- see
    binary_sensor.py's async_setup_entry for why it's no longer created
    for Modbus. Without this, anyone who already had it (from before that
    change, or from switching a device from cloud to Modbus) keeps a
    dangling, permanently-unavailable entity in the registry instead of it
    actually going away. Cheap and safe to run on every setup: a no-op
    once the entity no longer exists.
    """
    if not is_modbus_entry(entry):
        return
    registry = er.async_get(hass)
    for sn in devices:
        unique_id = f"{entry.entry_id}_{sn}_work_mode"
        entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id)
        if entity_id is not None:
            _LOGGER.debug("Removing work_mode entity %s for Modbus entry", entity_id)
            registry.async_remove(entity_id)


def _remove_alarm_entities_for_modbus(
    hass: HomeAssistant, entry: ConfigEntry, devices: dict
) -> None:
    """Remove HyxiDeviceAlarmSensor's and HyxiClearAlarmsButton's registry
    entries for Modbus entries.

    Both read/act on dev_data["alarms"], which neither Modbus client
    populates -- see binary_sensor.py's and button.py's async_setup_entry
    for why neither is created for Modbus. Without this, anyone who
    already had them (from before that change, or from switching a device
    from cloud to Modbus) keeps dangling, permanently-unavailable entities
    in the registry instead of them actually going away. Cheap and safe to
    run on every setup: a no-op once neither entity exists.
    """
    if not is_modbus_entry(entry):
        return
    registry = er.async_get(hass)
    for sn in devices:
        for domain, unique_id in (
            ("binary_sensor", f"{entry.entry_id}_{sn}_device_alarm"),
            ("button", f"hyxi_{sn}_clear_alarms"),
        ):
            entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
            if entity_id is not None:
                _LOGGER.debug("Removing alarm entity %s for Modbus entry", entity_id)
                registry.async_remove(entity_id)


_BMS_ALARM_SUFFIXES = ("_batAlarm1", "_batAlarm2", "_batAlarm3")


def _remove_bms_alarm_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the batAlarm1-3 sensors, which no transport creates any more.

    They read HALO's BMS alarm words, which are unsupported
    (docs/modbus-provenance.md, rule 3). Matched by unique_id suffix across
    the whole entry rather than per current serial, so rows keyed by a
    battery serial (from before the 1.7.0 re-keying) go too. Cheap and safe
    to run on every setup: a no-op once none exist.
    """
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain == "sensor" and reg_entry.unique_id.endswith(
            _BMS_ALARM_SUFFIXES
        ):
            _LOGGER.debug("Removing BMS alarm sensor %s", reg_entry.entity_id)
            registry.async_remove(reg_entry.entity_id)


# Entry data key marking that the entry was told about battery control it
# can't use, so the repair issue is raised once.
_BATTERY_CONTROL_NOTICE = "cloud_battery_control_notified"


def _cloud_battery_control_issue_id(entry: ConfigEntry) -> str:
    return f"cloud_battery_control_{entry.entry_id}"


def _retire_cloud_battery_control(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Switch off battery control an entry can't use, and say why once.

    An entry with device control or the Energy Manager on, and an
    energy-storage device whose battery it can't control (see
    is_control_capable_device_type -- over the cloud, HYXI doesn't accept
    battery control with an owner's API keys), gets a repair issue. With
    no controllable battery left, the Energy Manager is turned off along
    with its device, and device control too unless microinverters still
    use it.
    """
    if not (is_battery_control_enabled(entry) or entry.options.get(CONF_EM_ENABLED)):
        return
    types = {device_type_of(dev_data) for dev_data in (coordinator.data or {}).values()}
    controllable = any(is_control_capable_device_type(entry, t) for t in types)
    stranded = any(
        t in ENERGY_STORAGE_DEVICE_TYPES
        and not is_control_capable_device_type(entry, t)
        for t in types
    )
    if not stranded:
        return

    options = dict(entry.options)
    if not controllable:
        if em_sn := options.get(CONF_EM_INVERTER_SN):
            _remove_energy_manager_device(hass, entry, em_sn)
        for key in EM_OPTION_KEYS:
            options.pop(key, None)
        if not any(offers_device_control(entry, t) for t in types):
            options["enable_battery_control"] = False
    coordinator.options = options
    if entry.data.get(_BATTERY_CONTROL_NOTICE):
        hass.config_entries.async_update_entry(entry, options=options)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        _cloud_battery_control_issue_id(entry),
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="cloud_battery_control",
        translation_placeholders={"entry": entry.title},
    )
    hass.config_entries.async_update_entry(
        entry, options=options, data={**entry.data, _BATTERY_CONTROL_NOTICE: True}
    )


def _remove_energy_manager_device(
    hass: HomeAssistant, entry: ConfigEntry, em_sn: str
) -> None:
    """Remove the Energy Manager's virtual device, and with it its entities."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{em_sn}_energy_manager"), entry.entry_id
    )
    if device is not None:
        device_registry.async_remove_device(device.id)


def _cleanup_control_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Remove control entities from registry if battery control is disabled."""
    # Imported here rather than at the top, so that importing the entry's
    # setup doesn't load the button and number platforms.
    from .button import POWER_COMMAND_ICONS, WORK_MODE_ICONS
    from .const import is_battery_control_enabled
    from .number import (
        HALO_SETTING_NUMBER_DEFS,
        HYBRID_SETTING_NUMBER_DEFS,
    )

    enabled = is_battery_control_enabled(entry)
    # A device keeps its control entities while device control is on and it
    # takes device controls.
    stale_sns = [
        sn
        for sn, dev_data in coordinator.data.items()
        if not enabled or not offers_device_control(entry, device_type_of(dev_data))
    ]
    if enabled and not stale_sns:
        return

    _LOGGER.debug("Cleaning up control entities no longer offered from the registry")
    registry = er.async_get(hass)
    # Device-scoped (hyxi_{sn}_{key}) -- every entity that only exists at
    # all because is_battery_control_enabled(entry) was True when it was
    # created, cloud and Modbus alike. Keep this in sync with every
    # is_battery_control_enabled(entry) check in button.py/number.py/
    # switch.py -- a key missing here is an entity that lingers as
    # "unavailable" forever once created, instead of disappearing the
    # moment the option that created it is turned back off. Sourced from
    # the same defs/dicts those modules build their entities from wherever
    # one already exists, rather than retyped, so a def added there is
    # covered here automatically instead of silently drifting out of sync
    # the way this list itself once did.
    keys_to_remove = (
        frozenset(
            (
                "mode_idle",
                "mode_charge",
                "mode_discharge",
                "mode_self_consume",
                "peak_shaving_close",
                "peak_shaving_charge",
                "peak_shaving_discharge",
                "peak_shaving_stop",
                "peak_shaving_hold",
                "frequency_control",
                "micro_power",
                "micro_ess_power",
                "charge_power",
                "discharge_power",
                "soc_min",
                "soc_max",
                "soc_min_hysteresis_pct",
                "soc_max_hysteresis_pct",
                "micro_power_limit",
                "last_sent_mode",
                # Modbus settings switches with no HyxiSettingNumberDef
                # equivalent (switch.py's HyxiAntiStarvationSwitch /
                # HyxiDispatchSwitch).
                "anti_starvation",
                "dispatch",
            )
        )
        | {d.key for d in HALO_SETTING_NUMBER_DEFS}
        | {d.key for d in HYBRID_SETTING_NUMBER_DEFS}
        | POWER_COMMAND_ICONS.keys()
        | {f"work_mode_{mode}" for mode in WORK_MODE_ICONS}
    )

    unique_ids_to_remove = {
        f"hyxi_{sn}_{key}" for sn in stale_sns for key in keys_to_remove
    }
    # Entry-scoped: one button per Modbus entry rather than per device SN
    # (see button.py's HyxiRefreshSettingsButton), so it isn't shaped like
    # the device-scoped ids above and needs its own entry here.
    if not enabled:
        unique_ids_to_remove.add(f"{entry.entry_id}_refresh_settings")

    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.unique_id in unique_ids_to_remove:
            _LOGGER.debug(
                "Removing control %s entity %s",
                reg_entry.domain,
                reg_entry.entity_id,
            )
            registry.async_remove(reg_entry.entity_id)


# The list of subscription codes earlier versions kept; HYXI's own
# subscription list replaces it.
_LEGACY_SUBSCRIPTION_STORE = "hyxi_cloud_subscriptions"


async def _async_remove_subscription_store(hass: HomeAssistant) -> None:
    """Delete the subscription code list earlier versions kept, if any."""
    from homeassistant.helpers.storage import Store

    await Store(hass, 1, _LEGACY_SUBSCRIPTION_STORE).async_remove()

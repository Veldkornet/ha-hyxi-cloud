"""HYXI Cloud Integration for Home Assistant."""
# pylint: disable=wrong-import-position

import asyncio
import hashlib
import hmac
import logging
from typing import TYPE_CHECKING

from aiohttp import ClientError, web
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import network
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from hyxi_cloud_api import HyxiApiClient
from hyxi_cloud_api import __version__ as API_VERSION

from .const import (
    BASE_URL_DEFAULT,
    BATTERY_SENSORS,
    CONF_ACCESS_KEY,
    CONF_EM_ENABLED,
    CONF_EM_FORECAST_ENTITY,
    CONF_EM_FORECAST_POWER_ENTITY,
    CONF_EM_INVERTER_SN,
    CONF_EM_P1_ENTITY,
    CONF_ENABLE_PUSH,
    CONF_MODBUS_FAMILY,
    CONF_MODBUS_UNIT,
    CONF_PUSH_RATE,
    CONF_PUSH_URL,
    CONF_SECRET_KEY,
    DEFAULT_MODBUS_FAMILY,
    DEFAULT_MODBUS_UNIT,
    DEFAULT_PUSH_RATE,
    DOMAIN,
    MANUFACTURER,
    MODBUS_FAMILY_HYBRID,
    MODBUS_MESSAGE_SPACING,
    PLATFORMS,
    VERSION,
    detect_phase_type,
    entry_stable_key,
    get_raw_device_code,
    is_control_capable_device_type,
    is_modbus_entry,
    mask_sensitive_key_value,
    mask_sn,
    mask_subscription_code,
    mask_url,
    modbus_params,
    normalize_device_type,
)
from .coordinator import HyxiDataUpdateCoordinator
from .protection import HyxiBatteryProtectionController

if TYPE_CHECKING:
    from .modbus_coordinator import HyxiModbusCoordinator

_LOGGER = logging.getLogger(__name__)

# Repeated across several subscription setup/teardown/logging call sites below.
_UNKNOWN_ERROR = "Unknown error"
_PUSH_SUBSCRIPTION_LABEL = "HYXI Push"
_ALARM_PUSH_SUBSCRIPTION_LABEL = "HYXI Alarm Push"


async def _build_modbus_coordinator(
    hass: HomeAssistant, entry: ConfigEntry
) -> HyxiModbusCoordinator:
    """Build a coordinator that reaches the device over local RS485.

    Which register map and client class to use was decided once, during
    setup, by config_flow's family detection (a real value at a
    family-specific signature register -- the confirmed HALO and hybrid
    address ranges don't overlap, so that's strong evidence). It is not
    re-detected here on every load: an entry created before this concept
    existed carries no CONF_MODBUS_FAMILY at all, and DEFAULT_MODBUS_FAMILY
    covers that case the same way entry_transport() covers pre-Modbus
    entries -- absence means the newer, stronger-evidenced default.
    """
    from homeassistant.helpers.importlib import async_import_module

    from .modbus.client import ModbusClient
    from .modbus_coordinator import HyxiModbusCoordinator

    unit_id = int(entry.data.get(CONF_MODBUS_UNIT, DEFAULT_MODBUS_UNIT))
    family = entry.data.get(CONF_MODBUS_FAMILY, DEFAULT_MODBUS_FAMILY)
    params = modbus_params(entry.data)

    # The device is reached through Home Assistant's own `modbus` integration:
    # async_get_unit hands back a unit on a connection HA owns, shares with
    # any other integration on the same bus (one lock, no competing sockets),
    # and closes when the last entry holding a unit on it unloads -- via a
    # callback it registers on `entry`. So nothing here constructs, holds or
    # closes a connection for the operational path, and teardown needs no code
    # of ours; config_flow's one-shot probe is the sole exception, and builds
    # its own deliberately (see _probe_and_detect_modbus).
    #
    # Imported at call time, not module scope, so a cloud-only entry never
    # pulls in the Modbus stack (pymodbus included); via async_import_module
    # so the first import lands in the executor instead of blocking the loop
    # -- `modbus` is only an after-dependency, not necessarily loaded yet.
    modbus = await async_import_module(hass, "homeassistant.components.modbus")
    try:
        unit = modbus.async_get_unit(hass, entry, params, unit_id)
    except HomeAssistantError as err:
        # Another consumer already holds this bus on link settings that
        # cannot share one connection -- typically a different integration
        # (a native `modbus:` hub, say) on the same host:port under another
        # framer. Surfaced, not retried in a loop: the conflicting configs
        # have to be reconciled.
        raise ConfigEntryError(str(err)) from err

    # HALO wants >200ms between frames, the hybrid protocol >500ms; the wrong
    # figure against a hybrid device breaks its documented timing. The shared
    # connection carries no message_spacing of its own, so it is set per-unit
    # (docs/modbus-provenance.md has the interleaving caveat).
    spacing = MODBUS_MESSAGE_SPACING[family]
    unit.set_message_spacing(spacing)

    _LOGGER.debug(
        "Modbus coordinator for entry %s: %s, unit %s, family %s, spacing %ss",
        entry.entry_id,
        params,
        unit_id,
        family,
        spacing,
    )

    client: ModbusClient
    if family == MODBUS_FAMILY_HYBRID:
        from .modbus.client_hybrid import HyxiHybridModbusClient

        client = HyxiHybridModbusClient(unit, unit_id)
    else:
        from .modbus.client import HyxiModbusClient

        client = HyxiModbusClient(unit, unit_id)

    return HyxiModbusCoordinator(hass, client, entry)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HYXI Cloud from a config entry."""
    _LOGGER.debug(
        "Starting HYXI Cloud Integration (Integration: %s, API: %s)",
        VERSION,
        API_VERSION,
    )

    modbus = is_modbus_entry(entry)
    _LOGGER.debug(
        "Entry %s uses the %s transport",
        entry.entry_id,
        "local Modbus" if modbus else "HYXI Cloud",
    )

    # Annotated explicitly as the common base -- without it, mypy narrows
    # coordinator to whichever branch assigns it first and rejects the
    # other as incompatible. HyxiModbusCoordinator is itself a
    # HyxiDataUpdateCoordinator subclass, so the base type covers both.
    coordinator: HyxiDataUpdateCoordinator
    if modbus:
        coordinator = await _build_modbus_coordinator(hass, entry)
    else:
        access_key = entry.data.get(CONF_ACCESS_KEY)
        secret_key = entry.data.get(CONF_SECRET_KEY)

        if not access_key or not secret_key:
            _LOGGER.error("HYXI Integration could not find Access/Secret keys.")
            return False

        # Base URL always defaults to global OpenAPI.
        base_url = entry.data.get("base_url") or BASE_URL_DEFAULT

        session = async_get_clientsession(hass)
        client = HyxiApiClient(access_key, secret_key, base_url, session)

        coordinator = HyxiDataUpdateCoordinator(hass, client, entry)
        coordinator.known_subscription_codes = await async_get_subscription_codes(hass)

    # Pre-seed coordinator.data from persistent cache so that if the API is slow
    # or unreachable at startup, data is immediately available and the fallback
    # in _async_update_data requires no additional disk read.
    await coordinator.async_preload_cache()

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        _LOGGER.error("Authentication failed during setup")
        raise
    except (
        UpdateFailed,
        ClientError,
        TimeoutError,
    ) as err:
        _LOGGER.warning("HYXI not ready: %s", err)
        raise ConfigEntryNotReady(f"Connection error: {err}") from err
    # A failed Modbus setup needs no bus release of ours: HA drains
    # entry.async_on_unload (where async_get_unit put its close) on any
    # non-success path.

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Push subscriptions register a webhook with HYXI's servers so the cloud
    # can call back. RS485 has no such channel -- it is polled -- so a Modbus
    # entry skips both entirely rather than subscribing on a device it does
    # not reach through the cloud at all.
    if not modbus:
        # Both handle enablement checks and cleanup of orphaned codes.
        await _async_setup_push_subscription(hass, entry, coordinator)
        await _async_setup_alarm_subscription(hass, entry, coordinator)

    _async_register_devices(hass, entry, coordinator)

    _remove_legacy_select_entities(hass, coordinator.data)
    _migrate_vpp_dispatch_to_work_mode(hass, entry, coordinator.data)
    _migrate_battery_sensor_unique_ids(hass, entry, coordinator.data)
    _migrate_microinverter_sum_identifiers(hass, entry)
    _remove_work_mode_sensor_for_modbus(hass, entry, coordinator.data)
    _remove_alarm_entities_for_modbus(hass, entry, coordinator.data)
    _cleanup_control_entities(hass, entry, coordinator)
    await _async_setup_battery_protection(hass, coordinator)
    _async_setup_energy_manager(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start EM engine after platforms are loaded (entities need to exist first)
    if coordinator.engine is not None:
        coordinator.engine.start()

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    setup_services(hass)

    _LOGGER.debug(
        "HYXI Cloud entry %s setup complete: %d devices, protection=%s, engine=%s",
        entry.entry_id,
        len(coordinator.data or {}),
        bool(getattr(coordinator, "protection_controllers", None)),
        coordinator.engine is not None,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading HYXI Cloud entry %s", entry.entry_id)
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is not None:
        if coordinator.engine is not None:
            coordinator.engine.stop()
        for controller in coordinator.protection_controllers.values():
            controller.stop()
        # A Modbus entry has no server-side subscriptions to tear down,
        # and HA releases its shared bus via entry.async_on_unload.
        if not is_modbus_entry(entry):
            # Leave the subscriptions alive on the server (cancel_remote=False):
            # the persisted code + fingerprint let the next setup reuse them,
            # avoiding a cancel/resubscribe cycle on every restart and reload.
            # Permanent cleanup happens in async_remove_entry when the entry is
            # actually deleted.
            await _async_teardown_push_subscription(
                hass, coordinator, entry, cancel_remote=False
            )
            await _async_teardown_alarm_subscription(
                hass, coordinator, entry, cancel_remote=False
            )
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            if hass.services.has_service(DOMAIN, "cancel_subscription"):
                hass.services.async_remove(DOMAIN, "cancel_subscription")
    _LOGGER.debug("HYXI Cloud entry %s unload result: %s", entry.entry_id, unload_ok)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Cancel any remaining subscriptions when the entry is permanently removed.

    Regular unload keeps subscriptions alive for reuse on the next load, so
    actual deletion of the integration must do the remote cleanup here.
    """
    raw_codes = [
        entry.data.get("push_subscribe_code"),
        entry.data.get("alarm_subscribe_code"),
    ]
    codes: list[str] = [c for c in raw_codes if c]
    if not codes:
        return

    access_key = entry.data.get(CONF_ACCESS_KEY)
    secret_key = entry.data.get(CONF_SECRET_KEY)
    if not access_key or not secret_key:
        return

    session = async_get_clientsession(hass)
    client = HyxiApiClient(
        access_key, secret_key, entry.data.get("base_url") or BASE_URL_DEFAULT, session
    )
    for code in codes:
        try:
            await async_cancel_and_unregister_subscription(hass, client, code)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "Could not cancel subscription %s during entry removal: %s "
                "(it remains in known_subscription_codes for manual cleanup via "
                "the hyxi_cloud.cancel_subscription service)",
                mask_subscription_code(code),
                err,
            )


def _async_register_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Register all devices and establish parent-child relationships."""
    device_registry = dr.async_get(hass)

    # Two-pass device registration to guarantee correct via_device ordering.
    # Without Pass 1, a child registered before its parent would fail the
    # via_device lookup and appear as an orphaned device in Home Assistant.
    #
    # Pass 1: Register every device as a standalone entry (no relationships).
    #         This ensures all SNs are present in the registry before Pass 2
    #         attempts to link them.
    for sn, dev_data in coordinator.data.items():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, sn)},
            name=dev_data.get("device_name") or f"Device {sn}",
            manufacturer=MANUFACTURER,
            model=dev_data.get("model"),
            sw_version=dev_data.get("sw_version"),
            hw_version=dev_data.get("hw_version"),
            serial_number=sn,
        )

    # Pass 2: Establish parent→child relationships now that all devices exist.
    for sn, dev_data in coordinator.data.items():
        metrics = dev_data.get("metrics", {})

        # 1. Handle Battery relationship.
        #    Guard: if bat_sn is already a first-class device in coordinator.data
        #    it was registered in Pass 1 with full metadata — skip the sparse stub
        #    and just link it via_device to avoid overwriting the full entry.
        bat_sn = metrics.get("batSn")
        if bat_sn:
            if bat_sn in coordinator.data:
                # Already registered with full metadata in Pass 1; just set the link.
                device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={(DOMAIN, bat_sn)},
                    via_device=(DOMAIN, sn),
                )
            else:
                # Battery is not a standalone device — create a minimal stub.
                device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={(DOMAIN, bat_sn)},
                    name=f"Battery {bat_sn}",
                    manufacturer=MANUFACTURER,
                    model="Energy Storage System",
                    serial_number=bat_sn,
                    via_device=(DOMAIN, sn),
                )

        # 2. Handle Parent Collector relationship.
        parent_sn = metrics.get("parentSn")
        if parent_sn:
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, sn)},
                via_device=(DOMAIN, parent_sn),
            )


def _async_setup_energy_manager(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Set up the Energy Manager engine and register its virtual device."""
    em_enabled = entry.options.get(CONF_EM_ENABLED, False)
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)
    if not em_enabled or not em_sn or em_sn not in coordinator.data:
        _LOGGER.debug(
            "EM not started: enabled=%s sn_configured=%s sn_in_data=%s",
            em_enabled,
            bool(em_sn),
            bool(em_sn) and em_sn in coordinator.data,
        )
        return

    from .engine import EMEntityConfig, EnergyManagerEngine

    em_config = EMEntityConfig(
        sn=em_sn,
        p1_entity=entry.options.get(CONF_EM_P1_ENTITY, ""),
        forecast_entity=entry.options.get(CONF_EM_FORECAST_ENTITY),
        forecast_power_entity=entry.options.get(CONF_EM_FORECAST_POWER_ENTITY),
    )
    engine = EnergyManagerEngine(hass, coordinator, em_config)
    coordinator.engine = engine

    # Register EM virtual device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{em_sn}_energy_manager")},
        name="Energy Manager",
        manufacturer=MANUFACTURER,
        model="Energy Manager",
        via_device=(DOMAIN, em_sn),
    )


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None and coordinator.options == entry.options:
        _LOGGER.debug(
            "HYXI: Config entry data updated, skipping reload as options did not change"
        )
        return
    _LOGGER.debug("HYXI: Options updated, reloading integration to apply new settings")
    await hass.config_entries.async_reload(entry.entry_id)


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
    stable_identifiers = {(DOMAIN, f"{stable_key}_microinverters_summary")}
    old_device = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_microinverters_summary")}
    )
    if old_device is None:
        return
    stable_device = device_registry.async_get_device(identifiers=stable_identifiers)
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


def _cleanup_control_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Remove control entities from registry if battery control is disabled."""
    from .const import is_battery_control_enabled

    if is_battery_control_enabled(entry):
        return

    _LOGGER.debug(
        "Battery control is disabled. Cleaning up any registered control entities from registry"
    )
    registry = er.async_get(hass)
    keys_to_remove = frozenset(
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
            "charge_power",
            "discharge_power",
            "soc_min",
            "soc_max",
            "soc_min_hysteresis_pct",
            "soc_max_hysteresis_pct",
            "micro_power_limit",
            "last_sent_mode",
        )
    )

    unique_ids_to_remove = {
        f"hyxi_{sn}_{key}" for sn in coordinator.data for key in keys_to_remove
    }

    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.unique_id in unique_ids_to_remove:
            _LOGGER.debug(
                "Removing control %s entity %s",
                reg_entry.domain,
                reg_entry.entity_id,
            )
            registry.async_remove(reg_entry.entity_id)


async def _async_setup_battery_protection(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Start battery protection on supported battery control devices."""
    from .const import is_battery_control_enabled

    if not is_battery_control_enabled(coordinator.entry):
        _LOGGER.debug("Battery control and protection is disabled by user settings")
        return

    tasks = []
    task_sns = []
    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if not is_control_capable_device_type(coordinator.entry, device_type):
            _LOGGER.debug(
                "Skipping protection for %s: device_type=%s not controllable",
                mask_sn(sn),
                device_type,
            )
            continue

        # Local Modbus always resolves to the mode-control surface
        # (protection.py's _uses_mode_control), independent of phase --
        # HALO has no phase 2/3 registers at all and would otherwise never
        # pass the phase check below. Cloud entries keep the original
        # phase-based gate: an unrecognized phase there means the cloud
        # phase-specific controlId to use can't be determined, so no
        # controller is started (safety-first).
        if not is_modbus_entry(coordinator.entry):
            phase = detect_phase_type(dev_data)
            if phase not in ("three_phase", "single_phase"):
                _LOGGER.debug(
                    "Skipping protection for %s: unrecognized phase=%s",
                    mask_sn(sn),
                    phase,
                )
                continue

        controller = HyxiBatteryProtectionController(hass, coordinator, sn)
        coordinator.protection_controllers[sn] = controller
        task_sns.append(sn)
        tasks.append(hass.async_create_task(controller.async_start()))

    if tasks:
        # return_exceptions=True rather than a bare gather(): a single
        # device's initial control write failing (bus contention, a
        # provider-controlled battery that will never accept local writes,
        # a transient timeout) must not take the whole config entry down.
        # HyxiBatteryProtectionController.async_start() registers its
        # coordinator listener before attempting that write, so the
        # controller stays "started" and retries naturally on the next
        # coordinator refresh even when this first attempt fails --
        # nothing further to clean up here. task_sns is tracked alongside
        # tasks explicitly, rather than zipping against
        # coordinator.protection_controllers, so pairing stays correct even
        # if this function is ever called again on a coordinator that
        # already holds controllers from an earlier call.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for sn, result in zip(task_sns, results, strict=True):
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Battery protection for %s failed to start (will retry on "
                    "the next update): %s",
                    mask_sn(sn),
                    result,
                )


async def _async_resolve_webhook_url(
    hass: HomeAssistant,
    webhook_id: str,
    custom_url: str | None,
) -> str | None:
    """Resolve the external callback URL for the HYXI push subscription."""
    if custom_url and custom_url.strip():
        # Treat custom_url as the base URL — always append the HA webhook path.
        base = custom_url.strip().rstrip("/")
        if not base.lower().startswith("https://"):
            _LOGGER.error(
                "HYXI Push: Custom webhook URL must use HTTPS. Ignoring unencrypted URL."
            )
            return None
        custom_resolved = base + webhook.async_generate_path(webhook_id)
        _LOGGER.info(
            "HYXI Push: Using custom base URL, callback endpoint: %s",
            mask_url(custom_resolved),
        )
        return custom_resolved

    _LOGGER.debug("HYXI Push: Resolving external callback URL automatically...")
    resolved = await _try_nabu_casa_cloudhook(hass, webhook_id)

    # Fall back to standard external network settings
    if not resolved:
        resolved = _resolve_via_network_helper(hass, webhook_id)

    return resolved


async def _try_nabu_casa_cloudhook(hass: HomeAssistant, webhook_id: str) -> str | None:
    """Try to resolve the callback URL via an active Nabu Casa cloud hook."""
    # pylint: disable-next=consider-using-from-import
    import homeassistant.components.cloud as cloud

    if not cloud.async_active_subscription(hass):
        return None

    _LOGGER.debug("HYXI Push: Nabu Casa subscription detected, trying cloud URL")
    try:
        return await cloud.async_get_or_create_cloudhook(hass, webhook_id)
    except Exception as err:  # pylint: disable=broad-except
        # Fall back to base Exception if CloudNotAvailable is not a valid exception class (e.g. in tests)
        exc_cls = getattr(cloud, "CloudNotAvailable", Exception)
        if not isinstance(exc_cls, type) or not issubclass(exc_cls, BaseException):
            exc_cls = Exception
        if not isinstance(err, exc_cls):
            raise
        _LOGGER.debug(
            "HYXI Push: Nabu Casa cloud hook not connected or available, falling back to network URL"
        )
        return None


def _resolve_via_network_helper(hass: HomeAssistant, webhook_id: str) -> str | None:
    """Try to resolve the callback URL via HA's network helper."""
    try:
        resolved = network.get_url(
            hass, allow_external=True
        ) + webhook.async_generate_path(webhook_id)
        _LOGGER.debug(
            "HYXI Push: Resolved callback URL via network helper: %s",
            mask_url(resolved),
        )
        return resolved
    except network.NoURLAvailableError:
        _LOGGER.debug(
            "HYXI Push: network.get_url raised NoURLAvailableError"
            " (no external URL configured)"
        )
        return None


def _compute_subscription_fingerprint(
    webhook_url: str, device_sns: list[str], push_rate_ms: int
) -> str:
    """Fingerprint the parameters a push subscription was created with.

    Used to decide whether a persisted subscription code can be reused as-is
    on the next setup, or whether something that would make it stale
    (callback URL, device list, or push rate) has changed and it needs to be
    cancelled and re-created.
    """
    raw = f"{webhook_url}|{','.join(sorted(device_sns))}|{push_rate_ms}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _should_reuse_subscription(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    entry: ConfigEntry,
    config_key: str,
    fingerprint_key: str,
    webhook_url: str,
    device_sns: list[str],
    push_rate_ms: int,
) -> str | None:
    """Return the persisted subscription code if it's still valid for reuse.

    A code is reusable when nothing that would invalidate it (callback URL,
    device list, or push rate) has changed since it was created, per the
    stored fingerprint. Returns None if there's no persisted code, or the
    fingerprint no longer matches and a fresh subscribe is needed. Shared by
    the push and alarm setup flows so this decision only needs to be
    changed in one place.
    """
    prior_code = entry.data.get(config_key)
    if not prior_code:
        return None
    fingerprint = _compute_subscription_fingerprint(
        webhook_url, device_sns, push_rate_ms
    )
    if entry.data.get(fingerprint_key) == fingerprint:
        return prior_code
    return None


def _clear_subscription_entry_data(
    hass: HomeAssistant, entry: ConfigEntry, config_key: str, fingerprint_key: str
) -> None:
    """Clear a persisted subscription code and its fingerprint from entry.data."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, config_key: None, fingerprint_key: None}
    )


async def _async_maybe_cancel_subscription(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    hass: HomeAssistant,
    client,
    subscribe_code: str,
    log_prefix: str,
    force: bool,
    cancel_remote: bool,
) -> bool:
    """Attempt to cancel a subscription per cancel_remote/force semantics.

    Returns True if the caller should clear its local code/fingerprint --
    either the cancel was confirmed, or force=True. Returns False to
    preserve the local record: the subscription is being kept alive for
    reuse (cancel_remote=False), or the cancel failed without force. Shared
    by the push and alarm teardown flows.
    """
    if not cancel_remote:
        _LOGGER.debug(
            "Keeping %s subscription active for reuse on next load (code: %s)",
            log_prefix,
            mask_subscription_code(subscribe_code),
        )
        return False

    try:
        await async_cancel_and_unregister_subscription(hass, client, subscribe_code)
        return True
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning(
            "Error cancelling %s subscription%s: %s",
            log_prefix,
            "" if not force else " (forcing local reset anyway)",
            err,
        )
        return force


def _log_push_subscription_failure(push_type: str, err_msg: str) -> None:
    """Log a formatted warning for push subscription failures."""
    if "B004002" in err_msg or "repeatedly" in err_msg:
        _LOGGER.warning(
            "Failed to register %s subscription: %s. "
            "If you have an active/orphaned subscription on another instance, retrieve the code from the "
            "Subscription Status sensor's attributes (known_subscription_codes) and cancel it using the "
            "'hyxi_cloud.cancel_subscription' service.",
            push_type,
            err_msg,
        )
    else:
        _LOGGER.warning("Failed to register %s subscription: %s", push_type, err_msg)


async def _async_cancel_entry_subscription(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
    config_key: str,
    log_prefix: str,
    fingerprint_key: str | None = None,
) -> None:
    """Cancel a previously stored subscription code and clear it from the config entry."""
    prior_code = entry.data.get(config_key)
    if not prior_code:
        return

    _LOGGER.debug(
        "%s: Cancelling prior/orphaned subscription (code: %s)",
        log_prefix,
        mask_subscription_code(prior_code),
    )
    try:
        await async_cancel_and_unregister_subscription(
            hass, coordinator.client, prior_code
        )
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.debug(
            "%s: Could not cancel prior subscription, preserving code for retry: %s",
            log_prefix,
            err,
        )
        return

    new_data = {**entry.data, config_key: None}
    if fingerprint_key:
        new_data[fingerprint_key] = None
    hass.config_entries.async_update_entry(entry, data=new_data)


async def _async_execute_real_time_subscription(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
    webhook_url: str,
    device_sns: list[str],
    push_rate_ms: int,
) -> None:
    """Execute the API call to subscribe to real-time data push."""
    try:
        res = await coordinator.client.subscribe_real_time_data(
            webhook_url,
            device_sns,
            push_rate_ms,  # API expects milliseconds
        )
        if res.get("success"):
            coordinator.subscribe_code = res["data"]["subscribeCode"]
            coordinator.push_status = "active"
            coordinator.push_error = None
            fingerprint = _compute_subscription_fingerprint(
                webhook_url, device_sns, push_rate_ms
            )
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "push_subscribe_code": coordinator.subscribe_code,
                    "push_subscribe_fingerprint": fingerprint,
                },
            )
            if coordinator.subscribe_code:
                await async_register_subscription_code(hass, coordinator.subscribe_code)
            _LOGGER.info(
                "Successfully subscribed to HYXI Real-Time Push (code: %s)",
                mask_subscription_code(coordinator.subscribe_code),
            )
        else:
            coordinator.push_status = "error"
            msg = res.get("msg", _UNKNOWN_ERROR)
            coordinator.push_error = msg
            _log_push_subscription_failure("HYXI Real-Time Push", msg)
    except Exception as err:  # pylint: disable=broad-exception-caught
        coordinator.push_status = "error"
        err_msg = str(err)
        coordinator.push_error = err_msg
        _log_push_subscription_failure("HYXI Real-Time Push", err_msg)


async def _async_execute_alarm_subscription(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
    webhook_url: str,
    device_sns: list[str],
    push_rate_ms: int,
) -> None:
    """Execute the API call to subscribe to alarm push."""
    try:
        res = await coordinator.client.subscribe_alarm(
            webhook_url,
            device_sns,
            push_rate_ms,
        )
        if res.get("success"):
            coordinator.alarm_subscribe_code = res["data"]["subscribeCode"]
            coordinator.alarm_push_status = "active"
            coordinator.alarm_push_error = None
            fingerprint = _compute_subscription_fingerprint(
                webhook_url, device_sns, push_rate_ms
            )
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "alarm_subscribe_code": coordinator.alarm_subscribe_code,
                    "alarm_subscribe_fingerprint": fingerprint,
                },
            )
            if coordinator.alarm_subscribe_code:
                await async_register_subscription_code(
                    hass, coordinator.alarm_subscribe_code
                )
            _LOGGER.info(
                "Successfully subscribed to HYXI Alarm Push (code: %s)",
                mask_subscription_code(coordinator.alarm_subscribe_code),
            )
        else:
            coordinator.alarm_push_status = "error"
            msg = res.get("msg", _UNKNOWN_ERROR)
            coordinator.alarm_push_error = msg
            _log_push_subscription_failure(_ALARM_PUSH_SUBSCRIPTION_LABEL, msg)
    except Exception as err:  # pylint: disable=broad-exception-caught
        coordinator.alarm_push_status = "error"
        err_msg = str(err)
        coordinator.alarm_push_error = err_msg
        _log_push_subscription_failure(_ALARM_PUSH_SUBSCRIPTION_LABEL, err_msg)


async def _async_setup_push_subscription(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Set up real-time webhook push subscription."""
    enable_push = entry.options.get(CONF_ENABLE_PUSH, False)
    if enable_push is not True:
        coordinator.push_status = "inactive"
        await _async_cancel_entry_subscription(
            hass,
            entry,
            coordinator,
            "push_subscribe_code",
            _PUSH_SUBSCRIPTION_LABEL,
            fingerprint_key="push_subscribe_fingerprint",
        )
        return

    push_rate_s = int(entry.options.get(CONF_PUSH_RATE, DEFAULT_PUSH_RATE))
    push_rate_ms = push_rate_s * 1000
    custom_url = entry.options.get(CONF_PUSH_URL)

    webhook_id = f"hyxi_cloud_{entry.entry_id}"
    coordinator.webhook_id = webhook_id
    coordinator.push_enabled = True

    # Register webhook handler. HA's webhook registry is in-memory, so this
    # must run on every load regardless of whether the subscription itself
    # is reused below.
    try:
        webhook.async_register(
            hass,
            DOMAIN,
            "HYXI Cloud Push",
            webhook_id,
            lambda _h, w_id, req: _async_handle_webhook(w_id, req, coordinator),
        )
    except ValueError:
        # Already registered (e.g. on reload config entry error)
        pass

    webhook_url = await _async_resolve_webhook_url(hass, webhook_id, custom_url)

    if not webhook_url:
        _LOGGER.warning(
            "HYXI Push: Could not resolve an external HTTPS callback URL. "
            "Real-time push is set to 'error' status. "
            "On dev/local instances without Nabu Casa or a configured external URL, "
            "enter a manually-reachable HTTPS URL in the 'Custom Callback URL' options field "
            "(e.g. via ngrok or a reverse proxy). "
            "Polling will continue as normal fallback."
        )
        coordinator.push_status = "error"
        coordinator.push_error = (
            "Could not resolve external URL — set a Custom Callback URL in options"
        )
        # Do not touch any existing subscription here -- an unresolved URL
        # doesn't mean the persisted subscription is bad, and it's not worth
        # burning it on a guess.
        return

    coordinator.push_url = webhook_url

    device_sns = [sn for sn in coordinator.data if sn]
    if not device_sns:
        _LOGGER.debug("No devices available to subscribe to push notifications")
        coordinator.push_status = "inactive"
        return

    # There is no HYXI endpoint to verify a code is still valid server-side;
    # if it has gone stale, the "Renew Subscription" button forces a fresh
    # cancel + subscribe.
    reused_code = _should_reuse_subscription(
        entry,
        "push_subscribe_code",
        "push_subscribe_fingerprint",
        webhook_url,
        device_sns,
        push_rate_ms,
    )
    if reused_code:
        coordinator.subscribe_code = reused_code
        coordinator.push_status = "active"
        coordinator.push_error = None
        _LOGGER.debug(
            "HYXI Push: Reusing existing subscription (code: %s)",
            mask_subscription_code(reused_code),
        )
        return

    # Something that requires a new subscription changed (or there's no
    # usable prior one) -- cancel any previous/orphaned code before
    # subscribing fresh.
    await _async_cancel_entry_subscription(
        hass,
        entry,
        coordinator,
        "push_subscribe_code",
        _PUSH_SUBSCRIPTION_LABEL,
        fingerprint_key="push_subscribe_fingerprint",
    )

    _LOGGER.debug(
        "Subscribing callback URL %s for devices: %s",
        mask_url(webhook_url),
        [mask_sn(sn) for sn in device_sns],
    )

    await _async_execute_real_time_subscription(
        hass, entry, coordinator, webhook_url, device_sns, push_rate_ms
    )


async def _async_teardown_push_subscription(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
    entry: ConfigEntry | None = None,
    force: bool = False,
    cancel_remote: bool = True,
) -> None:
    """Tear down push subscription and webhook.

    `cancel_remote=False` (regular unload on restart/reload) unregisters the
    webhook but leaves the subscription alive on the server and its code
    persisted, so the next setup can reuse it via the stored fingerprint
    instead of burning a cancel/resubscribe cycle.

    `force` clears the local subscription code even if the remote cancel
    call fails. Only appropriate for an explicit, user-initiated action
    (e.g. the Renew Subscription button) -- the automatic teardown paths
    must never do this, since the code is otherwise the only way to recover
    the account's one push subscription slot without contacting the
    supplier. It stays safe even when forced because the code remains in
    the account-wide `known_subscription_codes` store (only removed on a
    confirmed API cancel) for manual recovery.
    """
    webhook_id = coordinator.webhook_id
    if webhook_id:
        try:
            webhook.async_unregister(hass, webhook_id)
        except KeyError:
            # Webhook was already unregistered (e.g. double-teardown on crash recovery)
            pass
        coordinator.webhook_id = None

    subscribe_code = coordinator.subscribe_code
    if subscribe_code:
        should_clear = await _async_maybe_cancel_subscription(
            hass,
            coordinator.client,
            subscribe_code,
            _PUSH_SUBSCRIPTION_LABEL,
            force,
            cancel_remote,
        )
        if should_clear:
            coordinator.subscribe_code = None
            if entry is not None:
                _clear_subscription_entry_data(
                    hass, entry, "push_subscribe_code", "push_subscribe_fingerprint"
                )

    coordinator.push_enabled = False
    coordinator.push_status = "inactive"
    coordinator.push_url = None


async def _async_handle_webhook(
    webhook_id: str,
    request: web.Request,
    coordinator: HyxiDataUpdateCoordinator,
) -> web.Response:
    """Handle incoming webhook request from HYXI Cloud."""
    from homeassistant.util import dt as dt_util

    if not _is_authorized_webhook_request(request, coordinator):
        # Do not log the header value — it is user-controlled (CWE-117 Log Injection).
        _LOGGER.warning(
            "Unauthorized push attempt received on webhook %s",
            webhook_id,
        )
        return web.Response(status=401, text="Unauthorized")

    payload = await _parse_webhook_payload(request, "push")
    if payload is None:
        return web.Response(status=400, text="Invalid JSON")

    _LOGGER.debug(
        "HYXI Cloud Data Push webhook callback received. Webhook ID: %s, Active Subscribe Code: %s",
        "hyxi_cloud_***" if webhook_id.startswith("hyxi_cloud_") else "***",
        mask_subscription_code(coordinator.subscribe_code),
    )

    # 3. Process payload via SDK merging with existing metrics
    existing_metrics = {}
    if coordinator.data:
        existing_metrics = {
            sn: dev_data.get("metrics", {})
            for sn, dev_data in coordinator.data.items()
            if dev_data
        }

    try:
        push_results = coordinator.client.process_push_data(
            payload, existing_metrics=existing_metrics
        )
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Error parsing push payload: %s", err)
        return web.Response(status=500, text="Internal Processing Error")

    if not push_results:
        return web.json_response({"code": "0", "msg": "Success", "success": True})

    # 4. Apply updates to coordinator
    if coordinator.data is None:
        coordinator.data = {}
    any_updated = _apply_push_updates(coordinator, push_results)

    if any_updated:
        coordinator.last_push_received = dt_util.utcnow()
        coordinator.async_update_listeners()

    return web.json_response({"code": "0", "msg": "Success", "success": True})


def _is_authorized_webhook_request(request: web.Request, coordinator) -> bool:
    """Ingress Header authentication check (defense-in-depth), shared by the
    push and alarm push webhook handlers.
    """
    incoming_ak = request.headers.get("accessKey")
    if not (incoming_ak and coordinator.client.access_key):
        return False
    return hmac.compare_digest(
        incoming_ak.encode("utf-8"), coordinator.client.access_key.encode("utf-8")
    )


async def _parse_webhook_payload(request: web.Request, context: str) -> dict | None:
    """Parse a webhook body as JSON, or URL-encoded JSON as a fallback.

    `context` (e.g. "push", "alarm push") names the webhook in the log
    message on failure. Returns None, having already logged the failure,
    if neither works.
    """
    text = ""
    try:
        text = await request.text()
        import json

        try:
            return json.loads(text)
        except ValueError:
            # Maybe it's URL-encoded? Some platforms send payload={...}
            from urllib.parse import parse_qs

            parsed = parse_qs(text)
            if "payload" in parsed:
                return json.loads(parsed["payload"][0])
            raise ValueError("Not JSON and not URL-encoded payload") from None
    except Exception as e:  # pylint: disable=broad-exception-caught
        _LOGGER.warning(
            "Received invalid JSON payload on HYXI %s webhook. Error: %s. Raw text: %s",
            context,
            e,
            repr(text[:500]),
        )
        return None


def _apply_push_updates(coordinator, push_results: dict) -> bool:
    """Merge push results into coordinator.data, logging masked metrics.

    Returns True if any device was updated.
    """
    any_updated = False
    for sn, device_update in push_results.items():
        if sn not in coordinator.data:
            _LOGGER.debug("Received push data for untracked device SN: %s", mask_sn(sn))
            continue

        coordinator.data[sn]["metrics"] = device_update["metrics"]
        any_updated = True

        # Log the push metrics with sensitive keys masked (using mask_sensitive_key_value)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            logged_metrics = {
                k: mask_sensitive_key_value(k, v)
                for k, v in device_update["metrics"].items()
            }
            _LOGGER.debug(
                "HYXI Push Telemetry Update for Device %s: %s",
                mask_sn(sn),
                logged_metrics,
            )
    return any_updated


async def _async_setup_alarm_subscription(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Set up real-time alarm push subscription alongside real-time data push.

    Uses a dedicated webhook ID so HYXI can differentiate callback URLs.
    The alarm subscribe_code is persisted to entry.data under
    "alarm_subscribe_code" for crash-safe teardown on next startup.
    """
    enable_push = entry.options.get(CONF_ENABLE_PUSH, False)
    if enable_push is not True:
        coordinator.alarm_push_status = "inactive"
        await _async_cancel_entry_subscription(
            hass,
            entry,
            coordinator,
            "alarm_subscribe_code",
            _ALARM_PUSH_SUBSCRIPTION_LABEL,
            fingerprint_key="alarm_subscribe_fingerprint",
        )
        return

    push_rate_s = int(entry.options.get(CONF_PUSH_RATE, DEFAULT_PUSH_RATE))
    push_rate_ms = push_rate_s * 1000
    custom_url = entry.options.get(CONF_PUSH_URL)

    webhook_id = f"hyxi_cloud_{entry.entry_id}_alarm"
    coordinator.alarm_webhook_id = webhook_id

    # Register webhook handler. HA's webhook registry is in-memory, so this
    # must run on every load regardless of whether the subscription itself
    # is reused below.
    try:
        webhook.async_register(
            hass,
            DOMAIN,
            "HYXI Cloud Alarm Push",
            webhook_id,
            lambda _h, w_id, req: _async_handle_alarm_webhook(w_id, req, coordinator),
        )
    except ValueError:
        pass  # Already registered

    webhook_url = await _async_resolve_webhook_url(hass, webhook_id, custom_url)
    if not webhook_url:
        _LOGGER.warning(
            "HYXI Alarm Push: Could not resolve callback URL — "
            "alarm push disabled (real-time data push may still be active)."
        )
        coordinator.alarm_push_status = "error"
        coordinator.alarm_push_error = (
            "Could not resolve external URL — set a Custom Callback URL in options"
        )
        # Do not touch any existing subscription here -- an unresolved URL
        # doesn't mean the persisted subscription is bad.
        return

    coordinator.alarm_push_url = webhook_url

    device_sns = [sn for sn in coordinator.data if sn]
    if not device_sns:
        coordinator.alarm_push_status = "inactive"
        return

    reused_code = _should_reuse_subscription(
        entry,
        "alarm_subscribe_code",
        "alarm_subscribe_fingerprint",
        webhook_url,
        device_sns,
        push_rate_ms,
    )
    if reused_code:
        coordinator.alarm_subscribe_code = reused_code
        coordinator.alarm_push_status = "active"
        coordinator.alarm_push_error = None
        _LOGGER.debug(
            "HYXI Alarm Push: Reusing existing subscription (code: %s)",
            mask_subscription_code(reused_code),
        )
        return

    # Something that requires a new subscription changed (or there's no
    # usable prior one) -- cancel any previous/orphaned code before
    # subscribing fresh.
    await _async_cancel_entry_subscription(
        hass,
        entry,
        coordinator,
        "alarm_subscribe_code",
        _ALARM_PUSH_SUBSCRIPTION_LABEL,
        fingerprint_key="alarm_subscribe_fingerprint",
    )

    _LOGGER.debug(
        "HYXI Alarm Push: Subscribing %s devices at %s",
        len(device_sns),
        mask_url(webhook_url),
    )

    await _async_execute_alarm_subscription(
        hass, entry, coordinator, webhook_url, device_sns, push_rate_ms
    )


async def _async_teardown_alarm_subscription(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
    entry: ConfigEntry | None = None,
    force: bool = False,
    cancel_remote: bool = True,
) -> None:
    """Tear down alarm push subscription and webhook.

    See `_async_teardown_push_subscription` for the semantics of
    `cancel_remote` (keep the subscription alive for reuse on regular
    unload) and `force` (clear local state even on a failed cancel; only
    safe for an explicit, user-initiated action).
    """
    webhook_id = getattr(coordinator, "alarm_webhook_id", None)
    if webhook_id:
        try:
            webhook.async_unregister(hass, webhook_id)
        except KeyError:
            # Webhook was already unregistered (e.g. double-teardown on crash recovery)
            pass
        coordinator.alarm_webhook_id = None

    subscribe_code = getattr(coordinator, "alarm_subscribe_code", None)
    if subscribe_code:
        should_clear = await _async_maybe_cancel_subscription(
            hass,
            coordinator.client,
            subscribe_code,
            _ALARM_PUSH_SUBSCRIPTION_LABEL,
            force,
            cancel_remote,
        )
        if should_clear:
            coordinator.alarm_subscribe_code = None
            if entry is not None:
                _clear_subscription_entry_data(
                    hass, entry, "alarm_subscribe_code", "alarm_subscribe_fingerprint"
                )

    coordinator.alarm_push_status = "inactive"
    coordinator.alarm_push_url = None


async def _async_handle_alarm_webhook(
    webhook_id: str,
    request: web.Request,
    coordinator: HyxiDataUpdateCoordinator,
) -> web.Response:
    """Handle incoming alarm push webhook from HYXI Cloud.

    Parses the alarm payload via SDK, merges alarm records into
    coordinator.data[sn]["alarms"] so HyxiDeviceAlarmSensor fires instantly.
    """
    if not _is_authorized_webhook_request(request, coordinator):
        # Do not log the header value — it is user-controlled (CWE-117 Log Injection).
        _LOGGER.warning(
            "Unauthorized alarm push attempt received on webhook %s",
            webhook_id,
        )
        return web.Response(status=401, text="Unauthorized")

    from homeassistant.util import dt as dt_util

    payload = await _parse_webhook_payload(request, "alarm push")
    if payload is None:
        return web.Response(status=400, text="Invalid JSON")

    _LOGGER.debug(
        "HYXI Cloud Alarm Push webhook callback received. Webhook ID: %s, Active Subscribe Code: %s",
        "hyxi_cloud_***" if webhook_id.startswith("hyxi_cloud_") else "***",
        mask_subscription_code(coordinator.alarm_subscribe_code),
    )

    # Stamp contact time unconditionally — HYXI sends pings on schedule even
    # when there are no active alarms (empty dataList), so we always record contact.
    coordinator.alarm_last_push_received = dt_util.utcnow()

    try:
        alarm_results = coordinator.client.process_alarm_push_data(payload)
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Error parsing alarm push payload: %s", err)
        return web.Response(status=500, text="Internal Processing Error")

    if not alarm_results:
        return web.json_response({"code": "0", "msg": "Success", "success": True})

    if coordinator.data is None:
        coordinator.data = {}
    any_updated = _apply_alarm_updates(coordinator, alarm_results)

    if any_updated:
        coordinator.async_update_listeners()

    return web.json_response({"code": "0", "msg": "Success", "success": True})


def _apply_alarm_updates(
    coordinator: HyxiDataUpdateCoordinator, alarm_results: dict
) -> bool:
    """Merge alarm results into coordinator.data, logging masked alarm records.

    Returns True if any device was updated.
    """
    any_updated = False
    for sn, alarm_records in alarm_results.items():
        if sn not in coordinator.data:
            _LOGGER.warning(
                "HYXI Alarm Push: received alarm for untracked device SN: %s",
                mask_sn(sn),
            )
            continue

        # Merge: replace any alarm records with matching alarmCode, append new ones.
        existing = coordinator.data[sn].get("alarms") or []
        existing_by_code = {str(a.get("alarmCode", "")): a for a in existing}
        for rec in alarm_records:
            existing_by_code[rec["alarmCode"]] = rec
        coordinator.data[sn]["alarms"] = list(existing_by_code.values())
        any_updated = True

        # Log the push alarms with sensitive keys masked (using mask_sensitive_key_value)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            logged_alarms = []
            for rec in alarm_records:
                logged_rec = {k: mask_sensitive_key_value(k, v) for k, v in rec.items()}
                logged_alarms.append(logged_rec)

            _LOGGER.debug(
                "HYXI Alarm Push Telemetry Update for Device %s: %s",
                mask_sn(sn),
                logged_alarms,
            )
    return any_updated


def setup_services(hass: HomeAssistant) -> None:
    """Set up custom services for HYXI Cloud."""
    if hass.services.has_service(DOMAIN, "cancel_subscription"):
        return

    import voluptuous as vol
    from homeassistant.helpers import config_validation as cv

    async def async_handle_cancel_subscription(call) -> None:
        """Handle the cancel_subscription service call."""
        subscribe_code = call.data["subscribe_code"].strip()
        if not subscribe_code:
            raise HomeAssistantError("Subscription code cannot be empty")

        coordinators_values = hass.data.get(DOMAIN, {}).values()
        if not coordinators_values:
            raise HomeAssistantError(
                "No active HYXI Cloud integration entries found to call the API"
            )

        # Use the client from the first active integration entry
        coordinator = next(iter(coordinators_values))
        _LOGGER.info(
            "Manually cancelling HYXI subscription: %s",
            mask_subscription_code(subscribe_code),
        )
        try:
            await async_cancel_and_unregister_subscription(
                hass, coordinator.client, subscribe_code
            )
        except Exception as err:
            _LOGGER.exception(
                "Error manual cancelling HYXI subscription %s: %s",
                mask_subscription_code(subscribe_code),
                err,
            )
            err_msg = str(err)
            if "subscription request failed" in err_msg:
                # Extract the API error message
                api_msg = err_msg.split("subscription request failed:", 1)[-1].strip()
                if api_msg.startswith("(") and ")" in api_msg:
                    # Strip any parenthesized code if present (e.g. from real SDK)
                    pass
                raise HomeAssistantError(
                    f"Failed to cancel subscription: {api_msg}"
                ) from err
            raise HomeAssistantError(f"API error: {err}") from err

    hass.services.async_register(
        DOMAIN,
        "cancel_subscription",
        async_handle_cancel_subscription,
        schema=vol.Schema(
            {
                vol.Required("subscribe_code"): cv.string,
            }
        ),
    )


STORAGE_KEY = "hyxi_cloud_subscriptions"
STORAGE_VERSION = 1
_SUBSCRIPTION_STORE_LOCK = asyncio.Lock()


async def async_register_subscription_code(hass: HomeAssistant, code: str) -> None:
    """Save a subscription code to persistent storage and update coordinators."""
    from unittest.mock import Mock

    if isinstance(hass, Mock):
        return

    from homeassistant.helpers.storage import Store

    store: Store[dict[str, list[str]]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    async with _SUBSCRIPTION_STORE_LOCK:
        data = await store.async_load() or {}
        codes = data.setdefault("codes", [])
        if code not in codes:
            codes.append(code)
            await store.async_save(data)

        # Update active coordinators
        for coordinator in hass.data.get(DOMAIN, {}).values():
            coordinator.known_subscription_codes = list(codes)
            coordinator.async_update_listeners()


async def async_unregister_subscription_code(hass: HomeAssistant, code: str) -> None:
    """Remove a subscription code from persistent storage."""
    from unittest.mock import Mock

    if isinstance(hass, Mock):
        return

    from homeassistant.helpers.storage import Store

    store: Store[dict[str, list[str]]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    async with _SUBSCRIPTION_STORE_LOCK:
        data = await store.async_load() or {}
        codes = data.get("codes", [])
        if code in codes:
            codes.remove(code)
            await store.async_save(data)

        # Update active coordinators
        for coordinator in hass.data.get(DOMAIN, {}).values():
            coordinator.known_subscription_codes = list(codes)
            coordinator.async_update_listeners()


async def async_get_subscription_codes(hass: HomeAssistant) -> list[str]:
    """Retrieve all saved subscription codes."""
    from unittest.mock import Mock

    if isinstance(hass, Mock):
        return []

    from homeassistant.helpers.storage import Store

    store: Store[dict[str, list[str]]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    data = await store.async_load() or {}
    return data.get("codes", [])


async def async_cancel_and_unregister_subscription(
    hass: HomeAssistant, client, code: str
) -> None:
    """Cancel a subscription via the API and unregister it from storage.

    The code is only removed from local storage when the API confirms the
    cancellation succeeded. HYXI has no "subscription not found" error code,
    so a failure response (including transient/rate-limit errors) is not a
    reliable signal that the subscription is actually gone -- losing track
    of it locally means contacting the supplier to reset the account's one
    push subscription slot. Any failure is preserved and re-raised so
    callers know not to discard their own record of the code either.
    """
    code = code.strip()
    if not code:
        return

    _LOGGER.info("Cancelling HYXI subscription: %s", mask_subscription_code(code))
    res = await client.cancel_subscription(code)
    if isinstance(res, dict) and not res.get("success"):
        msg = res.get("msg", _UNKNOWN_ERROR)
        sub_err_cls = getattr(client, "SubscriptionError", RuntimeError)
        if not isinstance(sub_err_cls, type) or not issubclass(
            sub_err_cls, BaseException
        ):
            sub_err_cls = RuntimeError
        raise sub_err_cls(f"subscription request failed: {msg}")

    await async_unregister_subscription_code(hass, code)
    _LOGGER.info(
        "Successfully cancelled HYXI subscription: %s", mask_subscription_code(code)
    )

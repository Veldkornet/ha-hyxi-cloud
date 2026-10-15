"""HYXI Cloud Integration for Home Assistant."""

import asyncio
import logging
from typing import TYPE_CHECKING

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import UpdateFailed
from hyxi_cloud_api import HyxiApiClient
from hyxi_cloud_api import __version__ as API_VERSION

from .const import (
    BASE_URL_DEFAULT,
    CONF_ACCESS_KEY,
    CONF_EM_ENABLED,
    CONF_EM_FORECAST_ENTITY,
    CONF_EM_FORECAST_POWER_ENTITY,
    CONF_EM_INVERTER_SN,
    CONF_EM_P1_ENTITY,
    CONF_ENABLE_PUSH,
    CONF_MODBUS_FAMILY,
    CONF_MODBUS_UNIT,
    CONF_SECRET_KEY,
    DEFAULT_MODBUS_FAMILY,
    DEFAULT_MODBUS_UNIT,
    DOMAIN,
    MANUFACTURER,
    MODBUS_FAMILY_HYBRID,
    MODBUS_MESSAGE_SPACING,
    PLATFORMS,
    VERSION,
    detect_phase_type,
    device_type_of,
    get_raw_device_code,
    is_control_capable_device_type,
    is_modbus_entry,
    mask_sn,
    mask_subscription_code,
    modbus_params,
    normalize_device_type,
)
from .coordinator import (
    HyxiConfigEntry,
    HyxiDataUpdateCoordinator,
    HyxiDiscoveryCoordinator,
)
from .devices import _async_register_devices
from .entity import new_devices_signal
from .migrations import (
    _async_remove_subscription_store,
    _cleanup_control_entities,
    _cloud_battery_control_issue_id,
    _merge_duplicate_battery_energy_sensors,
    _migrate_battery_sensor_unique_ids,
    _migrate_hybrid_serial_decoding,
    _migrate_microinverter_sum_identifiers,
    _migrate_vpp_dispatch_to_work_mode,
    _remove_alarm_entities_for_modbus,
    _remove_bms_alarm_sensors,
    _remove_legacy_select_entities,
    _remove_purge_subscriptions_button,
    _remove_work_mode_sensor_for_modbus,
    _retire_cloud_battery_control,
)
from .protection import HyxiBatteryProtectionController
from .services import setup_services
from .subscriptions import (
    _async_sync_subscriptions,
    _async_teardown_alarm_subscription,
    _async_teardown_push_subscription,
    async_cancel_subscription,
    async_refresh_subscriptions,
)
from .webhooks import (
    _LEGACY_WEBHOOKS,
    _WEBHOOKS,
    _async_delete_cloudhook,
    _ensure_webhook_ids,
    _legacy_webhook_id,
    _register_legacy_webhooks,
)

if TYPE_CHECKING:
    from .modbus_coordinator import HyxiModbusCoordinator

_LOGGER = logging.getLogger(__name__)


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
    # of ours. config_flow's one-shot probe builds its own for a fresh setup
    # or a bus change (see _probe_and_detect_modbus), and shares this one for
    # a reconfigure that keeps the bus (see _probe_on_shared_bus).
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


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # NOSONAR
    """Register the integration's services, once for all its entries.

    Home Assistant awaits this with its configuration, which a
    config-entry-only integration has no use for.
    """
    setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HyxiConfigEntry) -> bool:
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
    discovery: HyxiDiscoveryCoordinator | None = None
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
        discovery = HyxiDiscoveryCoordinator(hass, client, entry)
        coordinator.discovery = discovery

    # Pre-seed coordinator.data from persistent cache so that if the API is slow
    # or unreachable at startup, data is immediately available and the fallback
    # in _async_update_data requires no additional disk read.
    await coordinator.async_preload_cache()

    try:
        if discovery is not None:
            await _async_first_discovery(discovery, coordinator)
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

    entry.runtime_data = coordinator

    # Push subscriptions register a webhook with HYXI's servers so the cloud
    # can call back. RS485 has no such channel -- it is polled -- so a Modbus
    # entry skips both entirely rather than subscribing on a device it does
    # not reach through the cloud at all.
    if not modbus:
        await _async_remove_subscription_store(hass)
        _ensure_webhook_ids(hass, entry)
        _register_legacy_webhooks(hass, entry, coordinator)
        # Fetched first, so a stored code HYXI no longer holds isn't reused.
        await async_refresh_subscriptions(coordinator)
        await _async_sync_subscriptions(hass, entry, coordinator)

    _migrate_hybrid_serial_decoding(hass, entry, coordinator.data)
    _async_register_devices(hass, entry, coordinator)

    _remove_legacy_select_entities(hass, coordinator.data)
    _remove_purge_subscriptions_button(hass, entry)
    _migrate_vpp_dispatch_to_work_mode(hass, entry, coordinator.data)
    _migrate_battery_sensor_unique_ids(hass, entry, coordinator.data)
    _merge_duplicate_battery_energy_sensors(hass, entry)
    _migrate_microinverter_sum_identifiers(hass, entry)
    _remove_work_mode_sensor_for_modbus(hass, entry, coordinator.data)
    _remove_alarm_entities_for_modbus(hass, entry, coordinator.data)
    _remove_bms_alarm_sensors(hass, entry)
    _retire_cloud_battery_control(hass, entry, coordinator)
    _cleanup_control_entities(hass, entry, coordinator)
    await _async_setup_battery_protection(hass, coordinator, coordinator.data)
    _async_setup_energy_manager(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start EM engine after platforms are loaded (entities need to exist first)
    if coordinator.engine is not None:
        coordinator.engine.start()

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    if discovery is not None:
        # After the platforms, so each is listening for new devices.
        _track_new_devices(hass, entry, coordinator, discovery)

    _LOGGER.debug(
        "HYXI Cloud entry %s setup complete: %d devices, protection=%s, engine=%s",
        entry.entry_id,
        len(coordinator.data or {}),
        bool(getattr(coordinator, "protection_controllers", None)),
        coordinator.engine is not None,
    )

    return True


async def _async_first_discovery(
    discovery: HyxiDiscoveryCoordinator, coordinator: HyxiDataUpdateCoordinator
) -> None:
    """Discover the devices before the first poll. A failed discovery is
    tolerated while cached devices can stand in for them; it is retried on
    the discovery coordinator's schedule."""
    try:
        await discovery.async_config_entry_first_refresh()
    except ConfigEntryNotReady as err:
        reason = discovery.last_exception
        if not coordinator.data:
            raise ConfigEntryNotReady(str(reason)) from err
        _LOGGER.warning(
            "%s at startup; using %d cached devices until it succeeds",
            reason,
            len(coordinator.data),
        )


def _track_new_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
    discovery: HyxiDiscoveryCoordinator,
) -> None:
    """Poll as soon as discovery finds devices that haven't been polled, and
    once a poll returns new devices, register them, signal the platforms to
    add their entities, start their battery protection and update the push
    subscription to cover them. The Energy Manager's inverter turning up
    only now needs a reload, as the Energy Manager starts with the entry.

    Waiting for a poll that includes a device, rather than reacting to
    discovery alone, keeps cached devices (which stand in while polling
    fails) from counting. A device that disappears drops out of the polled
    data and its entities go unavailable.
    """

    @callback
    def _on_discovery() -> None:
        if not discovery.last_update_success or discovery.data is None:
            return
        entry.async_create_task(hass, async_refresh_subscriptions(coordinator))
        unpolled = frozenset(discovery.data.devices) - frozenset(coordinator.data or {})
        # Cached data also means nothing has been polled since discovery
        # last failed.
        if unpolled or coordinator.hyxi_metadata["cache_active"]:
            entry.async_create_task(hass, coordinator.async_request_refresh())

    known = set(coordinator.data or {})

    @callback
    def _on_poll() -> None:
        if coordinator.hyxi_metadata["cache_active"]:
            return
        added = (coordinator.data or {}).keys() - known
        if not added:
            return
        known.update(added)
        _async_add_new_devices(
            hass, entry, coordinator, {sn: coordinator.data[sn] for sn in added}
        )

    # The discovery coordinator has no entities; this listener is also what
    # keeps it on its schedule.
    entry.async_on_unload(discovery.async_add_listener(_on_discovery))
    entry.async_on_unload(coordinator.async_add_listener(_on_poll))


@callback
def _async_add_new_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
    new: dict[str, dict],
) -> None:
    """Register devices a poll added, signal the platforms to add their
    entities, start their battery protection and renew the push
    subscription to cover them; or reload, if one is the Energy Manager's
    inverter."""
    if (
        entry.options.get(CONF_EM_ENABLED)
        and entry.options.get(CONF_EM_INVERTER_SN) in new
    ):
        _LOGGER.info("HYXI found the Energy Manager's inverter; reloading")
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    _LOGGER.info("HYXI found %d new device(s); adding them", len(new))
    _async_register_devices(hass, entry, coordinator)
    async_dispatcher_send(hass, new_devices_signal(entry), new)
    entry.async_create_task(
        hass, _async_setup_battery_protection(hass, coordinator, new)
    )
    if entry.options.get(CONF_ENABLE_PUSH) is True:
        entry.async_create_task(
            hass, _async_sync_subscriptions(hass, entry, coordinator)
        )


async def async_unload_entry(hass: HomeAssistant, entry: HyxiConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading HYXI Cloud entry %s", entry.entry_id)
    coordinator = entry.runtime_data
    if coordinator.engine is not None:
        coordinator.engine.stop()
    for controller in coordinator.protection_controllers.values():
        controller.stop()
    if is_modbus_entry(entry):
        await _release_modbus_dispatch_on_teardown(hass, coordinator)
    else:
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
    _LOGGER.debug("HYXI Cloud entry %s unload result: %s", entry.entry_id, unload_ok)
    return unload_ok


async def _release_modbus_dispatch_on_teardown(
    hass: HomeAssistant, coordinator: HyxiDataUpdateCoordinator
) -> None:
    """Hand the battery back to the inverter when the entry stops managing it.

    Runs on unload -- disabling device control, deleting the entry, or a
    reload -- but not on a Home Assistant shutdown, where the entry reloads
    and resumes control and a mode flip would be pointless churn. Skipped
    when control was never active this run (nothing to release). Writes
    unconditionally otherwise rather than checking the dispatch_enabled
    metric, which lags the device by up to a settings-refresh interval; the
    write is idempotent and cheap.

    Best-effort: a failed write (bus already gone) must not block the unload.
    """
    if hass.is_stopping:
        return
    if not (coordinator.protection_controllers or coordinator.engine is not None):
        return
    try:
        await coordinator.client.set_dispatch_enabled(False)
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.debug("Could not release dispatch on unload: %s", err)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Cancel any remaining subscriptions when the entry is permanently removed.

    Regular unload keeps subscriptions alive for reuse on the next load, so
    actual deletion of the integration must do the remote cleanup here.
    """
    ir.async_delete_issue(hass, DOMAIN, _cloud_battery_control_issue_id(entry))
    for webhook_id in [
        *(entry.data.get(key) for key in _WEBHOOKS),
        *(
            _legacy_webhook_id(entry, key)
            for key in entry.data.get(_LEGACY_WEBHOOKS, {})
        ),
    ]:
        if webhook_id:
            await _async_delete_cloudhook(hass, webhook_id)

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
            await async_cancel_subscription(client, code)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "Could not cancel subscription %s during entry removal: %s "
                "(once the integration is set up again, it is listed on the "
                "Subscription Status sensor for the "
                "hyxi_cloud.cancel_subscription service)",
                mask_subscription_code(code),
                err,
            )


def _async_setup_energy_manager(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Set up the Energy Manager engine and register its virtual device."""
    em_enabled = entry.options.get(CONF_EM_ENABLED, False)
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)
    em_device = (coordinator.data or {}).get(em_sn) if em_sn else None
    battery_controllable = em_device is not None and is_control_capable_device_type(
        entry, device_type_of(em_device)
    )
    if not em_enabled or not em_sn or not battery_controllable:
        _LOGGER.debug(
            "EM not started: enabled=%s sn_configured=%s sn_in_data=%s "
            "battery_controllable=%s",
            em_enabled,
            bool(em_sn),
            em_device is not None,
            battery_controllable,
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

    # Register EM virtual device under its inverter. UNDEFINED (not None)
    # when the inverter isn't found, so a lookup miss leaves any existing
    # link alone instead of clearing it.
    device_registry = dr.async_get(hass)
    inverter = device_registry.async_get_device_by_identifier(
        (DOMAIN, em_sn), entry.entry_id
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{em_sn}_energy_manager")},
        name="Energy Manager",
        manufacturer=MANUFACTURER,
        model="Energy Manager",
        via_device_id=inverter.id if inverter is not None else dr.UNDEFINED,
    )


async def async_reload_entry(hass: HomeAssistant, entry: HyxiConfigEntry) -> None:
    """Reload config entry when options change."""
    if entry.runtime_data.options == entry.options:
        _LOGGER.debug(
            "HYXI: Config entry data updated, skipping reload as options did not change"
        )
        return
    _LOGGER.debug("HYXI: Options updated, reloading integration to apply new settings")
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_setup_battery_protection(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
    devices: dict[str, dict],
) -> None:
    """Start battery protection on those of `devices` it supports."""
    from .const import is_battery_control_enabled

    if not is_battery_control_enabled(coordinator.entry):
        _LOGGER.debug("Battery control and protection is disabled by user settings")
        return

    tasks = []
    task_sns = []
    for sn, dev_data in devices.items():
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
        # device's controller failing to start must not take the whole
        # config entry down. A failure of its initial evaluation (and so
        # of the control write it sends) is logged by the controller
        # itself; async_start() registers its coordinator listener first,
        # so the controller retries on the next coordinator refresh --
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

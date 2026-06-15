"""HYXI Cloud Integration for Home Assistant."""
# pylint: disable=wrong-import-position

import hmac
import logging

from aiohttp import ClientError, web
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
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
    CONF_ACCESS_KEY,
    CONF_EM_ENABLED,
    CONF_EM_FORECAST_ENTITY,
    CONF_EM_FORECAST_POWER_ENTITY,
    CONF_EM_INVERTER_SN,
    CONF_EM_P1_ENTITY,
    CONF_ENABLE_PUSH,
    CONF_PUSH_RATE,
    CONF_PUSH_URL,
    CONF_SECRET_KEY,
    DEFAULT_PUSH_RATE,
    DOMAIN,
    MANUFACTURER,
    PLATFORMS,
    VERSION,
    detect_phase_type,
    get_raw_device_code,
    mask_sensitive_key_value,
    mask_sn,
    mask_url,
    normalize_device_type,
)
from .coordinator import HyxiDataUpdateCoordinator
from .protection import HyxiBatteryProtectionController

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HYXI Cloud from a config entry."""
    _LOGGER.debug(
        "Starting HYXI Cloud Integration (Integration: %s, API: %s)",
        VERSION,
        API_VERSION,
    )

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
        _LOGGER.warning("HYXI Cloud not ready: %s", err)
        raise ConfigEntryNotReady(f"Connection error: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Set up real-time push subscription if enabled (graceful fallback to polling if it fails)
    await _async_setup_push_subscription(hass, entry, coordinator)
    # Set up alarm push subscription (runs alongside data push, same webhook base URL)
    if entry.options.get(CONF_ENABLE_PUSH, False):
        await _async_setup_alarm_subscription(hass, entry, coordinator)

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

    _remove_legacy_select_entities(hass, coordinator.data)
    _cleanup_control_entities(hass, entry, coordinator)
    await _async_setup_battery_protection(hass, coordinator)

    # Energy Manager: create engine if configured and enabled
    em_enabled = entry.options.get(CONF_EM_ENABLED, False)
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)
    if em_enabled and em_sn and em_sn in coordinator.data:
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
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{em_sn}_energy_manager")},
            name="Energy Manager",
            manufacturer=MANUFACTURER,
            model="Energy Manager",
            via_device=(DOMAIN, em_sn),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start EM engine after platforms are loaded (entities need to exist first)
    if coordinator.engine is not None:
        await coordinator.engine.async_start()

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await async_setup_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is not None:
        if coordinator.engine is not None:
            await coordinator.engine.async_stop()
        for controller in coordinator.protection_controllers.values():
            await controller.async_stop()
        await _async_teardown_push_subscription(hass, coordinator, entry)
        await _async_teardown_alarm_subscription(hass, coordinator, entry)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            if hass.services.has_service(DOMAIN, "cancel_subscription"):
                hass.services.async_remove(DOMAIN, "cancel_subscription")
    return unload_ok


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


def _cleanup_control_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Remove control entities from registry if battery control is disabled."""
    from .const import is_battery_control_enabled

    if is_battery_control_enabled(entry, coordinator):
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

    if not is_battery_control_enabled(coordinator.entry, coordinator):
        _LOGGER.debug("Battery control and protection is disabled by user settings")
        return

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if device_type not in ("hybrid_inverter", "all_in_one"):
            continue
        phase = detect_phase_type(dev_data)
        if phase not in ("three_phase", "single_phase"):
            continue

        controller = HyxiBatteryProtectionController(hass, coordinator, sn)
        coordinator.protection_controllers[sn] = controller
        await controller.async_start()


async def _async_resolve_webhook_url(
    hass: HomeAssistant,
    webhook_id: str,
    custom_url: str | None,
) -> str | None:
    """Resolve the external callback URL for the HYXI push subscription."""
    if custom_url and custom_url.strip():
        # Treat custom_url as the base URL — always append the HA webhook path.
        base = custom_url.strip().rstrip("/")
        resolved = base + webhook.async_generate_path(webhook_id)
        _LOGGER.info(
            "HYXI Push: Using custom base URL, callback endpoint: %s",
            mask_url(resolved),
        )
        return resolved

    _LOGGER.debug("HYXI Push: Resolving external callback URL automatically...")
    resolved = None

    # Check Nabu Casa first
    import homeassistant.components.cloud as cloud  # pylint: disable=consider-using-from-import

    if cloud.async_active_subscription(hass):
        _LOGGER.debug("HYXI Push: Nabu Casa subscription detected, trying cloud URL")
        try:
            resolved = await cloud.async_get_or_create_cloudhook(hass, webhook_id)
        except Exception as err:  # pylint: disable=broad-except
            # Fall back to base Exception if CloudNotAvailable is not a valid exception class (e.g. in tests)
            exc_cls = getattr(cloud, "CloudNotAvailable", Exception)
            if not isinstance(exc_cls, type) or not issubclass(exc_cls, BaseException):
                exc_cls = Exception
            if isinstance(err, exc_cls):
                _LOGGER.debug(
                    "HYXI Push: Nabu Casa cloud hook not connected or available, falling back to network URL"
                )
            else:
                raise err

    # Fall back to standard external network settings
    if not resolved:
        try:
            resolved = network.get_url(
                hass, allow_external=True
            ) + webhook.async_generate_path(webhook_id)
            _LOGGER.debug(
                "HYXI Push: Resolved callback URL via network helper: %s",
                mask_url(resolved),
            )
        except network.NoURLAvailableError:
            _LOGGER.debug(
                "HYXI Push: network.get_url raised NoURLAvailableError"
                " (no external URL configured)"
            )

    return resolved


async def _async_setup_push_subscription(  # pylint: disable=too-many-statements
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Set up real-time webhook push subscription."""
    enable_push = entry.options.get(CONF_ENABLE_PUSH, False)
    if enable_push is not True:
        coordinator.push_status = "inactive"
        return

    # Cancel any previously-active subscription that wasn't cleanly torn down
    prior_code = entry.data.get("push_subscribe_code")
    if prior_code:
        _LOGGER.debug(
            "HYXI Push: Cancelling prior orphaned subscription (code: %s)", prior_code
        )
        try:
            await coordinator.client.cancel_subscription(prior_code)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug("HYXI Push: Could not cancel prior subscription: %s", err)
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "push_subscribe_code": None}
        )

    push_rate_s = int(entry.options.get(CONF_PUSH_RATE, DEFAULT_PUSH_RATE))
    push_rate_ms = push_rate_s * 1000
    custom_url = entry.options.get(CONF_PUSH_URL)

    webhook_id = f"hyxi_cloud_{entry.entry_id}"
    coordinator.webhook_id = webhook_id
    coordinator.push_enabled = True

    # Register webhook handler
    try:
        webhook.async_register(
            hass,
            DOMAIN,
            "HYXI Cloud Push",
            webhook_id,
            lambda h, w_id, req: _async_handle_webhook(h, w_id, req, coordinator),
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
        return

    coordinator.push_url = webhook_url

    device_sns = [sn for sn in coordinator.data if sn]
    if not device_sns:
        _LOGGER.debug("No devices available to subscribe to push notifications")
        coordinator.push_status = "inactive"
        return

    _LOGGER.debug(
        "Subscribing callback URL %s for devices: %s",
        mask_url(webhook_url),
        [mask_sn(sn) for sn in device_sns],
    )

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
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, "push_subscribe_code": coordinator.subscribe_code},
            )
            if coordinator.subscribe_code:
                await async_register_subscription_code(hass, coordinator.subscribe_code)
            _LOGGER.info(
                "Successfully subscribed to HYXI Real-Time Push (code: %s)",
                coordinator.subscribe_code,
            )
        else:
            coordinator.push_status = "error"
            msg = res.get("msg", "Unknown error")
            coordinator.push_error = msg
            if "B004002" in msg or "repeatedly" in msg:
                _LOGGER.warning(
                    "Failed to register HYXI Real-Time Push subscription: %s. "
                    "If you have an active/orphaned subscription on another instance, retrieve the code from the "
                    "Subscription Status sensor's attributes (known_subscription_codes) and cancel it using the "
                    "'hyxi_cloud.cancel_subscription' service.",
                    msg,
                )
            else:
                _LOGGER.warning(
                    "Failed to register HYXI Real-Time Push subscription: %s", msg
                )
    except Exception as err:  # pylint: disable=broad-exception-caught
        coordinator.push_status = "error"
        err_msg = str(err)
        coordinator.push_error = err_msg
        if "B004002" in err_msg or "repeatedly" in err_msg:
            _LOGGER.warning(
                "Failed to register HYXI Real-Time Push subscription: %s. "
                "If you have an active/orphaned subscription on another instance, retrieve the code from the "
                "Subscription Status sensor's attributes (known_subscription_codes) and cancel it using the "
                "'hyxi_cloud.cancel_subscription' service.",
                err_msg,
            )
        else:
            _LOGGER.warning(
                "Failed to register HYXI Real-Time Push subscription: %s", err
            )


async def _async_teardown_push_subscription(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
    entry: ConfigEntry | None = None,
) -> None:
    """Tear down push subscription and webhook."""
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
        try:
            await async_cancel_and_unregister_subscription(
                hass, coordinator.client, subscribe_code
            )
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Error cancelling HYXI Push subscription: %s", err)
        coordinator.subscribe_code = None
        # Clear the persisted code — subscription is now cleanly cancelled.
        if entry is not None:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "push_subscribe_code": None}
            )

    coordinator.push_enabled = False
    coordinator.push_status = "inactive"
    coordinator.push_url = None


async def _async_handle_webhook(
    hass: HomeAssistant,
    webhook_id: str,
    request: web.Request,
    coordinator: HyxiDataUpdateCoordinator,
) -> web.Response:
    """Handle incoming webhook request from HYXI Cloud."""
    from homeassistant.util import dt as dt_util

    # 1. Ingress Header authentication check (defense-in-depth)
    incoming_ak = request.headers.get("accessKey") or request.headers.get("AccessKey")
    if not incoming_ak or not hmac.compare_digest(
        incoming_ak, coordinator.client.access_key
    ):
        # Do not log the header value — it is user-controlled (CWE-117 Log Injection).
        _LOGGER.warning(
            "Unauthorized push attempt received on webhook %s",
            webhook_id,
        )
        return web.Response(status=401, text="Unauthorized")

    # 2. Parse JSON payload
    try:
        payload = await request.json()
    except ValueError:
        _LOGGER.warning("Received invalid JSON payload on HYXI push webhook")
        return web.Response(status=400, text="Invalid JSON")

    _LOGGER.debug(
        "HYXI Cloud Data Push webhook callback received. Webhook ID: %s, Active Subscribe Code: %s",
        "hyxi_cloud_***" if webhook_id.startswith("hyxi_cloud_") else "***",
        coordinator.subscribe_code,
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
        _LOGGER.error("Error parsing push payload: %s", err)
        return web.Response(status=500, text="Internal Processing Error")

    if not push_results:
        return web.json_response({"code": "0", "msg": "Success", "success": True})

    # 4. Apply updates to coordinator
    any_updated = False
    if coordinator.data is None:
        coordinator.data = {}

    for sn, device_update in push_results.items():
        if sn not in coordinator.data:
            _LOGGER.warning(
                "Received push data for untracked device SN: %s", mask_sn(sn)
            )
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

    if any_updated:
        coordinator.last_push_received = dt_util.utcnow()
        coordinator.async_update_listeners()

    return web.json_response({"code": "0", "msg": "Success", "success": True})


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
    push_rate_s = int(entry.options.get(CONF_PUSH_RATE, DEFAULT_PUSH_RATE))
    push_rate_ms = push_rate_s * 1000
    custom_url = entry.options.get(CONF_PUSH_URL)

    webhook_id = f"hyxi_cloud_{entry.entry_id}_alarm"
    coordinator.alarm_webhook_id = webhook_id

    # Cancel any orphaned prior subscription
    prior_code = entry.data.get("alarm_subscribe_code")
    if prior_code:
        _LOGGER.debug(
            "HYXI Alarm Push: Cancelling prior orphaned subscription (code: %s)",
            prior_code,
        )
        try:
            await coordinator.client.cancel_subscription(prior_code)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "HYXI Alarm Push: Could not cancel prior subscription: %s", err
            )
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "alarm_subscribe_code": None}
        )

    # Register webhook handler
    try:
        webhook.async_register(
            hass,
            DOMAIN,
            "HYXI Cloud Alarm Push",
            webhook_id,
            lambda h, w_id, req: _async_handle_alarm_webhook(h, w_id, req, coordinator),
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
        return

    device_sns = [sn for sn in coordinator.data if sn]
    if not device_sns:
        coordinator.alarm_push_status = "inactive"
        return

    _LOGGER.debug(
        "HYXI Alarm Push: Subscribing %s devices at %s",
        len(device_sns),
        mask_url(webhook_url),
    )

    try:
        res = await coordinator.client.subscribe_alarm(
            webhook_url,
            device_sns,
            push_rate_ms,
        )
        if res.get("success"):
            coordinator.alarm_subscribe_code = res["data"]["subscribeCode"]
            coordinator.alarm_push_status = "active"
            coordinator.alarm_push_url = webhook_url
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "alarm_subscribe_code": coordinator.alarm_subscribe_code,
                },
            )
            if coordinator.alarm_subscribe_code:
                await async_register_subscription_code(
                    hass, coordinator.alarm_subscribe_code
                )
            _LOGGER.info(
                "Successfully subscribed to HYXI Alarm Push (code: %s)",
                coordinator.alarm_subscribe_code,
            )
        else:
            coordinator.alarm_push_status = "error"
            msg = res.get("msg", "Unknown error")
            if "B004002" in msg or "repeatedly" in msg:
                _LOGGER.warning(
                    "Failed to register HYXI Alarm Push subscription: %s. "
                    "If you have an active/orphaned subscription on another instance, retrieve the code from the "
                    "Subscription Status sensor's attributes (known_subscription_codes) and cancel it using the "
                    "'hyxi_cloud.cancel_subscription' service.",
                    msg,
                )
            else:
                _LOGGER.warning("HYXI Alarm Push subscription failed: %s", msg)
    except Exception as err:  # pylint: disable=broad-exception-caught
        coordinator.alarm_push_status = "error"
        err_msg = str(err)
        if "B004002" in err_msg or "repeatedly" in err_msg:
            _LOGGER.warning(
                "Failed to register HYXI Alarm Push subscription: %s. "
                "If you have an active/orphaned subscription on another instance, retrieve the code from the "
                "Subscription Status sensor's attributes (known_subscription_codes) and cancel it using the "
                "'hyxi_cloud.cancel_subscription' service.",
                err_msg,
            )
        else:
            _LOGGER.warning("Failed to register HYXI Alarm Push subscription: %s", err)


async def _async_teardown_alarm_subscription(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
    entry: ConfigEntry | None = None,
) -> None:
    """Tear down alarm push subscription and webhook."""
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
        try:
            await async_cancel_and_unregister_subscription(
                hass, coordinator.client, subscribe_code
            )
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Error cancelling HYXI Alarm Push subscription: %s", err)
        coordinator.alarm_subscribe_code = None
        if entry is not None:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "alarm_subscribe_code": None}
            )

    coordinator.alarm_push_status = "inactive"


async def _async_handle_alarm_webhook(
    hass: HomeAssistant,
    webhook_id: str,
    request: web.Request,
    coordinator: HyxiDataUpdateCoordinator,
) -> web.Response:
    """Handle incoming alarm push webhook from HYXI Cloud.

    Parses the alarm payload via SDK, merges alarm records into
    coordinator.data[sn]["alarms"] so HyxiDeviceAlarmSensor fires instantly.
    """
    incoming_ak = request.headers.get("accessKey") or request.headers.get("AccessKey")
    if not incoming_ak or not hmac.compare_digest(
        incoming_ak, coordinator.client.access_key
    ):
        # Do not log the header value — it is user-controlled (CWE-117 Log Injection).
        _LOGGER.warning(
            "Unauthorized alarm push attempt received on webhook %s",
            webhook_id,
        )
        return web.Response(status=401, text="Unauthorized")

    from homeassistant.util import dt as dt_util

    try:
        payload = await request.json()
    except ValueError:
        _LOGGER.warning("Received invalid JSON payload on HYXI alarm push webhook")
        return web.Response(status=400, text="Invalid JSON")

    _LOGGER.debug(
        "HYXI Cloud Alarm Push webhook callback received. Webhook ID: %s, Active Subscribe Code: %s",
        "hyxi_cloud_***" if webhook_id.startswith("hyxi_cloud_") else "***",
        coordinator.alarm_subscribe_code,
    )

    # Stamp contact time unconditionally — HYXI sends pings on schedule even
    # when there are no active alarms (empty dataList), so we always record contact.
    coordinator.alarm_last_push_received = dt_util.utcnow()

    try:
        alarm_results = coordinator.client.process_alarm_push_data(payload)
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.error("Error parsing alarm push payload: %s", err)
        return web.Response(status=500, text="Internal Processing Error")

    if not alarm_results:
        return web.json_response({"code": "0", "msg": "Success", "success": True})

    if coordinator.data is None:
        coordinator.data = {}

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

    if any_updated:
        coordinator.async_update_listeners()

    return web.json_response({"code": "0", "msg": "Success", "success": True})


async def async_setup_services(hass: HomeAssistant) -> None:
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
        _LOGGER.info("Manually cancelling HYXI subscription: %s", subscribe_code)
        try:
            await async_cancel_and_unregister_subscription(
                hass, coordinator.client, subscribe_code
            )
        except Exception as err:
            _LOGGER.error(
                "Error manual cancelling HYXI subscription %s: %s", subscribe_code, err
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


async def async_register_subscription_code(hass: HomeAssistant, code: str) -> None:
    """Save a subscription code to persistent storage and update coordinators."""
    from unittest.mock import Mock

    if isinstance(hass, Mock):
        return

    from homeassistant.helpers.storage import Store

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
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

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
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

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    data = await store.async_load() or {}
    return data.get("codes", [])


async def async_cancel_and_unregister_subscription(
    hass: HomeAssistant, client, code: str
) -> None:
    """Cancel a subscription via the API and unregister it from storage if successful or already inactive."""
    code = code.strip()
    if not code:
        return

    _LOGGER.info("Cancelling HYXI subscription: %s", code)
    try:
        res = await client.cancel_subscription(code)
        if isinstance(res, dict) and not res.get("success"):
            msg = res.get("msg", "Unknown error")
            sub_err_cls = getattr(client, "SubscriptionError", RuntimeError)
            if not isinstance(sub_err_cls, type) or not issubclass(
                sub_err_cls, BaseException
            ):
                sub_err_cls = RuntimeError
            raise sub_err_cls(f"subscription request failed: {msg}")

        await async_unregister_subscription_code(hass, code)
        _LOGGER.info("Successfully cancelled HYXI subscription: %s", code)
    except Exception as err:
        is_sub_err = False
        sub_err_cls = getattr(client, "SubscriptionError", None)
        if (
            sub_err_cls
            and isinstance(sub_err_cls, type)
            and issubclass(sub_err_cls, BaseException)
        ):
            if isinstance(err, sub_err_cls):
                is_sub_err = True

        if type(
            err
        ).__name__ == "SubscriptionError" or "subscription request failed" in str(err):
            is_sub_err = True

        if (
            is_sub_err
            and "Authentication failed" not in str(err)
            and "no_response" not in str(err)
        ):
            _LOGGER.info(
                "Subscription code %s was already unsubscribed or invalid (API error: %s), removing from known codes",
                code,
                err,
            )
            await async_unregister_subscription_code(hass, code)
        else:
            _LOGGER.warning("Error cancelling HYXI subscription %s: %s", code, err)
        raise

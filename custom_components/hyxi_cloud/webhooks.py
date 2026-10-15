"""Webhooks HYXI pushes real-time data and alarms to."""

import functools
import hmac
import logging

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    mask_sensitive_key_value,
    mask_sn,
    mask_subscription_code,
    mask_url,
)
from .coordinator import HyxiDataUpdateCoordinator
from .push_url import cloud_subscription_active, external_https_url, is_https_url

_LOGGER = logging.getLogger(__name__)


# Entry data keys of the push and alarm webhook IDs, each with the entry
# data key of its subscription code and the suffix of its legacy ID.
_WEBHOOKS = {
    "webhook_id": ("push_subscribe_code", ""),
    "alarm_webhook_id": ("alarm_subscribe_code", "_alarm"),
}


# Entry data key of the legacy webhooks still in use: webhook ID key ->
# the subscription code made with the legacy ID (None if there was none).
_LEGACY_WEBHOOKS = "legacy_webhooks"


def _legacy_webhook_id(entry: ConfigEntry, key: str) -> str:
    """The webhook ID an entry without random IDs used for key."""
    return f"hyxi_cloud_{entry.entry_id}{_WEBHOOKS[key][1]}"


def _register_webhook(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
    key: str,
    webhook_id: str,
) -> None:
    """Handle HYXI's pushes to webhook_id with the push or alarm handler,
    per key."""
    name, handler = {
        "webhook_id": ("HYXI Cloud Push", _async_handle_webhook),
        "alarm_webhook_id": ("HYXI Cloud Alarm Push", _async_handle_alarm_webhook),
    }[key]
    try:
        webhook.async_register(
            hass,
            DOMAIN,
            name,
            webhook_id,
            lambda _h, w_id, req: handler(w_id, req, coordinator),
        )
    except ValueError:
        pass  # Already registered (e.g. after a failed reload)


def _ensure_webhook_ids(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """Return the entry's random push and alarm webhook IDs, creating any
    it lacks.

    An entry without them used _legacy_webhook_id; that ID is kept as a
    legacy webhook, with the subscription code made with it, until
    _async_retire_legacy_webhooks retires it.
    """
    ids: dict[str, str] = {}
    missing: list[str] = []
    for key in _WEBHOOKS:
        if webhook_id := entry.data.get(key):
            ids[key] = webhook_id
        else:
            missing.append(key)
    if not missing:
        return ids
    legacy = dict(entry.data.get(_LEGACY_WEBHOOKS, {}))
    for key in missing:
        ids[key] = webhook.async_generate_id()
        legacy[key] = entry.data.get(_WEBHOOKS[key][0])
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, **ids, _LEGACY_WEBHOOKS: legacy}
    )
    return ids


def _register_legacy_webhooks(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: HyxiDataUpdateCoordinator
) -> None:
    """Handle pushes to the entry's legacy webhook IDs until they are
    retired."""
    for key in entry.data.get(_LEGACY_WEBHOOKS, {}):
        legacy_id = _legacy_webhook_id(entry, key)
        _register_webhook(hass, coordinator, key, legacy_id)
        entry.async_on_unload(
            functools.partial(webhook.async_unregister, hass, legacy_id)
        )


async def _async_retire_legacy_webhooks(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Retire each legacy webhook whose subscription code the entry no
    longer holds: stop handling it and delete its cloudhook. One whose
    cloudhook could not be deleted is kept and retried at the next setup."""
    held = entry.data.get(_LEGACY_WEBHOOKS, {})
    legacy = dict(held)
    for key, code in held.items():
        if code and entry.data.get(_WEBHOOKS[key][0]) == code:
            continue
        legacy_id = _legacy_webhook_id(entry, key)
        if await _async_delete_cloudhook(hass, legacy_id):
            webhook.async_unregister(hass, legacy_id)
            del legacy[key]
    if legacy == held:
        return
    data = {k: v for k, v in entry.data.items() if k != _LEGACY_WEBHOOKS}
    if legacy:
        data[_LEGACY_WEBHOOKS] = legacy
    hass.config_entries.async_update_entry(entry, data=data)


async def _async_delete_cloudhook(hass: HomeAssistant, webhook_id: str) -> bool:
    """Delete the Home Assistant Cloud cloudhook of a webhook. Returns False
    if it may still exist (Home Assistant Cloud could not be reached)."""
    if "cloud" not in hass.config.components:
        return True
    # pylint: disable-next=consider-using-from-import
    import homeassistant.components.cloud as cloud

    try:
        await cloud.async_delete_cloudhook(hass, webhook_id)
    except ValueError:
        pass  # No cloudhook for this webhook
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.debug("Could not delete a HYXI cloudhook: %s", err)
        return False
    return True


async def _async_resolve_webhook_url(
    hass: HomeAssistant,
    webhook_id: str,
    custom_url: str | None,
) -> str | None:
    """Resolve the external callback URL for the HYXI push subscription."""
    if custom_url and custom_url.strip():
        # Treat custom_url as the base URL — always append the HA webhook path.
        base = custom_url.strip().rstrip("/")
        if not is_https_url(base):
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
    if not cloud_subscription_active(hass):
        return None
    # pylint: disable-next=consider-using-from-import
    import homeassistant.components.cloud as cloud

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
    """Try to resolve the callback URL via HA's network helper, as an
    external HTTPS URL: HYXI's cloud can't reach an internal or plain-HTTP
    one."""
    base = external_https_url(hass)
    if base is None:
        _LOGGER.debug("HYXI Push: no external HTTPS URL configured")
        return None
    resolved = base + webhook.async_generate_path(webhook_id)
    _LOGGER.debug(
        "HYXI Push: Resolved callback URL via network helper: %s",
        mask_url(resolved),
    )
    return resolved


async def _async_handle_webhook(
    webhook_id: str,
    request: web.Request,
    coordinator: HyxiDataUpdateCoordinator,
) -> web.Response:
    """Handle incoming webhook request from HYXI Cloud."""
    from homeassistant.util import dt as dt_util

    if not _is_authorized_webhook_request(request, coordinator):
        # Do not log the header value — it is user-controlled (CWE-117 Log Injection).
        _LOGGER.warning("Unauthorized push attempt received on the HYXI push webhook")
        return web.Response(status=401, text="Unauthorized")

    payload = await _parse_webhook_payload(request, "push")
    if payload is None:
        return web.Response(status=400, text="Invalid JSON")

    _LOGGER.debug(
        "HYXI Cloud Data Push webhook callback received. Active Subscribe Code: %s",
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

    # A push counts as received even when none of its readings is newer.
    coordinator.last_push_received = dt_util.utcnow()

    # 4. Apply updates to coordinator
    if coordinator.data is None:
        coordinator.data = {}
    if _apply_push_updates(coordinator, push_results):
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
            "Received invalid JSON payload on HYXI %s webhook (%d characters): %s",
            context,
            len(text),
            e,
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
            "Unauthorized alarm push attempt received on the HYXI alarm webhook"
        )
        return web.Response(status=401, text="Unauthorized")

    from homeassistant.util import dt as dt_util

    payload = await _parse_webhook_payload(request, "alarm push")
    if payload is None:
        return web.Response(status=400, text="Invalid JSON")

    _LOGGER.debug(
        "HYXI Cloud Alarm Push webhook callback received. Active Subscribe Code: %s",
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

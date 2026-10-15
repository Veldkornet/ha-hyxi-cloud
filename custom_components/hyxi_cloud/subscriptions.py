"""HYXI push subscriptions for real-time data and alarms."""

import hashlib
import logging

from aiohttp import ClientError
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from hyxi_cloud_api import HyxiApiClient, SubscriptionType

from .const import (
    CONF_ENABLE_PUSH,
    CONF_PUSH_RATE,
    CONF_PUSH_URL,
    DEFAULT_PUSH_RATE,
    mask_sn,
    mask_subscription_code,
    mask_url,
)
from .coordinator import HyxiDataUpdateCoordinator
from .webhooks import (
    _async_resolve_webhook_url,
    _async_retire_legacy_webhooks,
    _ensure_webhook_ids,
    _register_webhook,
)

_LOGGER = logging.getLogger(__name__)


# The client's error when HYXI refuses a subscription call.
SubscriptionError = HyxiApiClient.SubscriptionError


_PUSH_SUBSCRIPTION_LABEL = "HYXI Push"


_ALARM_PUSH_SUBSCRIPTION_LABEL = "HYXI Alarm Push"


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


def _hyxi_holds(coordinator: HyxiDataUpdateCoordinator, code: str) -> bool | None:
    """Whether HYXI's subscription list for the entry holds code, or None
    while the list has not been fetched."""
    held = coordinator.subscriptions
    if held is None:
        return None
    return any(sub.subscribe_code == code for sub in held)


def _should_reuse_subscription(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    coordinator: HyxiDataUpdateCoordinator,
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
    stored fingerprint, and HYXI still holds it (assumed when its
    subscription list could not be fetched). Returns None if there's no
    persisted code, or it can't be reused and a fresh subscribe is needed. Shared by
    the push and alarm setup flows so this decision only needs to be
    changed in one place.
    """
    prior_code = entry.data.get(config_key)
    if not prior_code:
        return None
    fingerprint = _compute_subscription_fingerprint(
        webhook_url, device_sns, push_rate_ms
    )
    if entry.data.get(fingerprint_key) != fingerprint:
        return None
    if _hyxi_holds(coordinator, prior_code) is False:
        _LOGGER.info(
            "HYXI no longer holds subscription %s; subscribing again",
            mask_subscription_code(prior_code),
        )
        return None
    return prior_code


def _clear_subscription_entry_data(
    hass: HomeAssistant, entry: ConfigEntry, config_key: str, fingerprint_key: str
) -> None:
    """Clear a persisted subscription code and its fingerprint from entry.data."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, config_key: None, fingerprint_key: None}
    )


async def _async_maybe_cancel_subscription(
    coordinator: HyxiDataUpdateCoordinator,
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
        await _async_cancel_and_forget(coordinator, subscribe_code)
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning(
            "Error cancelling %s subscription%s: %s",
            log_prefix,
            "" if not force else " (forcing local reset anyway)",
            err,
        )
        return force
    return True


def _log_push_subscription_failure(push_type: str, err_msg: str) -> None:
    """Log a formatted warning for push subscription failures."""
    if "B004002" in err_msg or "repeatedly" in err_msg:
        _LOGGER.warning(
            "Failed to register %s subscription: %s. "
            "If you have an active/orphaned subscription on another instance, find its code in the "
            "Subscription Status sensor's 'subscriptions' attribute and cancel it using the "
            "'hyxi_cloud.cancel_subscription' service.",
            push_type,
            err_msg,
        )
    else:
        _LOGGER.warning("Failed to register %s subscription: %s", push_type, err_msg)


async def _async_cancel_and_forget(
    coordinator: HyxiDataUpdateCoordinator, code: str
) -> None:
    """Cancel a subscription and drop it from the entry's copy of HYXI's
    list, so it isn't taken for one still in place. Raises like
    async_cancel_subscription when the cancel fails."""
    await async_cancel_subscription(coordinator.client, code)
    if coordinator.subscriptions is not None:
        coordinator.subscriptions = [
            sub for sub in coordinator.subscriptions if sub.subscribe_code != code
        ]


async def _async_cancel_conflicting_subscriptions(
    coordinator: HyxiDataUpdateCoordinator,
    subscribe_type: SubscriptionType,
    device_sns: list[str],
    log_prefix: str,
) -> None:
    """Cancel the subscriptions of this type HYXI holds for any of these
    devices, whoever made them: HYXI rejects a new subscription for a device
    that already has one. One without devices (as alarm subscriptions can
    be) is taken to cover them all. HYXI's list is fetched again first if
    it couldn't be before."""
    if coordinator.subscriptions is None:
        await async_refresh_subscriptions(coordinator)
    if coordinator.subscriptions is None:
        _LOGGER.debug(
            "%s: HYXI's subscription list is unknown, so existing "
            "subscriptions that could block a new one are not cancelled",
            log_prefix,
        )
        return
    devices = set(device_sns)
    conflicting = [
        sub
        for sub in coordinator.subscriptions
        if sub.subscribe_type == subscribe_type
        and (not sub.devices or devices.intersection(sub.devices))
    ]
    for sub in conflicting:
        _LOGGER.warning(
            "%s: cancelling existing subscription %s (callback %s) for this "
            "entry's devices, which would block a new one",
            log_prefix,
            mask_subscription_code(sub.subscribe_code),
            mask_url(sub.callback_url),
        )
        try:
            await _async_cancel_and_forget(coordinator, sub.subscribe_code)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "%s: could not cancel subscription %s: %s",
                log_prefix,
                mask_subscription_code(sub.subscribe_code),
                err,
            )


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
    new_data = {**entry.data, config_key: None}
    if fingerprint_key:
        new_data[fingerprint_key] = None
    if _hyxi_holds(coordinator, prior_code) is False:
        # Nothing to cancel: HYXI no longer holds it.
        hass.config_entries.async_update_entry(entry, data=new_data)
        return

    _LOGGER.debug(
        "%s: Cancelling prior/orphaned subscription (code: %s)",
        log_prefix,
        mask_subscription_code(prior_code),
    )
    try:
        await _async_cancel_and_forget(coordinator, prior_code)
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.debug(
            "%s: Could not cancel prior subscription, preserving code for retry: %s",
            log_prefix,
            err,
        )
        return

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
        _LOGGER.info(
            "Successfully subscribed to HYXI Real-Time Push (code: %s)",
            mask_subscription_code(coordinator.subscribe_code),
        )
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
        _LOGGER.info(
            "Successfully subscribed to HYXI Alarm Push (code: %s)",
            mask_subscription_code(coordinator.alarm_subscribe_code),
        )
    except Exception as err:  # pylint: disable=broad-exception-caught
        coordinator.alarm_push_status = "error"
        err_msg = str(err)
        coordinator.alarm_push_error = err_msg
        _log_push_subscription_failure(_ALARM_PUSH_SUBSCRIPTION_LABEL, err_msg)


async def _async_sync_subscriptions(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Set up the push and alarm subscriptions per the entry's options,
    retire legacy webhooks no subscription uses any more, and list HYXI's
    subscriptions again."""
    # Both handle enablement checks and cleanup of orphaned codes.
    await _async_setup_push_subscription(hass, entry, coordinator)
    await _async_setup_alarm_subscription(hass, entry, coordinator)
    await _async_retire_legacy_webhooks(hass, entry)
    await async_refresh_subscriptions(coordinator)


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

    webhook_id = _ensure_webhook_ids(hass, entry)["webhook_id"]
    coordinator.webhook_id = webhook_id
    coordinator.push_enabled = True

    # Register webhook handler. HA's webhook registry is in-memory, so this
    # must run on every load regardless of whether the subscription itself
    # is reused below.
    _register_webhook(hass, coordinator, "webhook_id", webhook_id)

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

    reused_code = _should_reuse_subscription(
        coordinator,
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
    await _async_cancel_conflicting_subscriptions(
        coordinator,
        SubscriptionType.REAL_TIME_DATA,
        device_sns,
        _PUSH_SUBSCRIPTION_LABEL,
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
    supplier. It stays safe even when forced because a subscription HYXI
    still holds stays listed in the Subscription Status sensor's
    `subscriptions` attribute for manual cancelling.
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
            coordinator,
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

    webhook_id = _ensure_webhook_ids(hass, entry)["alarm_webhook_id"]
    coordinator.alarm_webhook_id = webhook_id

    # Register webhook handler. HA's webhook registry is in-memory, so this
    # must run on every load regardless of whether the subscription itself
    # is reused below.
    _register_webhook(hass, coordinator, "alarm_webhook_id", webhook_id)

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
        coordinator,
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
    await _async_cancel_conflicting_subscriptions(
        coordinator,
        SubscriptionType.ALARM,
        device_sns,
        _ALARM_PUSH_SUBSCRIPTION_LABEL,
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
            coordinator,
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


async def async_refresh_subscriptions(coordinator: HyxiDataUpdateCoordinator) -> None:
    """Fetch the subscriptions HYXI holds for the entry's credentials, keeping
    the last list if HYXI can't be reached."""
    try:
        coordinator.subscriptions = await coordinator.client.list_subscriptions()
    except (ClientError, TimeoutError) as err:
        _LOGGER.debug("Could not reach HYXI to list subscriptions: %s", err)
        return
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning("HYXI did not list the push subscriptions: %s", err)
        return
    coordinator.async_update_listeners()


async def async_cancel_subscription(client: HyxiApiClient, code: str) -> None:
    """Cancel a subscription via the API.

    HYXI has no "subscription not found" error code, so a refusal (including
    transient/rate-limit errors) is not a reliable signal that the
    subscription is actually gone. The client raises SubscriptionError for
    it, and any failure propagates so callers know not to discard their own
    record of the code.
    """
    code = code.strip()
    if not code:
        return

    _LOGGER.info("Cancelling HYXI subscription: %s", mask_subscription_code(code))
    await client.cancel_subscription(code)

    _LOGGER.info(
        "Successfully cancelled HYXI subscription: %s", mask_subscription_code(code)
    )

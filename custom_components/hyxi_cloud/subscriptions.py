"""HYXI push subscriptions for real-time data and alarms."""

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiohttp import ClientError
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from hyxi_cloud_api import HyxiApiClient, SubscriptionType

from .const import (
    CONF_ENABLE_PUSH,
    CONF_PUSH_RATE,
    CONF_PUSH_URL,
    DATA_ALARM_SUBSCRIBE_CODE,
    DATA_ALARM_SUBSCRIBE_FINGERPRINT,
    DATA_ALARM_WEBHOOK_ID,
    DATA_PUSH_SUBSCRIBE_CODE,
    DATA_PUSH_SUBSCRIBE_FINGERPRINT,
    DATA_PUSH_WEBHOOK_ID,
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


@dataclass(frozen=True)
class _Channel:
    """How a push channel's subscription is made, stored and tracked."""

    label: str
    subscription_type: SubscriptionType
    # The client's method that subscribes.
    subscribe: Callable[[HyxiApiClient], Callable[..., Awaitable[dict]]]
    # Entry data keys.
    webhook_key: str
    code_key: str
    fingerprint_key: str
    # Coordinator attributes; the webhook ID's is named like its entry key.
    code_attr: str
    status_attr: str
    error_attr: str
    url_attr: str
    unresolved_warning: str


_PUSH = _Channel(
    label="HYXI Real-Time Push",
    subscription_type=SubscriptionType.REAL_TIME_DATA,
    subscribe=lambda client: client.subscribe_real_time_data,
    webhook_key=DATA_PUSH_WEBHOOK_ID,
    code_key=DATA_PUSH_SUBSCRIBE_CODE,
    fingerprint_key=DATA_PUSH_SUBSCRIBE_FINGERPRINT,
    code_attr="subscribe_code",
    status_attr="push_status",
    error_attr="push_error",
    url_attr="push_url",
    unresolved_warning=(
        "HYXI Push: Could not resolve an external HTTPS callback URL. "
        "Real-time push is set to 'error' status. "
        "On dev/local instances without Nabu Casa or a configured external URL, "
        "enter a manually-reachable HTTPS URL in the 'Custom Callback URL' options field "
        "(e.g. via ngrok or a reverse proxy). "
        "Polling will continue as normal fallback."
    ),
)
_ALARM = _Channel(
    label="HYXI Alarm Push",
    subscription_type=SubscriptionType.ALARM,
    subscribe=lambda client: client.subscribe_alarm,
    webhook_key=DATA_ALARM_WEBHOOK_ID,
    code_key=DATA_ALARM_SUBSCRIBE_CODE,
    fingerprint_key=DATA_ALARM_SUBSCRIBE_FINGERPRINT,
    code_attr="alarm_subscribe_code",
    status_attr="alarm_push_status",
    error_attr="alarm_push_error",
    url_attr="alarm_push_url",
    unresolved_warning=(
        "HYXI Alarm Push: Could not resolve callback URL — "
        "alarm push disabled (real-time data push may still be active)."
    ),
)


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
    channel: _Channel,
    webhook_url: str,
    device_sns: list[str],
    push_rate_ms: int,
) -> str | None:
    """Return the persisted subscription code if it's still valid for reuse.

    A code is reusable when nothing that would invalidate it (callback URL,
    device list, or push rate) has changed since it was created, per the
    stored fingerprint, and HYXI still holds it (assumed when its
    subscription list could not be fetched). Returns None if there's no
    persisted code, or it can't be reused and a fresh subscribe is needed.
    """
    prior_code = entry.data.get(channel.code_key)
    if not prior_code:
        return None
    fingerprint = _compute_subscription_fingerprint(
        webhook_url, device_sns, push_rate_ms
    )
    if entry.data.get(channel.fingerprint_key) != fingerprint:
        return None
    if _hyxi_holds(coordinator, prior_code) is False:
        _LOGGER.info(
            "HYXI no longer holds subscription %s; subscribing again",
            mask_subscription_code(prior_code),
        )
        return None
    return prior_code


def _clear_subscription_entry_data(
    hass: HomeAssistant, entry: ConfigEntry, channel: _Channel
) -> None:
    """Clear the channel's persisted subscription code and its fingerprint
    from entry.data."""
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, channel.code_key: None, channel.fingerprint_key: None},
    )


async def _async_maybe_cancel_subscription(
    coordinator: HyxiDataUpdateCoordinator,
    subscribe_code: str,
    channel: _Channel,
    force: bool,
    cancel_remote: bool,
) -> bool:
    """Attempt to cancel a subscription per cancel_remote/force semantics.

    Returns True if the caller should clear its local code/fingerprint --
    either the cancel was confirmed, or force=True. Returns False to
    preserve the local record: the subscription is being kept alive for
    reuse (cancel_remote=False), or the cancel failed without force.
    """
    if not cancel_remote:
        _LOGGER.debug(
            "Keeping %s subscription active for reuse on next load (code: %s)",
            channel.label,
            mask_subscription_code(subscribe_code),
        )
        return False

    try:
        await _async_cancel_and_forget(coordinator, subscribe_code)
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning(
            "Error cancelling %s subscription%s: %s",
            channel.label,
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
    channel: _Channel,
    device_sns: list[str],
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
            channel.label,
        )
        return
    devices = set(device_sns)
    conflicting = [
        sub
        for sub in coordinator.subscriptions
        if sub.subscribe_type == channel.subscription_type
        and (not sub.devices or devices.intersection(sub.devices))
    ]
    for sub in conflicting:
        _LOGGER.warning(
            "%s: cancelling existing subscription %s (callback %s) for this "
            "entry's devices, which would block a new one",
            channel.label,
            mask_subscription_code(sub.subscribe_code),
            mask_url(sub.callback_url),
        )
        try:
            await _async_cancel_and_forget(coordinator, sub.subscribe_code)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "%s: could not cancel subscription %s: %s",
                channel.label,
                mask_subscription_code(sub.subscribe_code),
                err,
            )


async def _async_cancel_entry_subscription(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
    channel: _Channel,
) -> None:
    """Cancel the channel's stored subscription code and clear it from the
    config entry."""
    prior_code = entry.data.get(channel.code_key)
    if not prior_code:
        return
    if _hyxi_holds(coordinator, prior_code) is not False:
        _LOGGER.debug(
            "%s: Cancelling prior/orphaned subscription (code: %s)",
            channel.label,
            mask_subscription_code(prior_code),
        )
        try:
            await _async_cancel_and_forget(coordinator, prior_code)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "%s: Could not cancel prior subscription, preserving code for retry: %s",
                channel.label,
                err,
            )
            return
    # Else nothing to cancel: HYXI no longer holds it.
    _clear_subscription_entry_data(hass, entry, channel)


def _mark_active(
    coordinator: HyxiDataUpdateCoordinator, channel: _Channel, code: str
) -> None:
    """Record the channel's subscription code as active."""
    setattr(coordinator, channel.code_attr, code)
    setattr(coordinator, channel.status_attr, "active")
    setattr(coordinator, channel.error_attr, None)


async def _async_execute_subscription(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
    channel: _Channel,
    webhook_url: str,
    device_sns: list[str],
    push_rate_ms: int,
) -> None:
    """Subscribe the channel, recording its code and fingerprint."""
    try:
        res = await channel.subscribe(coordinator.client)(
            webhook_url,
            device_sns,
            push_rate_ms,  # API expects milliseconds
        )
        code = res["data"]["subscribeCode"]
        _mark_active(coordinator, channel, code)
        fingerprint = _compute_subscription_fingerprint(
            webhook_url, device_sns, push_rate_ms
        )
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                channel.code_key: code,
                channel.fingerprint_key: fingerprint,
            },
        )
        _LOGGER.info(
            "Successfully subscribed to %s (code: %s)",
            channel.label,
            mask_subscription_code(code),
        )
    except Exception as err:  # pylint: disable=broad-exception-caught
        setattr(coordinator, channel.status_attr, "error")
        err_msg = str(err)
        setattr(coordinator, channel.error_attr, err_msg)
        _log_push_subscription_failure(channel.label, err_msg)


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
    """Set up the real-time data push subscription."""
    if entry.options.get(CONF_ENABLE_PUSH, False) is True:
        coordinator.push_enabled = True
    await _async_setup_subscription(hass, entry, coordinator, _PUSH)


async def _async_setup_alarm_subscription(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> None:
    """Set up the alarm push subscription alongside real-time data push, on
    its own webhook ID so HYXI's callbacks can be told apart."""
    await _async_setup_subscription(hass, entry, coordinator, _ALARM)


async def _async_setup_subscription(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
    channel: _Channel,
) -> None:
    """Set up a channel's webhook and subscription, reusing the subscription
    while what it was made with is unchanged."""
    if entry.options.get(CONF_ENABLE_PUSH, False) is not True:
        setattr(coordinator, channel.status_attr, "inactive")
        await _async_cancel_entry_subscription(hass, entry, coordinator, channel)
        return

    push_rate_ms = int(entry.options.get(CONF_PUSH_RATE, DEFAULT_PUSH_RATE)) * 1000
    custom_url = entry.options.get(CONF_PUSH_URL)

    webhook_id = _ensure_webhook_ids(hass, entry)[channel.webhook_key]
    setattr(coordinator, channel.webhook_key, webhook_id)

    # Register webhook handler. HA's webhook registry is in-memory, so this
    # must run on every load regardless of whether the subscription itself
    # is reused below.
    _register_webhook(hass, coordinator, channel.webhook_key, webhook_id)

    webhook_url = await _async_resolve_webhook_url(hass, webhook_id, custom_url)
    if not webhook_url:
        _LOGGER.warning(channel.unresolved_warning)
        setattr(coordinator, channel.status_attr, "error")
        setattr(
            coordinator,
            channel.error_attr,
            "Could not resolve external URL — set a Custom Callback URL in options",
        )
        # Do not touch any existing subscription here -- an unresolved URL
        # doesn't mean the persisted subscription is bad, and it's not worth
        # burning it on a guess.
        return

    setattr(coordinator, channel.url_attr, webhook_url)

    device_sns = [sn for sn in coordinator.data if sn]
    if not device_sns:
        _LOGGER.debug("%s: no devices to subscribe", channel.label)
        setattr(coordinator, channel.status_attr, "inactive")
        return

    reused_code = _should_reuse_subscription(
        coordinator,
        entry,
        channel,
        webhook_url,
        device_sns,
        push_rate_ms,
    )
    if reused_code:
        _mark_active(coordinator, channel, reused_code)
        _LOGGER.debug(
            "%s: Reusing existing subscription (code: %s)",
            channel.label,
            mask_subscription_code(reused_code),
        )
        return

    # Something that requires a new subscription changed (or there's no
    # usable prior one) -- cancel any previous/orphaned code before
    # subscribing fresh.
    await _async_cancel_entry_subscription(hass, entry, coordinator, channel)
    await _async_cancel_conflicting_subscriptions(coordinator, channel, device_sns)

    _LOGGER.debug(
        "%s: Subscribing callback URL %s for devices: %s",
        channel.label,
        mask_url(webhook_url),
        [mask_sn(sn) for sn in device_sns],
    )
    await _async_execute_subscription(
        hass, entry, coordinator, channel, webhook_url, device_sns, push_rate_ms
    )


async def _async_teardown_push_subscription(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
    entry: ConfigEntry | None = None,
    force: bool = False,
    cancel_remote: bool = True,
) -> None:
    """Tear down the real-time data push subscription and webhook; see
    _async_teardown_subscription."""
    await _async_teardown_subscription(
        hass, entry, coordinator, _PUSH, force, cancel_remote
    )
    coordinator.push_enabled = False


async def _async_teardown_alarm_subscription(
    hass: HomeAssistant,
    coordinator: HyxiDataUpdateCoordinator,
    entry: ConfigEntry | None = None,
    force: bool = False,
    cancel_remote: bool = True,
) -> None:
    """Tear down the alarm push subscription and webhook; see
    _async_teardown_subscription."""
    await _async_teardown_subscription(
        hass, entry, coordinator, _ALARM, force, cancel_remote
    )


async def _async_teardown_subscription(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    hass: HomeAssistant,
    entry: ConfigEntry | None,
    coordinator: HyxiDataUpdateCoordinator,
    channel: _Channel,
    force: bool,
    cancel_remote: bool,
) -> None:
    """Tear down a channel's subscription and webhook.

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
    webhook_id = getattr(coordinator, channel.webhook_key, None)
    if webhook_id:
        try:
            webhook.async_unregister(hass, webhook_id)
        except KeyError:
            # Webhook was already unregistered (e.g. double-teardown on crash recovery)
            pass
        setattr(coordinator, channel.webhook_key, None)

    subscribe_code = getattr(coordinator, channel.code_attr, None)
    if subscribe_code:
        should_clear = await _async_maybe_cancel_subscription(
            coordinator, subscribe_code, channel, force, cancel_remote
        )
        if should_clear:
            setattr(coordinator, channel.code_attr, None)
            if entry is not None:
                _clear_subscription_entry_data(hass, entry, channel)

    setattr(coordinator, channel.status_attr, "inactive")
    setattr(coordinator, channel.url_attr, None)


def entry_subscription_codes(entry: ConfigEntry) -> list[str]:
    """The push and alarm subscription codes the entry holds."""
    return [
        code
        for channel in (_PUSH, _ALARM)
        if (code := entry.data.get(channel.code_key))
    ]


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

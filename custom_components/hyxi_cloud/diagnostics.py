"""Diagnostics for HYXI config entries."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_KEY,
    CONF_EM_INVERTER_SN,
    CONF_MODBUS_DEVICE,
    CONF_MODBUS_HOST,
    CONF_PUSH_URL,
    CONF_SECRET_KEY,
    is_sensitive_key,
    is_serial_key,
)
from .coordinator import HyxiConfigEntry, HyxiDataUpdateCoordinator

# Entry settings and fields whose values are always removed, besides the
# device fields is_sensitive_key matches.
TO_REDACT = frozenset(
    {
        CONF_ACCESS_KEY,
        CONF_SECRET_KEY,
        CONF_MODBUS_HOST,
        CONF_MODBUS_DEVICE,
        CONF_PUSH_URL,
        "push_subscribe_code",
        "push_subscribe_fingerprint",
        "alarm_subscribe_code",
        "alarm_subscribe_fingerprint",
        "webhook_id",
        "alarm_webhook_id",
        "legacy_webhooks",
        "subscribe_code",
        "callback_url",
        "device_name",
    }
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HyxiConfigEntry
) -> dict[str, Any]:
    """Return the entry's settings and, once loaded, its status, push state,
    subscriptions and devices: with its keys, addresses, callback URLs,
    webhook IDs, subscription codes and device names removed, and each
    serial number replaced by a placeholder."""
    coordinator = entry.runtime_data if entry.state is ConfigEntryState.LOADED else None
    diagnostics: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "state": entry.state.value,
            "data": dict(entry.data),
            "options": dict(entry.options),
        }
    }
    if coordinator is not None:
        diagnostics.update(_coordinator_diagnostics(coordinator))
    return _Scrubber(entry, coordinator, diagnostics).scrub(diagnostics)


def _coordinator_diagnostics(coordinator: HyxiDataUpdateCoordinator) -> dict[str, Any]:
    """The loaded entry's status, push state, subscriptions and devices."""
    engine = coordinator.engine
    return {
        "status": dict(coordinator.hyxi_metadata),
        "push": {
            "enabled": coordinator.push_enabled,
            "status": coordinator.push_status,
            "error": coordinator.push_error,
            "last_received": coordinator.last_push_received,
            "alarm_status": coordinator.alarm_push_status,
            "alarm_error": coordinator.alarm_push_error,
            "alarm_last_received": coordinator.alarm_last_push_received,
        },
        "subscriptions": (
            None
            if coordinator.subscriptions is None
            else [asdict(sub) for sub in coordinator.subscriptions]
        ),
        "energy_manager": (
            None
            if engine is None
            else {
                "managing": engine.is_managing,
                "decision": engine.decision,
                "current_mode": engine.current_mode,
            }
        ),
        "devices": coordinator.data,
    }


class _Scrubber:
    """Removes an entry's sensitive values from its diagnostics: by field,
    and wherever they appear in text, such as a title, an error or a device
    key."""

    def __init__(
        self,
        entry: HyxiConfigEntry,
        coordinator: HyxiDataUpdateCoordinator | None,
        diagnostics: dict[str, Any],
    ) -> None:
        """Collect what to replace in `diagnostics`, and by what."""
        secrets = set(_redacted_values(diagnostics))
        if coordinator is not None:
            # Built from the push URL and webhook ID, not stored.
            secrets.update(
                url for url in (coordinator.push_url, coordinator.alarm_push_url) if url
            )
        replacements = dict.fromkeys(secrets, REDACTED)
        serials = _serials(entry, coordinator)
        replacements.update(
            {sn: f"SN_{n}" for n, sn in enumerate(sorted(serials), start=1)}
        )
        self._replacements = replacements
        # Whole values only, longest first, so 192.168.1.5 doesn't match
        # inside 192.168.1.50.
        alternatives = "|".join(
            re.escape(old) for old in sorted(replacements, key=len, reverse=True)
        )
        self._pattern = (
            re.compile(rf"(?<![\w.])(?:{alternatives})(?![\w.])")
            if replacements
            else None
        )

    def scrub(self, value: Any) -> Any:
        """`value` with its sensitive fields and text replaced."""
        if isinstance(value, Mapping):
            return {
                self.scrub(key): (
                    REDACTED
                    if item not in (None, "") and _redacts(key)
                    else self.scrub(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.scrub(item) for item in value]
        if isinstance(value, str) and self._pattern is not None:
            return self._pattern.sub(
                lambda match: self._replacements[match.group(0)], value
            )
        return value


def _redacts(key: Any) -> bool:
    """Whether a field's value is always removed."""
    return key in TO_REDACT or is_sensitive_key(key)


def _redacted_values(value: Any) -> list[str]:
    """The text and numbers in `value`'s redacted fields, to remove wherever
    else they appear too."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _redacts(key) and isinstance(item, (str, int)) and item != "":
                found.append(str(item))
            else:
                found += _redacted_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found += _redacted_values(item)
    return found


def _serials(
    entry: HyxiConfigEntry, coordinator: HyxiDataUpdateCoordinator | None
) -> set[str]:
    """The serial numbers the entry knows of."""
    serials: set[str] = set()
    if em_sn := entry.options.get(CONF_EM_INVERTER_SN):
        serials.add(em_sn)
    if coordinator is not None:
        devices = coordinator.data or {}
        serials.update(devices)
        for dev_data in devices.values():
            metrics = dev_data.get("metrics") or {}
            serials.update(
                str(value)
                for key, value in metrics.items()
                if value and is_serial_key(key)
            )
        for sub in coordinator.subscriptions or ():
            serials.update(sub.devices)
    return serials

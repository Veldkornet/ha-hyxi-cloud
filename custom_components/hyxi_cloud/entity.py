"""Base entity for HYXI Cloud."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import HyxiDataUpdateCoordinator


def via_device_id(
    hass: HomeAssistant | None, config_entry_id: str, parent_sn: str
) -> str | None:
    """Resolve ``(DOMAIN, parent_sn)`` to a device registry id for ``DeviceInfo``.

    Like ``dr.async_get_device_id_by_identifier`` but returns ``None`` instead
    of raising when the parent isn't registered -- a device can report a
    ``parentSn`` for a collector that isn't itself polled, and that just reads
    as "no via device", the same as an unresolvable link used to. Also returns
    ``None`` for a hass-less entity (a device_info read before the entity is
    on a platform), rather than raising.
    """
    if hass is None:
        return None
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, parent_sn), config_entry_id
    )
    return device.id if device is not None else None


class HyxiEntity(CoordinatorEntity["HyxiDataUpdateCoordinator"]):
    """Base entity for HYXI Cloud."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HyxiDataUpdateCoordinator, sn: str, dev_data: dict
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }


class SettingsSyncMixin:
    """Adopt a Modbus settings-block value only from a genuinely fresh read.

    Shared by HyxiSettingNumber (number.py) and HyxiAntiStarvationSwitch
    (switch.py), the two entity kinds that seed their value from
    ModbusClient's settings block rather than the regular telemetry poll.
    Each host class implements only _apply_settings_metrics -- pulling its
    own value(s) out of a metrics dict already confirmed fresh and safe;
    _sync_from_settings and _handle_coordinator_update are the same for
    both and live here.

    Guards against two races a plain per-coordinator-update sync would hit:

    - A poll can read the settings block successfully and then still fail
      to publish anything new to coordinator.data, if every telemetry
      block afterwards fails (HyxiModbusClient.async_read_all raises before
      building a device dict). The freshness marker therefore travels
      *inside* metrics ("_settings_read_at", stamped by the same
      _build_metrics() call that produces the values it describes) instead
      of being read off the client directly -- a metrics dict that never
      got published carries neither the marker nor the stale values.
    - The settings block and a user's write share the same Modbus bus and
      the same coordinator poll can take several seconds (every telemetry
      block is read after settings, before listeners are notified), so a
      write can complete in between a read starting and that read's result
      reaching this entity. A read that started before this entity's own
      most recent write is never adopted, even if it is otherwise "new".
    """

    coordinator: Any
    _sn: str
    _last_write_at: float | None = None

    def _note_write(self) -> None:
        """Call once a write has landed on the device, before applying it
        optimistically -- marks every settings read older than this as
        unsafe to adopt."""
        self._last_write_at = time.monotonic()

    def _apply_settings_metrics(self, metrics: dict) -> None:
        """Adopt this entity's own value(s) out of a metrics dict that
        _settings_metrics has already confirmed is fresh and safe.

        Overridden by each host class: HyxiSettingNumber pulls its one
        definition key, HyxiAntiStarvationSwitch pulls
        anti_starvation_enabled.
        """
        raise NotImplementedError

    def _settings_metrics(self, dev_data: dict | None = None) -> dict | None:
        """The metrics to adopt a value from, or None if there is nothing
        safe to adopt.

        dev_data is the constructor's own snapshot on the very first call,
        since that predates this entity being registered with the
        coordinator; every later call (from _handle_coordinator_update)
        reads the coordinator's live data instead. Re-adopting the same
        read_at on a later, unrelated poll is harmless -- it just reassigns
        the value this entity already holds -- so nothing here needs to
        remember which read_at it last saw, only whether this one is safe.
        """
        if dev_data is None:
            dev_data = self.coordinator.data.get(self._sn) or {}
        metrics = dev_data.get("metrics") or {}
        read_at = metrics.get("_settings_read_at")
        if read_at is None:
            return None
        if self._last_write_at is not None and read_at <= self._last_write_at:
            return None
        return metrics

    @callback
    def _sync_from_settings(self, dev_data: dict | None = None) -> None:
        """Adopt the device's real value if a genuinely new, safe-to-use
        settings read is available."""
        metrics = self._settings_metrics(dev_data)
        if metrics is not None:
            self._apply_settings_metrics(metrics)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_from_settings()
        super()._handle_coordinator_update()  # type: ignore[misc]

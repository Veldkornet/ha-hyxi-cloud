"""Coordinator for the local Modbus transport.

Subclasses the cloud coordinator rather than standing beside it, because
everything downstream -- diagnostic sensors, the protection controllers, the
Energy Manager engine -- reaches for attributes established in that
__init__: hyxi_metadata, protection_controllers, engine, the device store,
and the push-state fields the subscription sensor reads even when push can
never be active. Only the fetch itself differs.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DEFAULT_MODBUS_INTERVAL
from .coordinator import HyxiDataUpdateCoordinator
from .modbus.client import ModbusClient

_LOGGER = logging.getLogger(__name__)


class HyxiModbusCoordinator(HyxiDataUpdateCoordinator):
    """Polls one device over RS485 and publishes the cloud data shape.

    Works with either device family's client -- HyxiModbusClient (HALO) or
    HyxiHybridModbusClient (hybrid) -- since both satisfy ModbusClient and
    nothing here reaches past that shared surface.
    """

    def __init__(
        self, hass: HomeAssistant, client: ModbusClient, entry: ConfigEntry
    ) -> None:
        """Initialize with a local polling interval."""
        super().__init__(hass, client, entry)

        # The parent reads update_interval as minutes, which is right for a
        # rate-limited cloud API and absurd for a wire. Local polling is
        # cheap, so the same option is seconds here.
        seconds = entry.options.get("update_interval", DEFAULT_MODBUS_INTERVAL)
        self.update_interval = timedelta(seconds=seconds)

        # Push is a HYXI webhook subscription; there is no local equivalent.
        # The subscription sensor reads these, so they are set explicitly
        # rather than left to mean "not yet started".
        self.push_status = "unavailable"
        self.alarm_push_status = "unavailable"

        _LOGGER.debug(
            "Modbus coordinator for entry %s polling every %ss",
            entry.title,
            seconds,
        )

    async def _async_update_data(self):
        """Read every register block and publish the result."""
        _LOGGER.debug(
            "Modbus poll starting for entry %s (interval %ss)",
            self.entry.entry_id,
            self.update_interval.total_seconds() if self.update_interval else None,
        )
        try:
            devices = await self.client.async_read_all()
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "Modbus poll failed for entry %s: %s (%s)",
                self.entry.entry_id,
                err,
                type(err).__name__,
            )
            self.hyxi_metadata["last_attempts"] += 1
            self.hyxi_metadata["last_error"] = str(err)
            self.hyxi_metadata["api_status"] = "Error"
            self.hyxi_metadata["cache_active"] = False
            raise UpdateFailed(f"Modbus read failed: {err}") from err

        try:
            await self.device_store.async_save(
                {"cached_at": dt_util.utcnow().isoformat(), "devices": devices}
            )
        except Exception as save_err:  # pylint: disable=broad-exception-caught
            # A cache write failing must never take the poll loop down.
            _LOGGER.warning("Failed to persist devices to storage: %s", save_err)

        self.hyxi_metadata["last_attempts"] = 1
        self.hyxi_metadata["last_success"] = dt_util.utcnow()
        self.hyxi_metadata["api_status"] = "Online"
        self.hyxi_metadata["cache_active"] = False
        self.hyxi_metadata["last_error"] = None

        _LOGGER.debug(
            "Modbus poll ok for entry %s: %d device(s), %d metric(s)",
            self.entry.entry_id,
            len(devices),
            sum(len(d.get("metrics") or {}) for d in devices.values()),
        )
        self._merge_metrics(devices)
        # Reuses the cloud coordinator's masked telemetry dump, so a debug
        # log reads the same whichever transport produced it.
        self._log_polled_telemetry(devices)
        await self._async_sync_device_metadata(devices)
        return devices

"""Minimal battery protection for HYXI inverter mode controls."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, detect_phase_type, mask_sn

if TYPE_CHECKING:
    from .coordinator import HyxiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

DEFAULT_SOC_MIN = 20
DEFAULT_SOC_MAX = 90
DEFAULT_SOC_MIN_HYSTERESIS = 2
DEFAULT_SOC_MAX_HYSTERESIS = 2
MODE_SWITCH_COOLDOWN = 60


class HyxiBatteryProtectionController:
    """Protect battery SOC limits for supported HYXI manual controls."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: HyxiDataUpdateCoordinator,
        sn: str,
    ) -> None:
        """Initialize the battery protection controller."""
        self._hass = hass
        self._coordinator = coordinator
        self._sn = sn
        self._last_sent_mode: str | None = None
        self._low_soc_hold = False
        self._high_soc_hold = False
        self._last_mode_switch = -999999.0
        self._unsub_listener: CALLBACK_TYPE | None = None
        self._eval_task: asyncio.Task | None = None

    @property
    def last_sent_mode(self) -> str | None:
        """Return the last tracked mode command."""
        return self._last_sent_mode

    async def async_start(self) -> None:
        """Start listening for coordinator updates."""
        if self._unsub_listener is not None:
            return

        # Proactively restore the last sent mode from HASS state registry if it exists
        entity_id = f"sensor.hyxi_{self._sn}_last_sent_mode"
        if (state := self._hass.states.get(entity_id)) is not None:
            mode = state.state
            if mode not in ("unknown", "unavailable", ""):
                self._last_sent_mode = mode
                self._last_mode_switch = time.monotonic()
                _LOGGER.debug(
                    "Proactively restored battery protection mode %s from HASS state for %s",
                    mode,
                    mask_sn(self._sn),
                )

        self._unsub_listener = self._coordinator.async_add_listener(
            self._handle_coordinator_update
        )
        await self.async_evaluate()

    async def async_stop(self) -> None:
        """Stop listening for coordinator updates."""
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        if self._eval_task is not None and not self._eval_task.done():
            self._eval_task.cancel()
            self._eval_task = None

    def note_manual_mode(self, mode: str) -> None:
        """Track a user-triggered mode command."""
        self._last_sent_mode = mode

    def restore_last_sent_mode(self, mode: str) -> None:
        """Restore the last tracked mode from restored state."""
        if mode not in {
            "idle",
            "charge",
            "discharge",
            "self_consume",
            "close",
            "stop",
            "hold",
        }:
            return

        self._last_sent_mode = mode
        self._last_mode_switch = time.monotonic()

    def should_block_manual_discharge(self) -> bool:
        """Return True when manual discharge should be blocked by SOC protection."""
        soc = self._get_soc()
        if soc is None:
            return False
        soc_min = self._get_param("soc_min", DEFAULT_SOC_MIN)
        return soc <= soc_min

    def should_block_manual_charge(self) -> bool:
        """Return True when manual charge should be blocked by SOC protection."""
        soc = self._get_soc()
        if soc is None:
            return False

        soc_max = self._get_param("soc_max", DEFAULT_SOC_MAX)
        soc_max_resume = max(
            0,
            soc_max
            - self._get_param(
                "soc_max_hysteresis_pct",
                DEFAULT_SOC_MAX_HYSTERESIS,
            ),
        )

        if soc >= soc_max:
            return True
        return self._high_soc_hold and soc > soc_max_resume

    @callback
    def _handle_coordinator_update(self) -> None:
        """Evaluate protection rules after new coordinator data arrives."""
        if self._eval_task is not None and not self._eval_task.done():
            self._eval_task.cancel()
        self._eval_task = self._hass.async_create_task(self.async_evaluate())

    async def async_evaluate(self) -> None:
        """Evaluate the current SOC and enforce protection limits."""
        dev_data = (self._coordinator.data or {}).get(self._sn)
        if not dev_data:
            return

        metrics = dev_data.get("metrics") or {}
        soc = self._metric_float(metrics.get("batSoc"))
        if soc is None:
            return

        soc_min = self._get_param("soc_min", DEFAULT_SOC_MIN)
        soc_max = self._get_param("soc_max", DEFAULT_SOC_MAX)
        phase = self._phase_type()
        soc_min_resume = min(
            soc_max,
            soc_min
            + self._get_param(
                "soc_min_hysteresis_pct",
                DEFAULT_SOC_MIN_HYSTERESIS,
            ),
        )
        soc_max_resume = max(
            soc_min,
            soc_max
            - self._get_param(
                "soc_max_hysteresis_pct",
                DEFAULT_SOC_MAX_HYSTERESIS,
            ),
        )

        if soc <= soc_min:
            self._low_soc_hold = True
            if self._last_sent_mode != "charge":
                await self._ensure_mode("hold" if phase == "single_phase" else "idle")
            return

        if self._low_soc_hold:
            if soc < soc_min_resume:
                if self._last_sent_mode != "charge":
                    await self._ensure_mode(
                        "hold" if phase == "single_phase" else "idle"
                    )
                return
            self._low_soc_hold = False

        if soc >= soc_max:
            self._high_soc_hold = True
            if phase == "single_phase":
                if self._last_sent_mode not in ("discharge", "hold"):
                    await self._ensure_mode("hold")
            elif self._last_sent_mode not in ("discharge", "idle", "self_consume"):
                await self._ensure_mode("idle")
            return

        if self._high_soc_hold:
            if soc > soc_max_resume:
                if phase == "single_phase":
                    if self._last_sent_mode not in ("discharge", "hold"):
                        await self._ensure_mode("hold")
                elif self._last_sent_mode not in (
                    "discharge",
                    "idle",
                    "self_consume",
                ):
                    await self._ensure_mode("idle")
                return
            self._high_soc_hold = False

    async def _ensure_mode(self, mode: str) -> None:
        """Send a mode command if cooldown allows it and mode changed."""
        if self._last_sent_mode == mode:
            return
        if (time.monotonic() - self._last_mode_switch) < MODE_SWITCH_COOLDOWN:
            return

        await self._send_control(mode)

        self._last_sent_mode = mode
        self._last_mode_switch = time.monotonic()
        await self._coordinator.async_request_refresh()

    async def _send_control(self, mode: str) -> None:
        """Send the requested control using the correct phase-specific API."""
        client = self._coordinator.client
        phase = self._phase_type()

        if phase == "three_phase":
            if mode == "idle":
                await client.set_mode_idle(self._sn)
            elif mode == "charge":
                await client.set_mode_charge(self._sn, self._get_power_value("charge"))
            elif mode == "discharge":
                await client.set_mode_discharge(
                    self._sn, self._get_power_value("discharge")
                )
            elif mode == "self_consume":
                await client.set_mode_self_consume(self._sn)
            else:
                raise ValueError(f"Unsupported three-phase protection mode: {mode}")
            return

        if phase == "single_phase":
            if mode not in {"close", "charge", "discharge", "stop", "hold"}:
                raise ValueError(f"Unsupported single-phase protection mode: {mode}")
            await client.set_peak_shaving(self._sn, mode)
            return

        raise ValueError(f"Unsupported phase type for protection: {phase}")

    def _get_param(self, key: str, default: int) -> int:
        """Read a protection number value from the entity registry."""
        unique_id = f"hyxi_{self._sn}_{key}"
        registry = er.async_get(self._hass)
        entity_id = registry.async_get_entity_id("number", DOMAIN, unique_id)
        if entity_id is None:
            return default

        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return default

        try:
            return int(float(state.state))
        except (
            ValueError,
            TypeError,
        ):
            return default

    def _get_power_value(self, direction: str) -> int:
        """Read the stored charge or discharge power value."""
        unique_id = f"hyxi_{self._sn}_{direction}_power"
        registry = er.async_get(self._hass)
        entity_id = registry.async_get_entity_id("number", DOMAIN, unique_id)
        if entity_id is None:
            return 100

        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return 100

        try:
            watts = int(float(state.state))
            return max(watts, 1)
        except (
            ValueError,
            TypeError,
        ):
            return 100

    def _get_soc(self) -> float | None:
        """Read the current battery SOC."""
        dev_data = (self._coordinator.data or {}).get(self._sn)
        if not dev_data:
            return None
        metrics = dev_data.get("metrics") or {}
        return self._metric_float(metrics.get("batSoc"))

    def _phase_type(self) -> str:
        """Return the detected phase type for this device."""
        dev_data = (self._coordinator.data or {}).get(self._sn) or {}
        return detect_phase_type(dev_data)

    @staticmethod
    def _metric_float(value) -> float | None:
        """Parse a coordinator metric as float."""
        if value is None:
            return None
        try:
            return float(value)
        except (
            ValueError,
            TypeError,
        ):
            return None

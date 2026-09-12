"""Minimal battery protection for HYXI inverter mode controls."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from hyxi_cloud_api import HyxiApiClient

from . import control_verify
from .const import DOMAIN, detect_phase_type, is_modbus_entry, mask_sn

if TYPE_CHECKING:
    from .coordinator import HyxiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

DEFAULT_SOC_MIN = 20
DEFAULT_SOC_MAX = 90
DEFAULT_SOC_MIN_HYSTERESIS = 2
DEFAULT_SOC_MAX_HYSTERESIS = 2
MODE_SWITCH_COOLDOWN = 60

# HYXI's "the current application does not have permission to call this
# API" response code. Confirmed as a platform-wide block for Micro ESS
# Power On/Off (see MICRO_ESS_CONTROL_SUPPORTED in const.py); whether a
# mode-control rejection here is the same kind of platform-wide block or
# a per-plant permission level is unconfirmed either way.
_PERMISSION_DENIED_CODE = "B003026"
# hyxi_cloud_api's ControlError messages are formatted as
# "... (code=<code>): <msg>" (see _execute_with_auth_retry/alter_alarm),
# so anchoring on "code=<code>)" -- rather than a bare substring search
# for _PERMISSION_DENIED_CODE -- avoids a false match on an unrelated
# code that merely starts with the same digits (e.g. "B0030261") or a
# request identifier/message that happens to quote the code.
_PERMISSION_DENIED_MARKER = f"code={_PERMISSION_DENIED_CODE})"


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
        # Which in-flight control write's result _last_sent_mode currently
        # reflects, if any -- lets a delayed query_control_result outcome
        # (_handle_device_rejected) tell whether it's still current or has
        # since been superseded by another send (automatic or manual).
        self._last_sent_trace_id: str | None = None
        self._low_soc_hold = False
        self._high_soc_hold = False
        self._last_mode_switch = -999999.0
        self._last_control_error_kind: str | None = None
        self._last_device_rejected_logged = False
        self._unsub_listener: CALLBACK_TYPE | None = None
        self._eval_task: asyncio.Task | None = None
        self._verify_tasks: set[asyncio.Task] = set()

    @property
    def last_sent_mode(self) -> str | None:
        """Return the last tracked mode command."""
        return self._last_sent_mode

    async def async_start(self) -> None:
        """Start listening for coordinator updates."""
        if self._unsub_listener is not None:
            return

        _LOGGER.debug(
            "Protection %s: battery protection controller starting", mask_sn(self._sn)
        )

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

    def stop(self) -> None:
        """Stop listening for coordinator updates."""
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
            _LOGGER.debug(
                "Protection %s: battery protection controller stopping",
                mask_sn(self._sn),
            )
        if self._eval_task is not None and not self._eval_task.done():
            self._eval_task.cancel()
        self._eval_task = None
        control_verify.cancel_pending(self._verify_tasks)

    def note_manual_mode(self, mode: str, trace_id: str | None = None) -> None:
        """Track a mode command sent outside _ensure_mode: a genuine
        manual command (control.py's button/service path, which does
        pass `trace_id` once it has one -- see async_send_battery_mode),
        or the Energy Manager notifying protection of its own mode
        changes (engine.py's _notify_protection, which doesn't -- EM
        tracks its own trace_id separately for its own verification).

        A change in `mode` always updates both fields, trace_id included
        (even to None), since tracking for the old mode is no longer
        relevant once the tracked mode itself has moved on. When `mode`
        merely reaffirms what's already tracked, a real trace_id (a new
        send, e.g. a double-clicked manual command) still replaces the
        old one -- the newest send is what a later rejection should be
        checked against -- but a bare reaffirmation with no trace_id of
        its own (EM's routine notifications never carry one) leaves
        whatever's already tracked alone, rather than silently cancelling
        a still-pending verification of that same mode.
        """
        if mode != self._last_sent_mode:
            self._last_sent_mode = mode
            self._last_sent_trace_id = trace_id
            return
        if trace_id is not None:
            self._last_sent_trace_id = trace_id

    def note_manual_mode_rejected(self, mode: str, trace_id: str) -> None:
        """A manually-issued `mode` (button/service, see control.py) was
        accepted by HYXI's cloud but rejected by the device.

        Delegates to the same trace_id-based staleness check
        _handle_device_rejected uses for protection's own automatic
        sends, now that note_manual_mode records the manual send's
        trace_id too -- so two same-mode sends in quick succession (e.g.
        a double-clicked button) are told apart correctly instead of
        matching on the mode string alone.
        """
        self._handle_device_rejected(mode, trace_id)

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
            _LOGGER.debug(
                "Protection %s: skipping evaluation, no coordinator data yet",
                mask_sn(self._sn),
            )
            return

        metrics = dev_data.get("metrics") or {}
        soc = self._metric_float(metrics.get("batSoc"))
        if soc is None:
            _LOGGER.debug(
                "Protection %s: skipping evaluation, batSoc metric missing",
                mask_sn(self._sn),
            )
            return

        soc_min = self._get_param("soc_min", DEFAULT_SOC_MIN)
        soc_max = self._get_param("soc_max", DEFAULT_SOC_MAX)
        mode_control = self._uses_mode_control()
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

        _LOGGER.debug(
            "Protection %s: soc=%.1f soc_min=%.1f soc_max=%.1f low_hold=%s high_hold=%s last_mode=%s",
            mask_sn(self._sn),
            soc,
            soc_min,
            soc_max,
            self._low_soc_hold,
            self._high_soc_hold,
            self._last_sent_mode,
        )

        if soc <= soc_min:
            await self._enter_low_soc_hold(soc, soc_min, mode_control)
            return

        if self._low_soc_hold and await self._maybe_stay_in_low_soc_hold(
            soc, soc_min_resume, mode_control
        ):
            return

        if soc >= soc_max:
            await self._enter_high_soc_hold(soc, soc_max, mode_control)
            return

        if self._high_soc_hold:
            await self._maybe_stay_in_high_soc_hold(soc, soc_max_resume, mode_control)

    async def _enter_low_soc_hold(
        self, soc: float, soc_min: float, mode_control: bool
    ) -> None:
        """SOC at/below minimum: enter (or refresh) the low-SOC hold."""
        if not self._low_soc_hold:
            _LOGGER.debug(
                "Protection %s: entering low-SOC hold (soc=%.1f <= soc_min=%.1f)",
                mask_sn(self._sn),
                soc,
                soc_min,
            )
        self._low_soc_hold = True
        if self._last_sent_mode != "charge":
            await self._ensure_mode("idle" if mode_control else "hold")

    async def _maybe_stay_in_low_soc_hold(
        self, soc: float, soc_min_resume: float, mode_control: bool
    ) -> bool:
        """Already in low-SOC hold: stay in it (True) if SOC hasn't
        recovered past the resume threshold yet, else clear the hold flag
        and signal the caller to keep evaluating (False).
        """
        if soc < soc_min_resume:
            if self._last_sent_mode != "charge":
                await self._ensure_mode("idle" if mode_control else "hold")
            return True
        _LOGGER.debug(
            "Protection %s: exiting low-SOC hold (soc=%.1f >= resume=%.1f)",
            mask_sn(self._sn),
            soc,
            soc_min_resume,
        )
        self._low_soc_hold = False
        return False

    async def _enter_high_soc_hold(
        self, soc: float, soc_max: float, mode_control: bool
    ) -> None:
        """SOC at/above maximum: enter (or refresh) the high-SOC hold."""
        if not self._high_soc_hold:
            _LOGGER.debug(
                "Protection %s: entering high-SOC hold (soc=%.1f >= soc_max=%.1f)",
                mask_sn(self._sn),
                soc,
                soc_max,
            )
        self._high_soc_hold = True
        await self._ensure_not_charging_over_max(mode_control)

    async def _maybe_stay_in_high_soc_hold(
        self, soc: float, soc_max_resume: float, mode_control: bool
    ) -> None:
        """Already in high-SOC hold: stay in it if SOC hasn't dropped past
        the resume threshold yet, else clear the hold flag.
        """
        if soc > soc_max_resume:
            await self._ensure_not_charging_over_max(mode_control)
            return
        _LOGGER.debug(
            "Protection %s: exiting high-SOC hold (soc=%.1f <= resume=%.1f)",
            mask_sn(self._sn),
            soc,
            soc_max_resume,
        )
        self._high_soc_hold = False

    async def _ensure_not_charging_over_max(self, mode_control: bool) -> None:
        """Send idle/hold unless the last sent mode already stopped
        charging. Shared by high-SOC hold entry and hold-refresh.
        """
        if mode_control:
            if self._last_sent_mode not in ("discharge", "idle", "self_consume"):
                await self._ensure_mode("idle")
        elif self._last_sent_mode not in ("discharge", "hold"):
            await self._ensure_mode("hold")

    async def _ensure_mode(self, mode: str) -> None:
        """Send a mode command if cooldown allows it and mode changed."""
        if self._last_sent_mode == mode:
            return
        remaining = MODE_SWITCH_COOLDOWN - (time.monotonic() - self._last_mode_switch)
        if remaining > 0:
            _LOGGER.debug(
                "Protection %s: mode switch to %s suppressed, cooldown %.0fs remaining",
                mask_sn(self._sn),
                mode,
                remaining,
            )
            return

        try:
            response = await self._send_control(mode)
        except HyxiApiClient.ControlError as err:
            # The write was rejected. Throttle retries to the cooldown and
            # don't let it escape as an unhandled task exception; log at
            # WARNING the first time a given kind of rejection is seen,
            # then quietly at DEBUG while that same kind keeps failing --
            # but a *different* kind of rejection (e.g. switching from
            # "under external control" to "permission denied", or back)
            # is WARNING again, since it's new, actionable information.
            self._last_mode_switch = time.monotonic()
            # Cloud-only: HYXI's API code. Modbus write failures never
            # carry this HYXI response code.
            if not is_modbus_entry(
                self._coordinator.entry
            ) and _PERMISSION_DENIED_MARKER in str(err):
                # HYXI's own API is refusing the write as unauthorized.
                # Whether that's a per-plant permission level, a
                # platform-side restriction on this control altogether (as
                # with Micro ESS, see MICRO_ESS_CONTROL_SUPPORTED in
                # const.py), or a credentials/authorization issue isn't
                # something we can tell apart from the response alone --
                # so the guidance doesn't guess and just points at HYXI
                # support.
                kind = "permission_denied"
                guidance = (
                    "HYXI's API rejected the write as unauthorized. "
                    "Contact HYXI support for assistance."
                )
            else:
                kind = "external_control"
                guidance = (
                    "The inverter may be under external control; turn off "
                    "Device Control & Protection for it if it is managed "
                    "elsewhere."
                )
            _LOGGER.log(
                logging.WARNING
                if kind != self._last_control_error_kind
                else logging.DEBUG,
                "Protection %s: could not set mode '%s' to keep SOC within "
                "limits: %s. %s",
                mask_sn(self._sn),
                mode,
                err,
                guidance,
            )
            self._last_control_error_kind = kind
            return

        self._last_control_error_kind = None
        self._last_sent_mode = mode
        self._last_sent_trace_id = control_verify.extract_trace_id(response)
        self._last_mode_switch = time.monotonic()
        self._maybe_verify_control_result(mode, self._last_sent_trace_id)
        await self._coordinator.async_request_refresh()

    def _maybe_verify_control_result(self, mode: str, trace_id: str | None) -> None:
        """Schedule a background check that the device actually applied
        `mode`, for a Cloud write with a traceId to look up.

        Modbus has no such concept -- a write either already raised above
        or is final, and extract_trace_id already returns None for its
        response shape -- but the transport is also checked explicitly
        here, so that stays true even if a future Modbus response shape
        ever coincidentally looked traceId-shaped.
        """
        if is_modbus_entry(self._coordinator.entry) or trace_id is None:
            return
        task = self._hass.async_create_task(
            control_verify.verify_control_result(
                self._coordinator.client,
                self._sn,
                "Protection",
                mode,
                trace_id,
                lambda result: self._on_verify_result(mode, trace_id, result),
            )
        )
        self._verify_tasks.add(task)
        task.add_done_callback(self._verify_tasks.discard)

    def _on_verify_result(self, mode: str, trace_id: str, result: str | None) -> None:
        """Handle the outcome control_verify.verify_control_result reports
        for one Cloud write; it already logged the confirm/timeout cases,
        so this only reacts to what protection's own state needs to know.

        A success only clears the throttle when it's still for the
        currently-tracked send -- a stale, superseded trace resolving
        successfully after a newer one has already been rejected must not
        reset the "already warned" flag for that newer, still-relevant
        rejection.
        """
        if result == control_verify.RESULT_SUCCESS:
            if self._last_sent_trace_id == trace_id:
                self._last_device_rejected_logged = False
        elif result == control_verify.RESULT_FAILURE:
            self._handle_device_rejected(mode, trace_id)
        # None (a lookup failure, or still "issuing"/unrecognized past the
        # attempt budget): nothing to correct here.

    def _handle_device_rejected(self, mode: str, trace_id: str) -> None:
        """HYXI's cloud accepted the write but the device itself rejected it.

        Unlike a transport-level ControlError (caught synchronously in
        _ensure_mode), this is only known once query_control_result
        confirms it, well after _ensure_mode already optimistically
        recorded `mode` as sent. `trace_id` identifies which send this is
        confirming: if a newer command (automatic or manual, see
        note_manual_mode) has since replaced it, this rejection is stale
        news about a mode nothing is relying on anymore, so it's logged
        quietly and _last_sent_mode is left alone. Otherwise, undo the
        optimistic record so the next evaluation actually retries once
        the mode-switch cooldown allows it, instead of assuming the
        device is already in `mode`.
        """
        if self._last_sent_trace_id != trace_id:
            _LOGGER.debug(
                "Protection %s: mode '%s' was rejected by the device, but a "
                "newer command has since been sent -- no action needed.",
                mask_sn(self._sn),
                mode,
            )
            return

        _LOGGER.log(
            logging.DEBUG if self._last_device_rejected_logged else logging.WARNING,
            "Protection %s: mode '%s' was accepted by HYXI's cloud but "
            "rejected by the device; will retry once the mode-switch "
            "cooldown allows it.",
            mask_sn(self._sn),
            mode,
        )
        self._last_device_rejected_logged = True
        self._last_sent_mode = None
        self._last_sent_trace_id = None

    async def _send_control(self, mode: str) -> dict:
        """Send the requested control using the correct transport-specific API.

        Returns the client's raw response so callers can pull a Cloud
        control's traceId out of it (a Modbus response carries no such
        field, but the interface is shared -- see modbus/client.py).
        """
        client = self._coordinator.client

        if is_modbus_entry(self._coordinator.entry):
            mode_control = True
        else:
            # Preserved from before this method understood transports: an
            # entry only reaches here via a real controller instance, which
            # __init__.py never creates for a cloud device whose phase came
            # back "unknown" -- so this is a defensive check for a phase
            # that changed after startup, not a case expected in practice.
            phase = self._phase_type()
            if phase not in ("three_phase", "single_phase"):
                raise ValueError(f"Unsupported phase type for protection: {phase}")
            mode_control = phase == "three_phase"

        _LOGGER.debug(
            "Protection %s: sending mode=%s mode_control=%s",
            mask_sn(self._sn),
            mode,
            mode_control,
        )

        if mode_control:
            if mode == "idle":
                return await client.set_mode_idle(self._sn)
            if mode == "charge":
                return await client.set_mode_charge(
                    self._sn, self._get_power_value("charge")
                )
            if mode == "discharge":
                return await client.set_mode_discharge(
                    self._sn, self._get_power_value("discharge")
                )
            if mode == "self_consume":
                return await client.set_mode_self_consume(self._sn)
            raise ValueError(f"Unsupported three-phase protection mode: {mode}")

        if mode not in {"close", "charge", "discharge", "stop", "hold"}:
            raise ValueError(f"Unsupported single-phase protection mode: {mode}")
        return await client.set_peak_shaving(self._sn, mode)

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
        except ValueError, TypeError:
            _LOGGER.debug(
                "Protection %s: could not parse %s state %r, using default %s",
                mask_sn(self._sn),
                entity_id,
                state.state,
                default,
            )
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
        except ValueError, TypeError:
            _LOGGER.debug(
                "Protection %s: could not parse %s state %r, using default 100",
                mask_sn(self._sn),
                entity_id,
                state.state,
            )
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

    def _uses_mode_control(self) -> bool:
        """Whether this device should be driven through the mode surface
        (idle/charge/discharge/self-use) rather than the cloud's separate
        peak-shaving surface (hold/charge/discharge/stop/close).

        Over the cloud this is decided by electrical phase count -- HYXI's
        controlId 1062-1065 (mode) applies to three-phase hardware,
        controlId 1021 (peak shaving) to single-phase. Local Modbus has no
        such split: both register maps this integration supports over
        Modbus expose the same 4-state mode surface regardless of phase
        count, and neither has a confirmed local equivalent of peak
        shaving's extra states -- so a Modbus device always uses mode
        control, the same decision button.py/number.py make. This must
        stay the one place that decision is made and read by both the SOC
        decision logic (which mode name to pick) and _send_control (which
        client method to call) -- if the two ever disagreed, _send_control
        would receive a mode name from the wrong vocabulary entirely (e.g.
        "hold", which set_mode_idle's surface has no meaning for).
        """
        if is_modbus_entry(self._coordinator.entry):
            return True
        return self._phase_type() == "three_phase"

    @staticmethod
    def _metric_float(value) -> float | None:
        """Parse a coordinator metric as float."""
        if value is None:
            return None
        try:
            return float(value)
        except ValueError, TypeError:
            return None

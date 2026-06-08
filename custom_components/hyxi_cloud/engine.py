# pylint: disable=too-many-lines
"""Energy Manager — decision engine for automated battery control.

Manages battery charging and operating mode based on:
  - Real-time P1 meter and solar production
  - Battery SOC limits (read from existing protection numbers)
  - Solar forecast predictions (sunset urgency)
  - High-load detection with battery assist

Modes used:
  - self_consume: default — inverter discharges to offset house load
  - charge: actively charge battery from solar excess
  - discharge: only used when SOC exceeds maximum
  - idle: stop all battery activity (night reserve depleted, unsafe high load)
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from hyxi_cloud_api import HyxiApiClient

from .const import (
    CONF_EM_LOOP_INTERVAL,
    DOMAIN,
    EM_DEFAULTS,
    EM_LOOP_INTERVAL,
    detect_phase_type,
    get_raw_device_code,
    mask_sn,
    normalize_device_type,
)

if TYPE_CHECKING:
    from .coordinator import HyxiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Default rolling average window for P1 readings (overridable via p1_smoothing_period param)
_P1_SMOOTHING_DEFAULT = 60


@dataclass
class EMEntityConfig:
    """Entity configuration for the Energy Manager engine."""

    sn: str
    p1_entity: str
    forecast_entity: str | None = None
    forecast_power_entity: str | None = None


@dataclass
class DecisionState:
    """Snapshot of current system state for the decision engine."""

    soc: float
    solar: float
    p1: float
    home_load: float
    soc_min: float
    soc_max: float
    max_charge: float
    max_discharge: float
    is_night: bool
    solar_producing: bool
    night_soc_target: float


@dataclass
class SolarConfig:
    """Computed solar charge parameters for a single decision tick."""

    min_solar_for_charge: float
    charge_margin: float
    charge_entry_threshold: float
    readings_needed: int
    sunset_urgent: bool


class EnergyManagerEngine:
    """Decision engine for automated battery management."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: HyxiDataUpdateCoordinator,
        config: EMEntityConfig,
    ) -> None:
        """Initialize the energy manager engine."""
        self._hass = hass
        self._coordinator = coordinator
        self._sn = config.sn
        self._p1_entity = config.p1_entity
        self._forecast_entity = config.forecast_entity
        self._forecast_power_entity = config.forecast_power_entity

        # State tracking
        self._last_mode_switch: float = -999999.0
        self._last_decision: str = ""
        self._last_action: str = ""
        self._in_decision: bool = False
        self._last_fast_path_trigger: float = 0
        self._last_power_adjust: float = -999999.0
        self._last_charge_exit: float = 0
        self._last_bottomout_exit: float = -999999.0
        self._charge_entry_export_count: int = 0
        self._charge_bottomout_count: int = 0
        self._current_mode: str | None = None
        self._last_sent_power: dict[str, int] = {"charge": 0, "discharge": 0}

        # PV curtailment state (peak shaving stop/hold cycling)
        self._pv_curtailed: bool = False
        self._last_pv_curtail_toggle: float = -999999.0

        # P1 rolling average
        self._p1_buffer: deque[tuple[float, float]] = deque()

        # Lifecycle
        self._enabled: bool = False
        self._unsub_listeners: list[CALLBACK_TYPE] = []

        # Sensor update callbacks
        self._update_callbacks: list[Callable[[], None]] = []

    # ── Properties for sensor entities ──────────────────────────────────

    @property
    def status(self) -> str:
        """Engine status: running, stopped, disabled, cooldown, error."""
        if not self._enabled:
            return "stopped"
        # Check if em_enabled switch is off
        em_enabled_uid = f"hyxi_{self._sn}_em_enabled"
        em_entity = self._find_entity_id("switch", em_enabled_uid)
        if em_entity and not self._get_ha_state_bool(em_entity, True):
            return "disabled"
        if self._last_decision == "error":
            return "error"
        cooldown = self._get_param("mode_switch_cooldown")
        if (time.monotonic() - self._last_mode_switch) < cooldown:
            return "cooldown"
        if self._dry_run:
            return "dry_run"
        return "running"

    @property
    def decision(self) -> str:
        """Current decision label."""
        return self._last_decision

    @property
    def last_action(self) -> str:
        """Last action taken."""
        return self._last_action

    @property
    def current_mode(self) -> str | None:
        """Last mode set by the engine."""
        return self._current_mode

    @property
    def enabled(self) -> bool:
        """Whether the engine loop is running."""
        return self._enabled

    @property
    def p1_avg(self) -> float:
        """Rolling 1-minute average of P1 readings."""
        if not self._p1_buffer:
            return 0.0
        return sum(v for _, v in self._p1_buffer) / len(self._p1_buffer)

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Start the engine loop and state listeners."""
        if self._enabled:
            return

        self._enabled = True
        _LOGGER.info("Energy Manager started for %s", mask_sn(self._sn))

        # Decision loop — interval from options or default
        interval = int(
            self._coordinator.entry.options.get(CONF_EM_LOOP_INTERVAL, EM_LOOP_INTERVAL)
        )
        self._unsub_listeners.append(
            async_track_time_interval(
                self._hass,
                self._loop_tick,
                timedelta(seconds=interval),
            )
        )

        # P1 state change listener for rolling average + high-load fast-path
        if self._p1_entity:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self._hass,
                    [self._p1_entity],
                    self._on_p1_change,
                )
            )

        # Low-SOC fast-path: listen for battery SOC changes
        soc_entity = self._find_entity_id("sensor", f"hyxi_{self._sn}_batsoc")
        if soc_entity:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self._hass,
                    [soc_entity],
                    self._on_soc_change,
                )
            )

        # Hourly night consumption estimate
        self._unsub_listeners.append(
            async_track_time_interval(
                self._hass,
                self._update_night_estimate,
                timedelta(hours=1),
            )
        )

        self._notify_sensors()

    async def async_stop(self) -> None:
        """Stop the engine loop and all listeners."""
        if not self._enabled:
            return

        self._enabled = False
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()
        _LOGGER.info("Energy Manager stopped for %s", mask_sn(self._sn))
        self._notify_sensors()

    def register_update_callback(self, cb: Callable[[], None]) -> None:
        """Register a callback for sensor updates after each decision."""
        self._update_callbacks.append(cb)

    def unregister_update_callback(self, cb: Callable[[], None]) -> None:
        """Remove a sensor update callback."""
        if cb in self._update_callbacks:
            self._update_callbacks.remove(cb)

    def _notify_sensors(self) -> None:
        """Notify all registered sensor entities to update."""
        for cb in self._update_callbacks:
            cb()

    # ── State reading helpers ───────────────────────────────────────────

    def _find_entity_id(self, domain: str, unique_id: str) -> str | None:
        """Look up an entity_id by unique_id via the entity registry."""
        registry = er.async_get(self._hass)
        return registry.async_get_entity_id(domain, DOMAIN, unique_id)

    def _get_coordinator_metric(self, key: str, default: float = 0.0) -> float:
        """Get a metric value from the coordinator data."""
        if not self._coordinator.data:
            return default
        dev_data = self._coordinator.data.get(self._sn)
        if not dev_data:
            return default
        metrics = dev_data.get("metrics") or {}
        val = metrics.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (
            ValueError,
            TypeError,
        ):
            return default

    def _get_ha_state_float(self, entity_id: str | None, default: float = 0.0) -> float:
        """Get a float value from an HA entity state."""
        if not entity_id:
            return default
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return default
        try:
            return float(state.state)
        except (
            ValueError,
            TypeError,
        ):
            return default

    def _get_ha_state_bool(self, entity_id: str | None, default: bool = False) -> bool:
        """Get a boolean from an HA switch entity state."""
        if not entity_id:
            return default
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state == "on"

    def _get_battery_capacity(self) -> float:
        """Get battery capacity in Wh.

        Priority: options override → batCap API metric → 2000 Wh fallback.
        """
        options = self._coordinator.entry.options
        if options.get("em_battery_capacity_override"):
            cap = options.get("em_battery_capacity_wh", 2000)
            try:
                return float(cap)
            except (
                ValueError,
                TypeError,
            ):
                # Ignore invalid battery capacity override value
                pass
        api_cap = self._get_coordinator_metric("batCap", 0)
        if api_cap > 0:
            return api_cap * 1000  # kWh -> Wh
        return 2000.0

    def _get_param(self, key: str) -> float:
        """Read a parameter value from its EM number/switch entity."""
        if key == "battery_capacity_wh":
            return self._get_battery_capacity()

        default = EM_DEFAULTS.get(key, 0)
        unique_id = f"hyxi_{self._sn}_em_{key}"
        entity_id = self._find_entity_id("number", unique_id)
        if not entity_id:
            # Try switch domain for boolean params
            entity_id = self._find_entity_id("switch", unique_id)
            if entity_id:
                return 1.0 if self._get_ha_state_bool(entity_id) else 0.0
            return float(default)
        return self._get_ha_state_float(entity_id, float(default))

    def _has_peak_shaving(self) -> bool:
        """Check if device supports peak shaving (controlId 1021).

        Peak shaving is single-phase only. Devices with this control can
        use 'stop' to curtail PV production when export limit is reached
        and the battery is full.
        """
        if not self._coordinator.data:
            return False
        dev_data = self._coordinator.data.get(self._sn)
        if not dev_data:
            return False
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        phase = detect_phase_type(dev_data)
        return phase == "single_phase" and device_type in (
            "hybrid_inverter",
            "all_in_one",
        )

    def _get_protection_param(self, key: str, default: float) -> float:
        """Read a value from the existing protection number entities."""
        unique_id = f"hyxi_{self._sn}_{key}"
        entity_id = self._find_entity_id("number", unique_id)
        if not entity_id:
            return default
        return self._get_ha_state_float(entity_id, default)

    def _get_soc(self) -> float:
        """Get battery state of charge."""
        return self._get_coordinator_metric("batSoc", 50)

    def _get_solar(self) -> float:
        """Get current solar power."""
        return self._get_coordinator_metric("ppv", 0)

    def _get_home_load(self) -> float:
        """Get current home load."""
        return self._get_coordinator_metric("home_load", 0)

    def _get_p1(self) -> float:
        """Get current P1 meter reading (positive=import, negative=export)."""
        return self._get_ha_state_float(self._p1_entity, 0)

    def _is_night(self) -> bool:
        """Check if it's nighttime (no solar and sun below horizon)."""
        solar = self._get_solar()
        if solar > 50:
            return False
        sun_state = self._hass.states.get("sun.sun")
        if sun_state is None:
            return solar < 50
        elevation = sun_state.attributes.get("elevation", 0)
        return elevation < 0

    def _hours_until_sunrise(self) -> float:
        """Calculate hours until next sunrise from sun.sun attributes."""
        sun_state = self._hass.states.get("sun.sun")
        if sun_state is None:
            return 12.0
        next_rising = sun_state.attributes.get("next_rising")
        if next_rising is None:
            return 12.0
        from homeassistant.util import dt as dt_util

        now = dt_util.utcnow()
        if isinstance(next_rising, str):
            next_rising = dt_util.parse_datetime(next_rising)
        if next_rising is None:
            return 12.0
        diff = (next_rising - now).total_seconds() / 3600
        return max(0.0, diff)

    def _hours_until_sunset(self) -> float:
        """Calculate hours until next sunset from sun.sun attributes."""
        sun_state = self._hass.states.get("sun.sun")
        if sun_state is None:
            return 12.0
        next_setting = sun_state.attributes.get("next_setting")
        if next_setting is None:
            return 12.0
        from homeassistant.util import dt as dt_util

        now = dt_util.utcnow()
        if isinstance(next_setting, str):
            next_setting = dt_util.parse_datetime(next_setting)
        if next_setting is None:
            return 12.0
        diff = (next_setting - now).total_seconds() / 3600
        return max(0.0, diff)

    # ── Protection integration ─────────────────────────────────────────

    def _get_protection_controller(self):
        """Get the existing battery protection controller for this SN."""
        return self._coordinator.protection_controllers.get(self._sn)

    def _notify_protection(self, mode: str) -> None:
        """Notify the protection controller about a mode change."""
        if controller := self._get_protection_controller():
            controller.note_manual_mode(mode)

    # ── Control methods: direct API calls matching stateless button controls ──

    @property
    def _dry_run(self) -> bool:
        """Check if dry-run mode is enabled in options."""
        return bool(self._coordinator.entry.options.get("em_dry_run", False))

    async def _set_mode(self, mode: str, power_w: int | None = None) -> bool:
        """Set operating mode via direct API call with cooldown enforcement."""
        cooldown = self._get_param("mode_switch_cooldown")
        if (time.monotonic() - self._last_mode_switch) < cooldown:
            _LOGGER.debug("EM: Mode switch to %s blocked by cooldown", mode)
            return False

        if mode in ("charge", "discharge"):
            power_w = int(max(1, min(power_w or 100, 10000)))

        if self._dry_run:
            action = f"{mode} @ {power_w}W" if power_w else mode
            _LOGGER.info(
                "EM DRY-RUN: Would set mode -> %s for %s", action, mask_sn(self._sn)
            )
            self._last_mode_switch = time.monotonic()
            prev_mode = self._current_mode
            self._current_mode = mode
            self._last_action = f"[dry-run] {action}"
            self._hass.bus.async_fire(
                "hyxi_em_mode_changed",
                {
                    "sn": self._sn,
                    "mode": mode,
                    "power": power_w,
                    "previous_mode": prev_mode,
                    "decision": self._last_decision,
                    "dry_run": True,
                },
            )
            self._notify_sensors()
            return True

        client: HyxiApiClient = self._coordinator.client
        try:
            if mode == "idle":
                await client.set_mode_idle(self._sn)
            elif mode == "charge":
                await client.set_mode_charge(self._sn, power_w)
            elif mode == "discharge":
                await client.set_mode_discharge(self._sn, power_w)
            elif mode == "self_consume":
                await client.set_mode_self_consume(self._sn)
            else:
                _LOGGER.error("EM: Unknown mode: %s", mode)
                return False

            self._last_mode_switch = time.monotonic()
            prev_mode = self._current_mode
            self._current_mode = mode
            if power_w and mode in ("charge", "discharge"):
                self._last_sent_power[mode] = power_w
            action = f"{mode} @ {power_w}W" if power_w else mode
            self._last_action = action
            _LOGGER.info("EM: Mode -> %s", action)

            # Fire HA event for automations / logging
            self._hass.bus.async_fire(
                "hyxi_em_mode_changed",
                {
                    "sn": self._sn,
                    "mode": mode,
                    "power": power_w,
                    "previous_mode": prev_mode,
                    "decision": self._last_decision,
                },
            )

            # Notify protection controller about the mode change
            self._notify_protection(mode)

            return True

        except HyxiApiClient.ControlError as err:
            _LOGGER.error("EM: Failed to set mode %s: %s", mode, err)
            return False

    async def _adjust_power(self, direction: str, target_w: int) -> bool:
        """Adjust charge/discharge power and resend command to inverter."""
        adjust_cooldown = self._get_param("power_adjust_cooldown")
        if (time.monotonic() - self._last_power_adjust) < adjust_cooldown:
            return False

        target_w = int(max(1, min(target_w, 10000)))
        threshold = self._get_param("power_change_threshold")

        # Check if power actually changed enough
        current_power = self._get_current_power_setting(direction)
        if abs(target_w - current_power) <= threshold and target_w > 100:
            return False

        if self._dry_run:
            _LOGGER.info(
                "EM DRY-RUN: Would adjust %s power -> %dW for %s",
                direction,
                target_w,
                mask_sn(self._sn),
            )
            self._last_power_adjust = time.monotonic()
            self._current_mode = direction
            return True

        client: HyxiApiClient = self._coordinator.client
        try:
            if direction == "charge":
                await client.set_mode_charge(self._sn, target_w)
            else:
                await client.set_mode_discharge(self._sn, target_w)

            self._last_power_adjust = time.monotonic()
            self._current_mode = direction
            self._last_sent_power[direction] = target_w
            self._last_action = f"{direction} @ {target_w}W"
            _LOGGER.debug("EM: %s power -> %dW", direction, target_w)

            # Notify protection controller about the mode change
            self._notify_protection(direction)
            self._notify_sensors()

            return True

        except HyxiApiClient.ControlError as err:
            _LOGGER.error("EM: Failed to adjust %s power: %s", direction, err)
            return False

    async def _set_peak_shaving(self, option: str) -> bool:
        """Send a peak shaving command (stop/hold) with cooldown.

        Used for PV curtailment on single-phase devices when battery is full
        and export exceeds limit. Minimum 30s between toggles to prevent
        oscillation.
        """
        pv_curtail_cooldown = 30
        if (time.monotonic() - self._last_pv_curtail_toggle) < pv_curtail_cooldown:
            _LOGGER.debug("EM: Peak shaving '%s' blocked by curtail cooldown", option)
            return False

        if self._dry_run:
            _LOGGER.info(
                "EM DRY-RUN: Would set peak shaving -> %s for %s",
                option,
                mask_sn(self._sn),
            )
            self._last_pv_curtail_toggle = time.monotonic()
            self._pv_curtailed = option == "stop"
            self._last_action = f"[dry-run] peak_shaving_{option}"
            self._notify_sensors()
            return True

        client: HyxiApiClient = self._coordinator.client
        try:
            await client.set_peak_shaving(self._sn, option)
            self._last_pv_curtail_toggle = time.monotonic()
            self._pv_curtailed = option == "stop"
            self._last_action = f"peak_shaving_{option}"
            _LOGGER.info("EM: Peak shaving -> %s for %s", option, mask_sn(self._sn))
            self._notify_sensors()
            return True
        except HyxiApiClient.ControlError as err:
            _LOGGER.error("EM: Failed to set peak shaving '%s': %s", option, err)
            return False

    async def _release_pv_curtailment(self) -> None:
        """Resume PV production after curtailment by sending peak shaving 'hold'."""
        if not self._pv_curtailed:
            return
        _LOGGER.info("EM: Releasing PV curtailment — resuming production")
        await self._set_peak_shaving("hold")
        self._pv_curtailed = False

    def _get_current_power_setting(self, direction: str) -> float:
        """Get the last-sent power for the given direction.

        Uses internal tracking (set by _set_mode/_adjust_power). Falls back
        to the number entity if it exists (e.g. three-phase power numbers).
        """
        tracked = self._last_sent_power.get(direction, 0)
        if tracked > 0:
            return tracked
        unique_id = f"hyxi_{self._sn}_{direction}_power"
        entity_id = self._find_entity_id("number", unique_id)
        if entity_id:
            return self._get_ha_state_float(entity_id, 0)
        return 0

    # ── Night consumption estimation ────────────────────────────────────

    def _estimate_night_consumption_wh(self) -> float:
        """Estimate energy needed to survive the night (Wh)."""
        avg_load = self._get_param("avg_night_consumption")
        hours_until_solar = self._hours_until_sunrise() + 1.0
        night_hours = 11
        hours_remaining = min(hours_until_solar, night_hours)
        wh_needed = avg_load * hours_remaining
        buffer_pct = self._get_param("night_buffer_pct") / 100
        wh_needed *= 1 + buffer_pct
        return wh_needed

    def _soc_needed_for_night(self) -> float:
        """Calculate the SOC percentage needed to survive the night."""
        wh_needed = self._estimate_night_consumption_wh()
        capacity = self._get_param("battery_capacity_wh")
        # Read soc_min from EXISTING protection number entity
        soc_min = self._get_protection_param("soc_min", 20)
        if capacity <= 0:
            capacity = 10000
        soc_pct_needed = (wh_needed / capacity) * 100
        return soc_min + soc_pct_needed

    # ── Solar forecast helpers ──────────────────────────────────────────

    def _get_forecast_remaining_wh(self) -> float:
        """Get remaining solar forecast for today in Wh."""
        remaining_kwh = self._get_ha_state_float(self._forecast_entity, 0)
        return remaining_kwh * 1000

    def _solar_will_cover_charge(self, target_soc: float) -> bool:
        """Check if forecasted solar can charge battery to target SOC."""
        current_soc = self._get_soc()
        capacity = self._get_param("battery_capacity_wh")
        wh_needed = capacity * (target_soc - current_soc) / 100
        if wh_needed <= 0:
            return True

        forecast_wh = self._get_forecast_remaining_wh()
        if forecast_wh > 0:
            usable_forecast = forecast_wh * 0.6
            return usable_forecast >= wh_needed

        # No forecast — estimate from current solar and time to sunset
        solar_now = self._get_solar()
        hours_to_sunset = self._hours_until_sunset()
        if hours_to_sunset <= 0 or solar_now <= 100:
            return False

        avg_night_load = self._get_param("avg_night_consumption")
        estimated_solar_wh = (solar_now / 2) * hours_to_sunset
        usable_wh = (estimated_solar_wh - avg_night_load * hours_to_sunset) * 0.8
        return usable_wh >= wh_needed

    # ── Battery energy calculation ──────────────────────────────────────

    def battery_energy_available_wh(self) -> float:
        """Calculate available battery energy above soc_min in Wh."""
        soc = self._get_soc()
        # Read soc_min from EXISTING protection number entity
        soc_min = self._get_protection_param("soc_min", 20)
        capacity = self._get_param("battery_capacity_wh")
        usable_pct = max(0, soc - soc_min)
        return capacity * usable_pct / 100

    # ── Main decision engine ────────────────────────────────────────────

    async def _make_decision(self) -> None:
        """Core decision logic. Called every EM_LOOP_INTERVAL seconds.

        Priority order:
          1. Safety: SOC below minimum -> emergency charge
          2. Safety: SOC above maximum -> forced discharge
          3. Sustained high load -> battery assist or idle
          4. Night -> self_consume until soc_min, then idle
          4b. Night preservation -> idle if SOC <= night target
          5. Solar optimization -> self_consume first, charge on sustained export
        """
        if self._in_decision:
            _LOGGER.debug(
                "EM: Decision engine already executing, skipping concurrent run"
            )
            return
        self._in_decision = True
        try:
            # Read soc_min/soc_max from EXISTING protection number entities
            solar = self._get_solar()
            s = DecisionState(
                soc=self._get_soc(),
                solar=solar,
                p1=self._get_p1(),
                home_load=self._get_home_load(),
                soc_min=self._get_protection_param("soc_min", 20),
                soc_max=self._get_protection_param("soc_max", 90),
                max_charge=self._get_param("max_charge_power"),
                max_discharge=self._get_param("max_discharge_power"),
                is_night=self._is_night(),
                solar_producing=solar > 50,
                night_soc_target=self._soc_needed_for_night(),
            )

            _LOGGER.debug(
                "EM TICK: SOC=%.0f%% P1=%.0fW solar=%.0fW load=%.0fW "
                "night=%s night_target=%.0f%%",
                s.soc,
                s.p1,
                s.solar,
                s.home_load,
                s.is_night,
                s.night_soc_target,
            )

            # PRIORITY 1 & 2: SOC safety limits
            if await self._check_soc_limits(s):
                return

            # PRIORITY 2b: Export limiting
            if await self._check_export_limit(s):
                return

            # PRIORITY 3: Sustained high load
            if await self._check_high_load(s):
                return

            # PRIORITY 4 & 4b: Night mode
            if await self._check_night(s):
                return

            # PRIORITY 5: Solar optimization
            if await self._check_solar(s):
                return

            # ── DEFAULT: self_consume as safe fallback ─────────────────────
            self._set_decision("idle_default")
            if self._current_mode in ("charge", "discharge"):
                await self._set_mode("self_consume")
        finally:
            self._in_decision = False

    async def _check_soc_limits(self, s: DecisionState) -> bool:
        """PRIORITY 1 & 2: SOC safety limits. Returns True if handled."""
        if s.soc <= s.soc_min:
            if s.solar_producing:
                charge_target = min(s.solar - 50, s.max_charge)
                charge_target = max(charge_target, 300)
                # Absorb grid export: if P1 negative, increase charge to
                # capture excess solar instead of wasting it to grid
                if s.p1 < 0:
                    charge_target = min(
                        charge_target + abs(int(s.p1)), int(s.max_charge)
                    )
                self._set_decision("emergency_solar_charge")
                if self._current_mode != "charge":
                    await self._set_mode("charge", int(charge_target))
                else:
                    await self._adjust_power("charge", int(charge_target))
                return True

            grid_charge_uid = f"hyxi_{self._sn}_em_grid_charge_allowed"
            grid_entity = self._find_entity_id("switch", grid_charge_uid)
            if self._get_ha_state_bool(grid_entity):
                grid_charge_w = min(2000, int(s.max_charge))
                self._set_decision("grid_charge_emergency")
                if self._current_mode != "charge":
                    await self._set_mode("charge", grid_charge_w)
                else:
                    await self._adjust_power("charge", grid_charge_w)
                return True
            self._set_decision("low_soc_idle")
            if self._current_mode != "idle":
                await self._set_mode("idle")
            return True

        if s.soc > s.soc_max:
            discharge_w = max(s.p1, 1000)
            discharge_w = min(discharge_w, s.max_discharge)
            self._set_decision("forced_discharge_over_max")
            if self._current_mode != "discharge":
                await self._set_mode("discharge", int(discharge_w))
            else:
                await self._adjust_power("discharge", int(discharge_w))
            return True

        return False

    async def _check_export_limit(self, s: DecisionState) -> bool:
        """PRIORITY 2b: Export limiting (single-phase only). Returns True if handled.

        Only applies to single-phase devices with peak shaving support
        (controlId 1021). Uses battery charge to absorb excess when SOC
        allows, and PV curtailment (stop/hold) when battery is full.
        Three-phase devices rely on existing mode controls instead.
        """
        if not self._has_peak_shaving():
            return False

        export_limit_uid = f"hyxi_{self._sn}_em_export_limiting"
        export_limit_entity = self._find_entity_id("switch", export_limit_uid)
        if not self._get_ha_state_bool(export_limit_entity, False):
            if self._pv_curtailed:
                await self._release_pv_curtailment()
            return False

        max_export = self._get_param("max_grid_export")
        if max_export <= 0:
            if self._pv_curtailed:
                await self._release_pv_curtailment()
            return False

        # P1 negative = exporting; check if export exceeds limit
        if s.p1 < -max_export:
            if s.soc < s.soc_max:
                # Battery has room — charge to absorb excess
                if self._pv_curtailed:
                    await self._release_pv_curtailment()
                excess = abs(s.p1) - max_export
                charge_target = min(excess, s.max_charge)
                charge_target = max(charge_target, 300)
                self._set_decision("export_limit_charge")
                if self._current_mode != "charge":
                    await self._set_mode("charge", int(charge_target))
                else:
                    await self._adjust_power("charge", int(charge_target))
                return True

            # Battery full — curtail PV via peak shaving stop
            self._set_decision("export_limit_pv_curtail")
            if not self._pv_curtailed:
                await self._set_peak_shaving("stop")
            return True

        # Export within limit — release curtailment if active
        if self._pv_curtailed:
            self._set_decision("export_limit_pv_resume")
            await self._release_pv_curtailment()
            return True

        # Were charging due to export limit but export is now within limit
        if (
            self._current_mode == "charge"
            and self._last_decision == "export_limit_charge"
        ):
            if s.p1 >= -max_export:
                self._set_decision("export_limit_ok")
                await self._set_mode("self_consume")
                return True

        return False

    async def _check_high_load(self, s: DecisionState) -> bool:
        """PRIORITY 3: Sustained high load. Returns True if handled."""
        # Check if high load feature is enabled by the user
        high_load_assist_uid = f"hyxi_{self._sn}_em_high_load_battery_assist"
        high_load_entity = self._find_entity_id("switch", high_load_assist_uid)
        high_load_assist = self._get_ha_state_bool(high_load_entity, False)
        if not high_load_assist:
            return False

        high_load_threshold = self._get_param("high_load_threshold")

        if s.home_load > high_load_threshold:
            high_load_wh = s.max_discharge * 0.5
            capacity = self._get_param("battery_capacity_wh")
            soc_cost = (high_load_wh / capacity) * 100 if capacity > 0 else 100

            if (s.soc - soc_cost) > s.night_soc_target:
                self._set_decision("high_load_battery_assist")
                if self._current_mode != "self_consume":
                    await self._set_mode("self_consume")
            else:
                self._set_decision("high_load_grid_only")
                if self._current_mode != "idle":
                    await self._set_mode("idle")
            return True

        return False

    async def _check_night(self, s: DecisionState) -> bool:
        """PRIORITY 4 & 4b: Night mode. Returns True if handled."""
        # Check if night mode feature is enabled by the user
        night_mode_uid = f"hyxi_{self._sn}_em_night_mode"
        night_mode_entity = self._find_entity_id("switch", night_mode_uid)
        if not self._get_ha_state_bool(night_mode_entity, False):
            return False

        if not s.solar_producing and s.is_night:
            if s.soc > s.soc_min:
                self._set_decision("night_self_consume")
                if self._current_mode != "self_consume":
                    await self._set_mode("self_consume")
            else:
                self._set_decision("night_reserve_hold")
                if self._current_mode != "idle":
                    await self._set_mode("idle")
            return True

        # Night battery preservation during daytime
        p1_avg = self.p1_avg
        if (
            not s.is_night
            and s.soc <= s.night_soc_target
            and s.p1 > 0
            and p1_avg > 0
            and not self._solar_will_cover_charge(s.night_soc_target)
        ):
            self._set_decision("night_preserve_idle")
            if self._current_mode != "idle":
                await self._set_mode("idle")
            return True

        return False

    async def _check_solar(self, s: DecisionState) -> bool:
        """PRIORITY 5: Solar optimization. Returns True if handled."""
        if s.solar_producing and s.soc < s.soc_max:
            await self._solar_charge_logic(s)
            return True

        if s.solar_producing and s.soc >= s.soc_max:
            self._set_decision("solar_battery_full")
            if self._current_mode != "self_consume":
                await self._set_mode("self_consume")
            return True

        return False

    async def _solar_charge_logic(self, s: DecisionState) -> None:
        """Solar charge entry/exit and power tuning logic."""
        min_solar_for_charge = self._get_param("min_solar_for_charge")
        charge_margin = self._get_param("charge_margin")
        charge_entry_threshold = self._get_param("charge_entry_threshold")
        charge_reentry_delay = self._get_param("charge_reentry_delay")
        readings_needed = max(int(charge_reentry_delay / 15 / 3), 2)

        # After a bottomout exit, double the readings needed
        bottomout_cooldown = self._get_param("bottomout_cooldown")
        if (time.monotonic() - self._last_bottomout_exit) < bottomout_cooldown:
            readings_needed = readings_needed * 2

        # Sunset urgency
        hours_to_sunset = self._hours_until_sunset()
        sunset_urgent = False
        if hours_to_sunset < 4 and s.soc < s.night_soc_target:
            if not self._solar_will_cover_charge(s.night_soc_target):
                sunset_urgent = True
                charge_entry_threshold = max(charge_entry_threshold // 2, 100)
                readings_needed = max(readings_needed // 2, 1)
                min_solar_for_charge = max(min_solar_for_charge - 300, 200)

        sc = SolarConfig(
            min_solar_for_charge=min_solar_for_charge,
            charge_margin=charge_margin,
            charge_entry_threshold=charge_entry_threshold,
            readings_needed=readings_needed,
            sunset_urgent=sunset_urgent,
        )

        if self._current_mode != "charge":
            await self._solar_entry_logic(s, sc)
        else:
            await self._solar_tune_logic(s, sc)

    async def _solar_entry_logic(
        self,
        s: DecisionState,
        sc: SolarConfig,
    ) -> None:
        """Handle charge entry decision when not currently charging."""
        if s.solar < sc.min_solar_for_charge:
            self._set_decision("solar_self_consume")
            if self._current_mode not in ("self_consume", "idle"):
                await self._set_mode("self_consume")
            self._charge_entry_export_count = 0

        elif s.p1 < -sc.charge_entry_threshold:
            self._charge_entry_export_count += 1
            if (
                self._charge_entry_export_count >= sc.readings_needed
                and s.solar >= sc.min_solar_for_charge
            ):
                charge_target = min(abs(s.p1) - sc.charge_margin - 100, s.solar - 500)
                charge_target = min(charge_target, s.max_charge)
                charge_target = max(charge_target, 300)
                decision = "pre_night_charge" if sc.sunset_urgent else "solar_charge"
                self._set_decision(decision)
                if await self._set_mode("charge", int(charge_target)):
                    self._charge_entry_export_count = 0
            else:
                self._set_decision("solar_export_waiting")
                if self._current_mode not in ("self_consume", "idle"):
                    await self._set_mode("self_consume")
        else:
            self._charge_entry_export_count = 0
            self._set_decision("solar_self_consume")
            if self._current_mode not in ("self_consume", "idle"):
                await self._set_mode("self_consume")

    async def _solar_tune_logic(
        self,
        s: DecisionState,
        sc: SolarConfig,
    ) -> None:
        """Fine-tune charge power when already in charge mode."""
        current_charge = self._get_current_power_setting("charge")
        solar_cap = max(s.solar - sc.charge_margin, 100)

        if s.solar < sc.min_solar_for_charge - 150:
            self._set_decision("solar_self_consume")
            self._last_charge_exit = time.monotonic()
            self._charge_entry_export_count = 0
            self._charge_bottomout_count = 0
            await self._set_mode("self_consume")

        elif s.p1 > sc.charge_margin:
            await self._solar_reduce_charge(
                s.p1, current_charge, solar_cap, sc.charge_margin
            )

        elif s.p1 < -(sc.charge_margin + 100):
            # Exporting too much — increase charge
            # Decrement bottomout counter (don't fully reset — volatile P1
            # can briefly dip negative between import spikes)
            self._charge_bottomout_count = max(0, self._charge_bottomout_count - 1)
            excess_export = abs(s.p1) - sc.charge_margin
            charge_target = current_charge + excess_export
            charge_target = min(charge_target, s.max_charge)
            charge_target = min(charge_target, solar_cap)
            self._set_decision("solar_charge")
            await self._adjust_power("charge", int(charge_target))
        else:
            # P1 within target range — balanced
            self._charge_bottomout_count = max(0, self._charge_bottomout_count - 1)
            self._set_decision("solar_charge")

    async def _solar_reduce_charge(
        self,
        p1,
        current_charge,
        solar_cap,
        charge_margin,
    ) -> None:
        """Reduce charge power when importing from grid.

        Uses dampened reduction: max 50% cut per tick to avoid volatile P1
        spikes crashing charge power from high values to 100W in one step.
        """
        desired_reduction = p1 + charge_margin
        max_step = max(current_charge * 0.5, 200)
        actual_reduction = min(desired_reduction, max_step)
        charge_target = current_charge - actual_reduction
        charge_target = min(charge_target, solar_cap)
        charge_target = max(charge_target, 100)

        if charge_target <= 100:
            self._charge_bottomout_count += 1
            if self._charge_bottomout_count >= 5:
                self._set_decision("solar_self_consume")
                self._last_charge_exit = time.monotonic()
                self._last_bottomout_exit = time.monotonic()
                self._charge_entry_export_count = 0
                self._charge_bottomout_count = 0
                await self._set_mode("self_consume")
            else:
                self._set_decision("solar_charge_reduced")
                await self._adjust_power("charge", 100)
        else:
            self._charge_bottomout_count = 0
            self._set_decision("solar_charge")
            await self._adjust_power("charge", int(charge_target))

    def _set_decision(self, decision: str) -> None:
        """Update the current decision label."""
        self._last_decision = decision
        self._notify_sensors()

    # ── Callbacks ───────────────────────────────────────────────────────

    async def _loop_tick(self, now) -> None:
        """15-second timer callback."""
        if not self._enabled:
            return

        # Staleness guard: auto-reload integration if data hasn't refreshed
        from homeassistant.util import dt as dt_util

        last_success = self._coordinator.hyxi_metadata.get("last_success")
        if last_success is not None:
            stale_seconds = (dt_util.utcnow() - last_success).total_seconds()
            if stale_seconds > 600:  # 10 minutes
                _LOGGER.warning(
                    "EM: Coordinator data stale (%.0f min) — reloading integration",
                    stale_seconds / 60,
                )
                persistent_notification.async_create(
                    self._hass,
                    f"Energy Manager detected stale data ({stale_seconds / 60:.0f} min). "
                    "Auto-reloading integration to restore updates.",
                    title="HYXI Cloud: Stale Data",
                    notification_id="hyxi_stale_data",
                )
                await self._hass.config_entries.async_reload(
                    self._coordinator.entry.entry_id
                )
                return

        # Check em_enabled switch — force self_consume on disable
        em_enabled_uid = f"hyxi_{self._sn}_em_enabled"
        em_entity = self._find_entity_id("switch", em_enabled_uid)
        if em_entity and not self._get_ha_state_bool(em_entity, True):
            if self._current_mode in ("charge", "discharge"):
                _LOGGER.info(
                    "EM: Disabled — forcing self_consume from %s for %s",
                    self._current_mode,
                    mask_sn(self._sn),
                )
                try:
                    await self._set_mode("self_consume")
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    HyxiApiClient.ControlError,
                ):
                    _LOGGER.debug("EM: Failed to force self_consume on disable")
                self._set_decision("disabled")
            return

        try:
            await self._make_decision()
        except (
            OSError,
            ValueError,
            TypeError,
            HyxiApiClient.ControlError,
        ):
            _LOGGER.exception("EM: Decision loop error")
            self._set_decision("error")
            # Safe fallback
            try:
                await self._set_mode("self_consume")
            except (
                OSError,
                ValueError,
                TypeError,
                HyxiApiClient.ControlError,
            ):
                _LOGGER.debug("EM: Fallback self_consume also failed")

    @callback
    def _on_p1_change(self, event) -> None:
        """Handle P1 state changes — update rolling average and high-load fast-path."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        try:
            value = float(new_state.state)
        except (
            ValueError,
            TypeError,
        ):
            return

        now = time.monotonic()
        self._p1_buffer.append((now, value))

        # Trim buffer to configurable window
        window = self._get_param("p1_smoothing_period") or _P1_SMOOTHING_DEFAULT
        cutoff = now - window
        while self._p1_buffer and self._p1_buffer[0][0] < cutoff:
            self._p1_buffer.popleft()

        # High-load fast-path: if home_load exceeds threshold, run decision immediately
        if not self._enabled:
            return

        home_load = self._get_home_load()
        threshold = self._get_param("high_load_threshold")
        if home_load > threshold:
            now = time.monotonic()
            if now - self._last_fast_path_trigger >= 15:
                self._last_fast_path_trigger = now
                self._hass.async_create_task(self._make_decision())

    @callback
    def _on_soc_change(self, event) -> None:
        """Handle SOC changes — low-SOC fast-path."""
        if not self._enabled:
            return

        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        try:
            soc = float(new_state.state)
        except (
            ValueError,
            TypeError,
        ):
            return

        soc_min = self._get_protection_param("soc_min", 20)
        if soc < soc_min:
            now = time.monotonic()
            if now - self._last_fast_path_trigger >= 15:
                self._last_fast_path_trigger = now
                self._hass.async_create_task(self._make_decision())

    async def _update_night_estimate(self, now) -> None:
        """Hourly night consumption update — EMA from P1 readings at night."""
        if not self._enabled:
            return

        from homeassistant.util import dt as dt_util

        hour = dt_util.now().hour
        if 21 <= hour or hour < 6:
            current_p1 = self._get_p1()
            if current_p1 > 0:
                prev = self._get_param("avg_night_consumption")
                new_avg = prev * 0.9 + current_p1 * 0.1
                _LOGGER.info(
                    "EM: Night consumption estimate: %.0fW (sample: %.0fW)",
                    new_avg,
                    current_p1,
                )

"""A local Modbus client shaped like the cloud client.

The integration's entity platforms, battery protection controller and Energy
Manager all reach their device through a handful of methods on
``coordinator.client``. Nothing in that surface mentions HTTP. Implementing
the same method names here, and emitting the same ``metrics`` vocabulary,
means sensor, binary_sensor, number, button, switch, protection and engine
work over RS485 without modification.

Two details make the substitution actually transparent:

Failures raise ``HyxiApiClient.ControlError``, because that is the class the
platforms catch by name. Raising anything else would surface as an unhandled
exception rather than a friendly "command failed".

Derived metrics come from ``HyxiApiClient.compute_derived_metrics``, the same
function the cloud coordinator uses, so ``home_load``, ``grid_import``,
``grid_export``, ``bat_charging`` and ``bat_discharging`` are computed
identically on both transports rather than reimplemented and drifting.

Deliberately free of Home Assistant imports: a candidate for extraction into
hyxi_cloud_api once a register map is hardware-confirmed.

Provenance and the list of what is still unverified: docs/modbus-provenance.md.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Protocol, runtime_checkable

from hyxi_cloud_api import HyxiApiClient
from modbus_connection import ModbusUnit
from modbus_connection.model import Component

from .registers import (
    TELEMETRY_COMPONENTS,
    HaloBackup,
    HaloBattery,
    HaloEnergy,
    HaloFaults,
    HaloGrid,
    HaloIdentity,
    HaloSettings,
    HaloStatus,
)

_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class ModbusClient(Protocol):
    """The shape both device-family clients present to the coordinator.

    HyxiModbusClient (below) and HyxiHybridModbusClient (client_hybrid.py)
    are unrelated classes with no shared base -- deliberately, since their
    register models genuinely differ -- but the coordinator and __init__.py
    only ever call through this surface, so a Protocol is what lets either
    one satisfy the same type hint without inventing an inheritance
    relationship neither class actually has.
    """

    ControlError: type[Exception]

    @property
    def serial_number(self) -> str: ...

    async def async_read_all(self) -> dict[str, dict]: ...
    async def set_mode_idle(self, device_sn: str) -> dict: ...
    async def set_mode_charge(self, device_sn: str, watts: int) -> dict: ...
    async def set_mode_discharge(self, device_sn: str, watts: int) -> dict: ...
    async def set_mode_self_consume(self, device_sn: str) -> dict: ...
    async def set_peak_shaving(self, device_sn: str, action: str) -> dict: ...
    def force_settings_refresh(self) -> None: ...


# The device type the HALO reports over the cloud API. Reused verbatim so
# normalize_device_type() resolves it to "micro_ess" on both transports and
# the entity platforms make the same decisions either way.
MICRO_ESS_DEVICE_CODE = "MICRO_STORAGE_ALL_IN_ONE"

# VPP dispatch mode 2, register 4147. Mode 3 ("selfuse") is deliberately
# not driven -- see set_mode_self_consume, which clears the dispatch enable
# (4146) instead so the device returns to its own configured work mode.
VPP_IDLE = 0
VPP_CHARGE = 1
VPP_DISCHARGE = 2

# How often async_read_settings re-reads the settings block instead of
# trusting its last read. Plain seconds against time.monotonic(), not a
# timedelta against wall-clock time, so a system clock change can't cause a
# spurious re-read (or an equally spurious month-long skip).
SETTINGS_REFRESH_SECONDS = 3600


def _mask(value: Any) -> str:
    """Mask a serial number for logs, matching the cloud path's format.

    Debug logs from this integration get pasted into GitHub issues, and a
    device serial identifies a specific installation.

    Produces the same digest as const.mask_sn, so one serial reads
    identically everywhere the integration logs it. Not the same as the API
    library's _mask_id, which salts per run -- that is deliberate there, and
    matching it would make correlation within our own logs impossible.

    Reimplemented rather than imported to keep this module free of Home
    Assistant, which const.py pulls in.
    """
    if value is None or str(value) == "None":
        return "****"
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]


def _hex_identifier(value: int | None) -> str | None:
    """Render an H64/H32 register as the identifier the device prints.

    The document specifies these as hexadecimal display values, so
    0x0010201234567810 is the serial number "10201234567810" rather than the
    decimal reading of those bytes.
    """
    if value is None:
        return None
    return f"{value:X}".lstrip("0") or "0"


def _to_watts(kilowatts: float | None) -> float | None:
    """Convert a kW-scaled register to the watts the metric vocabulary uses."""
    return None if kilowatts is None else round(kilowatts * 1000, 3)


def _enabled_when(raw: int | None, *, enabled_value: int) -> bool | None:
    """Resolve a raw 0/1 register to plain "enabled" boolean semantics, or
    None if the register wasn't read (rather than a real False).

    enabled_value is which raw value means enabled -- HALO and hybrid
    document opposite polarities for the same anti-starvation-protection
    concept (see set_anti_starvation/set_anti_starvation_protection), so
    each caller states its own instead of this guessing one.
    """
    return None if raw is None else raw == enabled_value


async def _read_settings_if_stale(
    settings: Component,
    last_attempt_at: float | None,
    last_confirmed_at: float | None,
    unit_id: int,
    logger: logging.Logger,
) -> tuple[float | None, float | None]:
    """Shared body of HyxiModbusClient/HyxiHybridModbusClient's
    async_read_settings -- attempt a settings read if last_attempt_at is
    stale or unset, and return (new_last_attempt_at, new_last_confirmed_at).

    See HyxiModbusClient.async_read_settings's docstring for why this is
    time-gated rather than part of the regular poll, and for why the two
    timestamps this returns are kept apart rather than being the one value
    the pre-fix version of this function returned. logger is the caller's
    own module logger, so a failure still logs under client.py or
    client_hybrid.py as appropriate rather than always under this one.
    """
    now = time.monotonic()
    if last_attempt_at is not None and now - last_attempt_at < SETTINGS_REFRESH_SECONDS:
        return last_attempt_at, last_confirmed_at
    try:
        await settings.async_update()
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.debug(
            "Modbus settings block unreadable on unit %s, number entities "
            "will fall back to their restored or minimum value: %s",
            unit_id,
            err,
        )
        return now, last_confirmed_at
    return now, now


class HyxiModbusClient:
    """Talks to one HYXI device over Modbus, presenting the cloud client's API."""

    #: Re-exported so callers can catch failures without importing the cloud
    #: client, matching how HyxiApiClient exposes it.
    ControlError = HyxiApiClient.ControlError

    #: The coordinator recomputes derived metrics after merging cached and
    #: fresh values, and reaches them through the client. Exposing the same
    #: staticmethod lets the shared merge logic work on either transport.
    compute_derived_metrics = staticmethod(HyxiApiClient.compute_derived_metrics)

    def __init__(self, unit: ModbusUnit, unit_id: int) -> None:
        """Bind to one unit from async_get_unit (see _build_modbus_coordinator).

        The connection beneath the handle is Home Assistant's -- shared
        across integrations on the bus and closed by HA -- so this class
        never holds or closes it. ``unit_id`` is kept for log lines and the
        serial-number fallback.
        """
        self._unit_id = unit_id
        self._unit = unit

        self.identity = HaloIdentity(unit)
        self.status = HaloStatus(unit)
        self.grid = HaloGrid(unit)
        self.backup = HaloBackup(unit)
        self.energy = HaloEnergy(unit)
        self.faults = HaloFaults(unit)
        self.battery = HaloBattery(unit)
        self.settings = HaloSettings(unit)

        self._components: dict[type[Component], Component] = {
            HaloStatus: self.status,
            HaloGrid: self.grid,
            HaloBackup: self.backup,
            HaloEnergy: self.energy,
            HaloFaults: self.faults,
            HaloBattery: self.battery,
        }
        self._serial: str | None = None
        self._identity_read = False
        self._settings_read_at: float | None = None
        self._settings_confirmed_at: float | None = None

    @property
    def serial_number(self) -> str:
        """The device serial, or a stable fallback if identity is unreadable.

        A device that answers telemetry but not its own identity registers
        still needs a stable key, or every poll would create new entities.
        """
        return self._serial or f"modbus_{self._unit_id}"

    async def async_read_identity(self) -> None:
        """Read the static identity block once, tolerating its absence.

        Identity is a convenience, not a precondition: a device that serves
        telemetry but rejects the identity block still has usable sensors,
        and serial_number falls back to a stable per-unit key. Failing the
        whole poll here would trade every metric for a model string.
        """
        if self._identity_read:
            return
        try:
            await self.identity.async_update()
            self._serial = _hex_identifier(self.identity.serial_number)
            _LOGGER.debug(
                "Modbus identity on unit %s: serial=%s model=%s arm=%s dsp=%s "
                "hw=%s rated=%sW/%sHz/%sV",
                self._unit_id,
                _mask(self._serial),
                _hex_identifier(self.identity.model_low),
                _hex_identifier(self.identity.arm_version),
                _hex_identifier(self.identity.dsp_version),
                _hex_identifier(self.identity.hardware_version),
                self.identity.rated_power,
                self.identity.rated_frequency,
                self.identity.rated_voltage,
            )
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "Modbus identity block unreadable, falling back to unit id "
                "for this device's key: %s",
                err,
            )
        self._identity_read = True

    async def async_read_settings(self) -> None:
        """Read the settings block at startup, then every
        SETTINGS_REFRESH_SECONDS, so number/switch entities can show the
        device's actual current value instead of always starting at 0.

        Not in TELEMETRY_COMPONENTS: settings change rarely, so reading the
        block on every single poll forever isn't worth the extra bus traffic,
        and every set_* method already keeps HA's own state in sync with
        what it writes. Re-reading hourly only exists to catch a change made
        by something other than this session (the app, another Modbus
        master) -- rare enough that an hour's staleness is fine, and cheap
        enough (one extra block read an hour) not to matter against a poll
        that already runs every few seconds. Tolerates failure the same way
        identity does: entities fall back to their restored or minimum
        value instead. _settings_read_at (the throttle) still advances on a
        failed attempt, so a device that is briefly unreachable doesn't get
        hammered with a retry on every poll until it is -- but
        _settings_confirmed_at, what actually reaches entities, does not: a
        failed attempt leaves the settings fields at whatever an earlier
        successful read left them, and re-publishing that stale snapshot
        with a "just confirmed" timestamp would let SettingsSyncMixin
        mistake it for genuinely fresh data and revert a write that landed
        after the last real success.

        _settings_confirmed_at is embedded into _build_metrics()'s own
        output ("_settings_read_at") rather than exposed as a client
        property: a poll that reads settings fine but then fails every
        telemetry block never calls _build_metrics() at all (async_read_all
        raises first), so the marker and the values it describes only ever
        reach coordinator.data together -- see entity.py's SettingsSyncMixin,
        which relies on that to avoid adopting data from a poll that never
        actually got published.
        """
        (
            self._settings_read_at,
            self._settings_confirmed_at,
        ) = await _read_settings_if_stale(
            self.settings,
            self._settings_read_at,
            self._settings_confirmed_at,
            self._unit_id,
            _LOGGER,
        )

    def force_settings_refresh(self) -> None:
        """Make the next async_read_settings re-read the block regardless
        of the refresh window -- backs the manual "Refresh Settings"
        button, for a user who just changed something from the app or
        another Modbus master and doesn't want to wait for the hourly
        window to notice."""
        self._settings_read_at = None

    async def async_read_all(self) -> dict[str, dict]:
        """Poll the device and return it in the coordinator's data shape.

        Components are read one at a time so a device that does not carry a
        block -- the off-grid registers on a unit with no backup port, say --
        loses only that block rather than the whole poll.
        """
        await self.async_read_identity()
        await self.async_read_settings()

        failed: list[str] = []
        started = time.monotonic()
        for component_type in TELEMETRY_COMPONENTS:
            component = self._components[component_type]
            block_started = time.monotonic()
            try:
                await component.async_update()
            except Exception as err:  # pylint: disable=broad-exception-caught
                failed.append(component_type.__name__)
                _LOGGER.debug(
                    "Modbus block %s failed after %.0fms: %s (%s)",
                    component_type.__name__,
                    (time.monotonic() - block_started) * 1000,
                    err,
                    type(err).__name__,
                )
            else:
                _LOGGER.debug(
                    "Modbus block %s read in %.0fms",
                    component_type.__name__,
                    (time.monotonic() - block_started) * 1000,
                )

        _LOGGER.debug(
            "Modbus poll of unit %s finished in %.0fms: %d/%d blocks read",
            self._unit_id,
            (time.monotonic() - started) * 1000,
            len(TELEMETRY_COMPONENTS) - len(failed),
            len(TELEMETRY_COMPONENTS),
        )

        if len(failed) == len(TELEMETRY_COMPONENTS):
            raise self.ControlError(
                "No Modbus register block could be read from the device"
            )
        if failed:
            _LOGGER.warning(
                "Modbus poll incomplete, these blocks did not answer: %s",
                ", ".join(failed),
            )

        return {self.serial_number: self._build_device()}

    def _build_device(self) -> dict:
        """Assemble one device entry in the shape the coordinator publishes."""
        metrics = self._build_metrics()
        derived = HyxiApiClient.compute_derived_metrics(metrics, MICRO_ESS_DEVICE_CODE)
        metrics.update(derived)

        return {
            "sn": self.serial_number,
            "device_name": "HYXI HALO",
            "model": _hex_identifier(self.identity.model_low) or "HYX-MS3000AC",
            "device_type_code": MICRO_ESS_DEVICE_CODE,
            "sw_version": _hex_identifier(self.identity.arm_version),
            "hw_version": _hex_identifier(self.identity.hardware_version),
            "metrics": metrics,
        }

    def _build_metrics(self) -> dict:
        """Translate register fields into the cloud client's metric keys.

        Only unambiguous mappings are made. Where the register map has no
        clear cloud counterpart the value is left out rather than guessed --
        an absent sensor is recoverable, a wrong one silently poisons
        history and automations.

        Note the units: gridP is kilowatts because compute_derived_metrics
        requires that for Micro ESS devices, while every other power metric
        is watts, matching what the cloud path stores.
        """
        raw: dict[str, Any] = {
            # Status
            "invSts": self.status.work_state,
            # Passed through raw. The cloud path's own workMode values are an
            # unconfirmed APK inference that the vendor document contradicts,
            # so no translation is applied in either direction here.
            # See docs/modbus-provenance.md, rule 1.
            "workMode": self.status.work_mode,
            "gridSts": self.status.grid_state,
            "deviceSwitchStatus": self.status.switch_status,
            "vbus": self.status.bus_voltage,
            "tinv": self.status.ac_temperature,
            "ambientTemper": self.status.ambient_temperature,
            "dcSideTemper": self.status.dc_temperature,
            # Unit unconfirmed -- the document gives no scale for either
            # field, so these pass through as the raw register value rather
            # than guessing a unit and risking a wrong one in history.
            "insulationResistance": self.status.insulation_resistance,
            "leakageCurrent": self.status.leakage_current,
            "meterOnline": self.status.meter_online,
            # Nameplate ratings, static -- read once with identity, not
            # re-polled, but published on every metrics build like batSn.
            "ratedPower": self.identity.rated_power,
            "ratedFrequency": self.identity.rated_frequency,
            "ratedVoltage": self.identity.rated_voltage,
            # ARM is the primary control processor, DSP a real-time
            # co-processor for power electronics -- the same
            # primary/secondary split the cloud API's "Master"/"Secondary"
            # labels describe. sw_version above already treats arm_version
            # as *the* firmware version for the device page on this same
            # reasoning.
            "swVerMaster": _hex_identifier(self.identity.arm_version),
            "swVerSlave": _hex_identifier(self.identity.dsp_version),
            # Grid side. gridP stays in kW -- see the docstring. gridQ/gridAp
            # reuse _to_watts for its *1000 scaling only -- the register is
            # kW-scaled regardless of whether the quantity is real, reactive
            # or apparent power.
            "gridP": self.grid.active_power,
            "gridQ": _to_watts(self.grid.reactive_power),
            "gridAp": _to_watts(self.grid.apparent_power),
            "gridPfd": self.grid.power_factor,
            "f": self.grid.frequency,
            "gridF": self.grid.frequency,
            "ph1v": self.grid.voltage,
            "ph1i": self.grid.current,
            "ph1p": _to_watts(self.grid.phase_power),
            # Off-grid side feeds the backup load sensor, plus its own
            # frequency/power/voltage/current now that those registers have
            # somewhere to go.
            "offGridF": self.backup.frequency,
            "offGridP": _to_watts(self.backup.active_power),
            "offGridV": self.backup.voltage,
            "offGridI": self.backup.current,
            "ph1Loadp": _to_watts(self.backup.phase_power),
            # Energy counters
            "eToday": self.energy.output_today,
            "eTodayIn": self.energy.input_today,
            "totalE": self.energy.output_total,
            "totalEnt": self.energy.input_total,
            "totalEchg": self.energy.battery_charged_total,
            "totalEdchg": self.energy.battery_discharged_total,
            "batCharge": self.energy.battery_charged_total,
            "batDisCharge": self.energy.battery_discharged_total,
            "bat_charge_total": self.energy.battery_charged_total,
            "bat_discharge_total": self.energy.battery_discharged_total,
            # Battery
            "batSn": _hex_identifier(self.identity.battery_serial_number),
            "packNum": self.battery.pack_count,
            "bmsState": self.battery.bms_state,
            "batSoc": self.battery.soc,
            "batSoh": self.battery.soh,
            "batTmp": self.battery.temperature,
            "batP": _to_watts(self.battery.power),
            "pbat": _to_watts(self.battery.power),
            "batVch": self.battery.cell_voltage_max,
            "batVcl": self.battery.cell_voltage_min,
            "batTch": self.battery.cell_temperature_max,
            "batTcl": self.battery.cell_temperature_min,
            # Raw BMS alarm words, undecoded -- see HaloFaults' docstring for
            # why bits are not named yet.
            "batAlarm1": self.battery.alarm_1,
            "batAlarm2": self.battery.alarm_2,
            "batAlarm3": self.battery.alarm_3,
            "batCapacityAh": self.battery.capacity_ah,
            "maxChargePower": _to_watts(self.battery.max_charge_power),
            "maxDischargePower": _to_watts(self.battery.max_discharge_power),
            # Current settings-register values, for number entities to show
            # on load instead of always starting at their minimum -- see
            # async_read_settings. Keyed to match HyxiSettingNumberDef.key
            # in number.py exactly. feed_in_power_limit is the one field
            # whose entity unit (W) differs from the register's own scale
            # (kW, see HaloSettings) -- set_feed_in_power_limit divides by
            # 1000 on write, so this multiplies back on read.
            "feed_in_power_limit": _to_watts(self.settings.feed_in_power_limit),
            "vpp_min_soc": self.settings.vpp_min_soc,
            "force_charge_start_soc": self.settings.force_charge_start_soc,
            "force_charge_stop_soc": self.settings.force_charge_stop_soc,
            "off_grid_min_soc": self.settings.off_grid_min_soc,
            "self_use_soc": self.settings.self_use_soc,
            "discharge_min_soc": self.settings.discharge_min_soc,
            # 0 disabled, 1 enabled -- direct, unlike the hybrid client's
            # inverted anti_starvation_protection (see set_anti_starvation).
            "anti_starvation_enabled": _enabled_when(
                self.settings.anti_starvation, enabled_value=1
            ),
            # See async_read_settings: this is _settings_confirmed_at, not
            # the throttle -- it travels with the values above so
            # entity.py's SettingsSyncMixin can tell a metrics dict that
            # reflects a genuinely new, successful settings read apart from
            # one that doesn't, rather than reading it off the client
            # directly.
            "_settings_read_at": self._settings_confirmed_at,
        }
        return {key: value for key, value in raw.items() if value is not None}

    # --- Control surface, matching HyxiApiClient method for method ---------

    async def _write_vpp(self, mode: int, watts: int | None = None) -> dict:
        """Drive the VPP dispatch block, enabling it first.

        Register 4147 is only consulted while 4146 enables dispatch mode 2,
        so the enable is written on every command rather than assumed. That
        also recovers automatically if something else cleared it.
        """
        try:
            await self.settings.write("vpp_enable", 1)
            if watts is not None:
                field = (
                    "vpp_charge_power" if mode == VPP_CHARGE else "vpp_discharge_power"
                )
                await self.settings.write(field, int(watts))
            await self.settings.write("vpp_mode", mode)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "Modbus VPP write failed on unit %s (mode=%s, power=%s): %s",
                self._unit_id,
                mode,
                watts,
                err,
            )
            raise self.ControlError(f"Modbus write failed: {err}") from err
        _LOGGER.debug(
            "Modbus VPP write ok on unit %s: 4146=1, 4147=%s, power=%s",
            self._unit_id,
            mode,
            watts,
        )
        return {"code": "0", "msg": "ok"}

    async def set_mode_idle(self, device_sn: str) -> dict:
        """Hold the battery: no charge, no discharge."""
        _LOGGER.debug("Modbus: set idle on %s", _mask(device_sn))
        return await self._write_vpp(VPP_IDLE)

    async def set_mode_charge(self, device_sn: str, watts: int) -> dict:
        """Charge at a fixed power."""
        _LOGGER.debug("Modbus: charge %sW on %s", watts, _mask(device_sn))
        return await self._write_vpp(VPP_CHARGE, watts)

    async def set_mode_discharge(self, device_sn: str, watts: int) -> dict:
        """Discharge at a fixed power."""
        _LOGGER.debug("Modbus: discharge %sW on %s", watts, _mask(device_sn))
        return await self._write_vpp(VPP_DISCHARGE, watts)

    async def set_mode_self_consume(self, device_sn: str) -> dict:
        """Hand control back to the inverter's own logic.

        Clears the VPP dispatch enable (register 4146) rather than writing
        VPP mode 3. Mode 3 self-consumes but stays under dispatch, so the
        app keeps showing "VPP mode" and native modes (self-use, TOU) stay
        suppressed. Clearing 4146 drops dispatch entirely and the device
        resumes whatever work mode (4024) the user configured -- the local
        mirror of the hybrid client disabling its scheduling register.
        """
        _LOGGER.debug("Modbus: self-consume on %s", _mask(device_sn))
        await self._write_setting("vpp_enable", 0, 4146)
        return {"code": "0", "msg": "ok"}

    async def set_peak_shaving(self, device_sn: str, action: str) -> dict:
        """Limit or release export.

        The cloud implements this through a peak-shaving control; locally
        there is a real export limit register, so "on" closes the feed-in
        switch outright rather than approximating it.
        """
        _LOGGER.debug("Modbus: peak shaving %s on %s", action, _mask(device_sn))
        gate_closed = action in ("on", "enable", "start")
        try:
            await self.settings.write("feed_in_enable", 0 if gate_closed else 1)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "Modbus peak shaving write failed on unit %s (action=%s): %s",
                self._unit_id,
                action,
                err,
            )
            raise self.ControlError(f"Modbus write failed: {err}") from err
        _LOGGER.debug(
            "Modbus peak shaving write ok on unit %s: 4162=%s",
            self._unit_id,
            0 if gate_closed else 1,
        )
        return {"code": "0", "msg": "ok"}

    async def _write_setting(self, field: str, value: Any, register: int) -> None:
        """Write one HaloSettings field, wrapping failures uniformly.

        A shared helper rather than repeating the try/except in every
        setting method below -- unlike _write_vpp and set_peak_shaving,
        these are plain single-field writes with nothing else to sequence.
        """
        try:
            await self.settings.write(field, value)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "Modbus %s write failed on unit %s (value=%s): %s",
                field,
                self._unit_id,
                value,
                err,
            )
            raise self.ControlError(f"Modbus write failed: {err}") from err
        _LOGGER.debug(
            "Modbus %s write ok on unit %s: %s=%s",
            field,
            self._unit_id,
            register,
            value,
        )

    async def set_feed_in_power_limit(self, watts: int) -> None:
        """Write the export power limit.

        The register is kW-scaled; the entity works in watts to match every
        other power control in this integration, so the conversion happens
        here rather than asking the caller to know the register's scale.
        """
        await self._write_setting("feed_in_power_limit", watts / 1000, 4163)

    async def set_vpp_min_soc(self, percent: int) -> None:
        """Write the minimum SOC the VPP dispatch block will discharge below.

        Register 4152 in holding space -- not grid active power, which is
        the same address in input space. See registers.py's HaloGrid.
        """
        await self._write_setting("vpp_min_soc", percent, 4152)

    async def set_force_charge_start_soc(self, percent: int) -> None:
        """Write the anti-starvation forced-charge start SOC."""
        await self._write_setting("force_charge_start_soc", percent, 4132)

    async def set_force_charge_stop_soc(self, percent: int) -> None:
        """Write the anti-starvation forced-charge stop SOC."""
        await self._write_setting("force_charge_stop_soc", percent, 4140)

    async def set_off_grid_min_soc(self, percent: int) -> None:
        """Write the minimum SOC while off grid (EPS/backup reserve)."""
        await self._write_setting("off_grid_min_soc", percent, 4133)

    async def set_self_use_soc(self, percent: int) -> None:
        """Write the self-consumption reserve SOC."""
        await self._write_setting("self_use_soc", percent, 4134)

    async def set_discharge_min_soc(self, percent: int) -> None:
        """Write the discharge floor SOC."""
        await self._write_setting("discharge_min_soc", percent, 4141)

    async def set_anti_starvation(self, enabled: bool) -> None:
        """Enable or disable battery anti-starvation protection.

        0 disabled, 1 enabled -- straightforward, unlike the hybrid
        client's anti_starvation_protection, which the document states has
        the opposite polarity. See client_hybrid.py's own method.
        """
        await self._write_setting("anti_starvation", 1 if enabled else 0, 4121)

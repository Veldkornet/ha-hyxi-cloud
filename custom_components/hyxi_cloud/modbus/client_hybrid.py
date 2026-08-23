"""A local Modbus client for the HYX-H hybrid inverter family.

Sibling to client.py's HyxiModbusClient (the HALO client), presenting the
same cloud-shaped control surface so sensor.py, binary_sensor.py, number.py,
button.py, switch.py, protection.py and engine.py work unmodified regardless
of which device family a Modbus entry talks to.

Reuses client.py's masking and formatting helpers rather than duplicating
them -- there is exactly one correct way to mask a serial number for this
integration's logs, and a copy would drift.

Provenance: docs/modbus-provenance.md. This map is a materially stronger
source than the HALO one -- the vendor's current document for the exact
hardware family, not a document for a different product whose examples
happened to decode against this one -- but is still unconfirmed against
actual hardware; nothing has been wired up yet.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from hyxi_cloud_api import HyxiApiClient
from modbus_connection.model import Component

from .client import _hex_identifier, _mask
from .registers_hybrid import (
    TELEMETRY_COMPONENTS,
    HybridBackup,
    HybridBattery,
    HybridEnergy,
    HybridFaults,
    HybridGrid,
    HybridIdentity,
    HybridPv,
    HybridSettings,
    HybridStatus,
)

_LOGGER = logging.getLogger(__name__)

# The device type the cloud API reports for this hardware family. Reused
# verbatim so normalize_device_type() resolves it to "hybrid_inverter" on
# both transports and the entity platforms make the same decisions either
# way -- including detect_phase_type's -HT/-HTA suffix check for the
# three-phase sensors this component set assumes.
HYBRID_DEVICE_CODE = "HYBRID_INVERTER"

# HybridSettings.control_mode. Battery power control is the default and the
# only mode this client drives -- inverter AC power control (mode 1) exists
# on the device but has no cloud-client equivalent to map onto.
CONTROL_MODE_BATTERY_POWER = 0


class HyxiHybridModbusClient:
    """Talks to one HYX-H hybrid inverter over Modbus, cloud client shaped."""

    ControlError = HyxiApiClient.ControlError
    compute_derived_metrics = staticmethod(HyxiApiClient.compute_derived_metrics)

    def __init__(self, connection: Any, unit_id: int) -> None:
        """Bind to one unit on an already-constructed connection."""
        self._connection = connection
        self._unit_id = unit_id
        unit = connection.for_unit(unit_id)
        self._unit = unit

        self.identity = HybridIdentity(unit)
        self.status = HybridStatus(unit)
        self.faults = HybridFaults(unit)
        self.grid = HybridGrid(unit)
        self.backup = HybridBackup(unit)
        self.pv = HybridPv(unit)
        self.battery = HybridBattery(unit)
        self.energy = HybridEnergy(unit)
        self.settings = HybridSettings(unit)

        self._components: dict[type[Component], Component] = {
            HybridStatus: self.status,
            HybridFaults: self.faults,
            HybridGrid: self.grid,
            HybridBackup: self.backup,
            HybridPv: self.pv,
            HybridBattery: self.battery,
            HybridEnergy: self.energy,
        }
        self._serial: str | None = None
        self._identity_read = False

    @property
    def serial_number(self) -> str:
        """The device serial, or a stable fallback if identity is unreadable."""
        return self._serial or f"modbus_{self._unit_id}"

    async def async_close(self) -> None:
        """Release the underlying connection.

        Logged unconditionally rather than at each call site -- see
        client.py's HyxiModbusClient.async_close for why.
        """
        _LOGGER.debug("Modbus: closing connection for unit %s", self._unit_id)
        await self._connection.close()

    async def async_read_identity(self) -> None:
        """Read the static identity block once, tolerating its absence."""
        if self._identity_read:
            return
        try:
            await self.identity.async_update()
            self._serial = str(self.identity.serial_number)
            _LOGGER.debug(
                "Modbus identity on unit %s: serial=%s protocol_v=%s "
                "main_dsp=%s main_program=%s battery_sn=%s",
                self._unit_id,
                _mask(self._serial),
                self.identity.protocol_version,
                _hex_identifier(self.identity.main_dsp_version),
                _hex_identifier(self.identity.main_program_version),
                _mask(self.identity.battery_serial_number),
            )
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "Modbus identity block unreadable, falling back to unit id "
                "for this device's key: %s",
                err,
            )
        self._identity_read = True

    async def async_read_all(self) -> dict[str, dict]:
        """Poll the device and return it in the coordinator's data shape."""
        await self.async_read_identity()

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
        derived = HyxiApiClient.compute_derived_metrics(metrics, HYBRID_DEVICE_CODE)
        metrics.update(derived)

        return {
            "sn": self.serial_number,
            "device_name": "HYXI Hybrid Inverter",
            "model": None,  # not exposed on this register map
            "device_type_code": HYBRID_DEVICE_CODE,
            "sw_version": _hex_identifier(self.identity.main_program_version),
            "hw_version": None,
            "metrics": metrics,
        }

    def _build_metrics(self) -> dict:
        """Translate register fields into the cloud client's metric keys.

        gridP is stored in kW here -- an inference, not a documented unit.
        The register itself is undocumented as to unit; treated as Watts on
        the same 0-decimal-place convention the document uses for other
        Watt-scale fields, then converted to kW to match what
        compute_derived_metrics expects for every device type (the HALO
        client needs the same conversion, for a different reason -- see
        _normalize_micro_ess_gridp in hyxi_cloud_api). See
        docs/modbus-provenance.md.
        """
        raw: dict[str, Any] = {
            "invSts": self.status.operation_status,
            "gridSts": self.status.grid_connected,
            "tinv": self.status.inverter_temperature,
            "boostTemper": self.status.boost_temperature,
            "dspTemper": self.status.dsp_temperature,
            # No documented value table for self-test status, unlike the
            # three fields below -- passed through raw rather than guessed.
            "selfTestStatus": self.status.self_test_status,
            "gridMode": self.status.grid_mode,
            "runCommand": self.status.run_command,
            "currentOperatingMode": self.status.current_operating_mode,
            "gridP": (
                None
                if self.grid.active_power is None
                else self.grid.active_power / 1000
            ),
            "gridQ": self.grid.reactive_power,
            "gridAp": self.grid.apparent_power,
            "f": self.grid.frequency,
            "gridF": self.grid.frequency,
            "ph1v": self.grid.voltage_a,
            "ph2v": self.grid.voltage_b,
            "ph3v": self.grid.voltage_c,
            "ph1i": self.grid.current_a,
            "ph2i": self.grid.current_b,
            "ph3i": self.grid.current_c,
            "ph1p": self.grid.phase_a_power,
            "ph2p": self.grid.phase_b_power,
            "ph3p": self.grid.phase_c_power,
            "offGridF": self.backup.frequency,
            "offGridP": self.backup.active_power,
            "ph1Loadv": self.backup.voltage_a,
            "ph2Loadv": self.backup.voltage_b,
            "ph3Loadv": self.backup.voltage_c,
            "ph1Loadp": self.backup.phase_a_power,
            "ph2Loadp": self.backup.phase_b_power,
            "ph3Loadp": self.backup.phase_c_power,
            "pv1v": self.pv.pv1_voltage,
            "pv1i": self.pv.pv1_current,
            "pv1p": self.pv.pv1_power,
            "pv2v": self.pv.pv2_voltage,
            "pv2i": self.pv.pv2_current,
            "pv2p": self.pv.pv2_power,
            "vbus": self.pv.bus_voltage,
            # Battery. batSn routes the battery-related keys above and below
            # onto a separate "Battery {sn}" device, matching the HALO
            # client -- omitting it (as this client previously did) leaves
            # them attached to the inverter device instead.
            "batSn": self.identity.battery_serial_number,
            "batSoc": self.battery.soc,
            "batSoh": self.battery.soh,
            "batTmp": self.battery.temperature,
            "batV": self.battery.voltage,
            "batI": self.battery.current,
            "batVch": self.battery.max_cell_voltage,
            "batVcl": self.battery.min_cell_voltage,
            "batTch": self.battery.max_cell_temperature,
            "batTcl": self.battery.min_cell_temperature,
            "batP": self.battery.power,
            "pbat": self.battery.power,
            "batOperatingStatus": self.battery.operating_status,
            "llcBusVoltage": self.battery.llc_bus_voltage,
            "batDischargeV": self.battery.discharge_voltage,
            "batDischargeI": self.battery.discharge_current,
            "batDischargeP": self.battery.discharge_power,
            "batChargeV": self.battery.charge_voltage,
            "batChargeI": self.battery.charge_current,
            "batChargeP": self.battery.charge_power,
            # Unit not stated in the document -- passed through raw.
            "batNominalCapacity": self.battery.nominal_capacity,
            "totalE": self.energy.output_a,
            "totalEb": self.energy.output_b,
            "totalEc": self.energy.output_c,
            "totalEchg": self.energy.charge_total,
            "totalEdchg": self.energy.discharge_total,
            "batCharge": self.energy.charge_total,
            "batDisCharge": self.energy.discharge_total,
        }
        return {key: value for key, value in raw.items() if value is not None}

    # --- Control surface, matching HyxiApiClient method for method ---------

    async def _prepare_scheduling(self) -> None:
        """Enable Modbus scheduling in the mode this client understands.

        Written on every command rather than assumed, the same reasoning as
        the HALO client's VPP enable: recovers automatically if something
        else disabled scheduling or switched control mode.
        """
        try:
            await self.settings.write("scheduling_enabled", 1)
            await self.settings.write("control_mode", CONTROL_MODE_BATTERY_POWER)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "Modbus scheduling enable failed on unit %s: %s", self._unit_id, err
            )
            raise self.ControlError(f"Modbus write failed: {err}") from err

    async def _write_battery_power(self, watts: int) -> dict:
        """Set the battery power setpoint. Positive discharges, negative charges."""
        await self._prepare_scheduling()
        try:
            await self.settings.write("battery_power", watts)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "Modbus battery power write failed on unit %s (watts=%s): %s",
                self._unit_id,
                watts,
                err,
            )
            raise self.ControlError(f"Modbus write failed: {err}") from err
        _LOGGER.debug(
            "Modbus battery power write ok on unit %s: 3015=%s", self._unit_id, watts
        )
        return {"code": "0", "msg": "ok"}

    async def set_mode_idle(self, device_sn: str) -> dict:
        """Hold the battery: neither charge nor discharge."""
        _LOGGER.debug("Modbus: set idle on %s", _mask(device_sn))
        return await self._write_battery_power(0)

    async def set_mode_charge(self, device_sn: str, watts: int) -> dict:
        """Charge at a fixed power."""
        _LOGGER.debug("Modbus: charge %sW on %s", watts, _mask(device_sn))
        return await self._write_battery_power(-abs(int(watts)))

    async def set_mode_discharge(self, device_sn: str, watts: int) -> dict:
        """Discharge at a fixed power."""
        _LOGGER.debug("Modbus: discharge %sW on %s", watts, _mask(device_sn))
        return await self._write_battery_power(abs(int(watts)))

    async def set_mode_self_consume(self, device_sn: str) -> dict:
        """Return the device to its own self-consumption logic.

        Unlike the HALO client, this is not "hold at zero power" -- it turns
        Modbus scheduling off entirely (register 3000), handing control back
        to the inverter's native self-use behaviour rather than pinning it
        at an idle setpoint under external control.
        """
        _LOGGER.debug("Modbus: self-consume on %s", _mask(device_sn))
        try:
            await self.settings.write("scheduling_enabled", 0)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "Modbus self-consume write failed on unit %s: %s",
                self._unit_id,
                err,
            )
            raise self.ControlError(f"Modbus write failed: {err}") from err
        _LOGGER.debug("Modbus self-consume write ok on unit %s: 3000=0", self._unit_id)
        return {"code": "0", "msg": "ok"}

    async def set_peak_shaving(self, device_sn: str, action: str) -> dict:
        """Limit or release export via the real feed-in registers."""
        _LOGGER.debug("Modbus: peak shaving %s on %s", action, _mask(device_sn))
        limit_enabled = action in ("on", "enable", "start")
        try:
            await self.settings.write("feed_in_enable", 1 if limit_enabled else 0)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "Modbus peak shaving write failed on unit %s (action=%s): %s",
                self._unit_id,
                action,
                err,
            )
            raise self.ControlError(f"Modbus write failed: {err}") from err
        _LOGGER.debug(
            "Modbus peak shaving write ok on unit %s: 1099=%s",
            self._unit_id,
            1 if limit_enabled else 0,
        )
        return {"code": "0", "msg": "ok"}

    async def _write_setting(self, field: str, value: Any, register: int) -> None:
        """Write one HybridSettings field, wrapping failures uniformly.

        A shared helper rather than repeating the try/except in every
        setting method below -- unlike set_peak_shaving and _write_vpp,
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

    async def set_feed_in_power(self, watts: int) -> None:
        """Write the export power limit. Unscaled -- the register is W."""
        await self._write_setting("feed_in_power", watts, 1100)

    async def set_max_charge_current(self, amps: float) -> None:
        """Write the maximum charge current. 0 means no limit."""
        await self._write_setting("max_charge_current", amps, 3112)

    async def set_max_discharge_current(self, amps: float) -> None:
        """Write the maximum discharge current. 0 means no limit."""
        await self._write_setting("max_discharge_current", amps, 3113)

    async def power_on(self, device_sn: str) -> None:
        """Send the power-on command (register 3002 = 1)."""
        _LOGGER.debug("Modbus: power on %s", _mask(device_sn))
        await self._write_setting("power_command", 1, 3002)

    async def power_off(self, device_sn: str) -> None:
        """Send the power-off command (register 3002 = 2)."""
        _LOGGER.debug("Modbus: power off %s", _mask(device_sn))
        await self._write_setting("power_command", 2, 3002)

    async def restart(self, device_sn: str) -> None:
        """Send the restart command (register 3002 = 3)."""
        _LOGGER.debug("Modbus: restart %s", _mask(device_sn))
        await self._write_setting("power_command", 3, 3002)

    async def set_self_use_soc(self, percent: int) -> None:
        """Write the self-consumption reserve SOC."""
        await self._write_setting("self_use_soc", percent, 1102)

    async def set_backup_soc(self, percent: int) -> None:
        """Write the off-grid/backup reserve SOC."""
        await self._write_setting("backup_soc", percent, 1103)

    async def set_forced_charge_soc(self, percent: int) -> None:
        """Write the anti-starvation forced-charge SOC."""
        await self._write_setting("forced_charge_soc", percent, 1104)

    async def set_feed_in_soc(self, percent: int) -> None:
        """Write the SOC above which export is permitted."""
        await self._write_setting("feed_in_soc", percent, 1105)

    async def set_off_grid_soc(self, percent: int) -> None:
        """Write the SOC threshold for switching to off-grid mode."""
        await self._write_setting("off_grid_soc", percent, 1106)

    async def set_anti_starvation_protection(self, enabled: bool) -> None:
        """Enable or disable battery anti-starvation protection.

        0 open, 1 close per the document -- the opposite polarity from the
        HALO client's anti_starvation (there, 0 disables and 1 enables).
        The entity presents "enabled" uniformly regardless of family; the
        polarity difference is hidden here, the same way set_peak_shaving
        already hides an inverted feed_in_enable sense between the two
        clients.
        """
        await self._write_setting(
            "anti_starvation_protection", 0 if enabled else 1, 1101
        )

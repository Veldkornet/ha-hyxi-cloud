"""HYX-H hybrid inverter register model.

Transcribed from HYXIPower's *RS485_MODBUS RTU Hybrid Inverter Protocol*,
V4.1 (2025/6/13), supplied directly to this project. Covers
HYX-H(5~12)K-HT, HYX-H(15~25)K-HT, HYX-H(6~15)K-HTA and HYX-H(6~15)K-HTAC --
including the H10K-HT this transport is being brought up against.

This is a materially stronger source than the HALO map in registers.py: it
is the vendor's own current document for the exact hardware family in use,
not a document for a different product family whose examples happened to
decode against this one. Provenance and what remains unverified even so:
docs/modbus-provenance.md.

Two rules carry over unchanged from the HALO map:

Input and holding are separate address spaces. Register 1007 is the
inverter's serial number (H64, read with 0x04) and, completely
independently, the RTC's absolute time (U32, read/write with 0x03/0x06/0x10)
-- confirmed by this document's own worked examples in section 5.4, not
inferred.

Multi-register values are low word first -- this document's wording differs
slightly from the HALO one ("the high word is behind and the low word is in
the front") but describes the identical layout, and its own example proves
it the same way: 1715081225 (0x663A186A) as register 1007 arrives on the
wire as 18 6A 66 3A.

One new rule specific to this map: HYX-H exposes function code 0x06 (write
single register) alongside 0x03/0x04/0x10, and requires more than 500ms
between frames -- the HALO document asked for 200ms. Both are handled at the
connection level (message_spacing), not here.
"""

from __future__ import annotations

from modbus_connection.model import (
    Component,
    gauge,
    int32,
    integer,
    raw_register,
    string,
    uint32,
    uint64,
)

MAX_SPAN = 100
LOW_WORD_FIRST = "little"


class HybridIdentity(Component):
    """Versions and serial number. Static; read once at setup."""

    register_space = "input"
    max_span = MAX_SPAN

    protocol_version = integer(0, signed=False)
    """Communication protocol version."""
    main_dsp_version = uint32(1, word_order=LOW_WORD_FIRST)
    """Main DSP program version, low/high H16 pair."""
    main_program_version = uint32(1001, word_order=LOW_WORD_FIRST)
    """Main program version. Confirmed H32 at a different address from the
    DSP version above -- the document lists both."""
    serial_number = uint64(1007, word_order=LOW_WORD_FIRST)
    """Inverter serial number. Input-space only -- see the module docstring
    for the collision with the holding-space clock at this same address."""
    battery_serial_number = string(1015, 10)
    """Battery pack serial number, S20 (20 ASCII chars over 10 registers)."""


class HybridStatus(Component):
    """Operating state and internal temperatures."""

    register_space = "input"
    max_span = MAX_SPAN

    inverter_temperature = gauge(19, 0.1, unit="°C")
    """Inverter side temperature."""
    boost_temperature = gauge(20, 0.1, unit="°C")
    """Boost converter temperature."""
    dsp_temperature = gauge(21, 0.1, unit="°C")
    """DSP temperature."""
    # Confirmed enumeration, but left as a plain integer rather than IntEnum:
    # this covers four inverter models (HT/HTA/HTAC across several power
    # ratings) and has not yet been checked against any of them. A firmware
    # difference between models should show up as an unexpected number, not
    # a crash.
    operation_status = integer(22, signed=False)
    """0 init, 1 standby, 2 DC-side startup, 3 self-test startup,
    4 self-test wait, 5 inverter operation, 6 steady state operation."""
    self_test_status = integer(23, signed=False)
    """Inverter system self-test status."""
    grid_mode = integer(25, signed=False)
    """1 grid-connected, 0 off-grid."""
    run_command = integer(26, signed=False)
    """1 start, 0 stop."""
    grid_connected = integer(53, signed=False)
    """System grid connection success flag. 1 successful, 0 failed."""
    current_operating_mode = integer(1265, signed=False)
    """1 self-use, 2 backup(green), 3 backup(grid), 4 feed-in, 5 off-grid,
    6 battery SOC calibration, 7 battery forced charging. A third, distinct
    enumeration from both the HALO document's work_mode and the cloud
    client's workMode -- see docs/modbus-provenance.md, rule 1. This one is
    at least the vendor's current document for this exact hardware."""


class HybridFaults(Component):
    """Fault and alarm bitfields, raw. See registers.py's HaloFaults for why
    these are not decoded into named bits yet."""

    register_space = "input"
    max_span = MAX_SPAN

    software_fault_1 = raw_register(38)
    software_fault_2 = raw_register(39)
    software_fault_3 = raw_register(40)
    software_fault_4 = raw_register(41)
    software_fault_5 = raw_register(42)
    software_fault_6 = raw_register(43)
    software_alarm_1 = raw_register(44)
    software_alarm_2 = raw_register(45)
    dsp_comm_fault = raw_register(1041)
    device_comm_fault = raw_register(1042)
    device_alarm = raw_register(1043)


class HybridGrid(Component):
    """Grid-side measurements. Three phase (A/B/C -- HT/HTA models)."""

    register_space = "input"
    max_span = MAX_SPAN

    voltage_a = gauge(300, 0.01, signed=False, unit="V")
    voltage_b = gauge(301, 0.01, signed=False, unit="V")
    voltage_c = gauge(302, 0.01, signed=False, unit="V")
    frequency = gauge(303, 0.01, unit="Hz")
    current_a = gauge(311, 0.01, unit="A")
    current_b = gauge(312, 0.01, unit="A")
    current_c = gauge(313, 0.01, unit="A")
    # Unverified unit: the document leaves these blank rather than stating
    # "W" or "kW". 0 decimal places and a magnitude consistent with Watts
    # (not whole-kW, which would be too coarse) is the same convention the
    # document uses for other clearly-Watt fields (e.g. inverter power at
    # 333), so these are treated as Watts. See docs/modbus-provenance.md.
    active_power = integer(316, unit="W")
    reactive_power = integer(317, unit="var")
    apparent_power = integer(318, unit="VA")
    phase_a_power = integer(370, unit="W")
    phase_b_power = integer(371, unit="W")
    phase_c_power = integer(372, unit="W")


class HybridBackup(Component):
    """Off-grid (EPS) side measurements, three phase."""

    register_space = "input"
    max_span = MAX_SPAN

    voltage_a = gauge(500, 0.01, signed=False, unit="V")
    voltage_b = gauge(501, 0.01, signed=False, unit="V")
    voltage_c = gauge(502, 0.01, signed=False, unit="V")
    frequency = gauge(503, 0.01, unit="Hz")
    active_power = integer(507, signed=False, unit="W")
    phase_a_power = integer(520, signed=False, unit="W")
    phase_b_power = integer(521, signed=False, unit="W")
    phase_c_power = integer(522, signed=False, unit="W")


class HybridPv(Component):
    """PV string measurements. Two strings on this hardware family."""

    register_space = "input"
    max_span = MAX_SPAN

    bus_voltage = gauge(600, 0.1, signed=False, unit="V")
    pv1_voltage = gauge(604, 0.1, signed=False, unit="V")
    pv1_current = gauge(605, 0.1, signed=False, unit="A")
    pv1_power = integer(606, signed=False, unit="W")
    pv2_voltage = gauge(610, 0.1, signed=False, unit="V")
    pv2_current = gauge(611, 0.1, signed=False, unit="A")
    pv2_power = integer(612, signed=False, unit="W")


class HybridBattery(Component):
    """BMS state, cell extremes and instantaneous power."""

    register_space = "input"
    max_span = MAX_SPAN

    llc_bus_voltage = gauge(802, 0.1, signed=False, unit="V")
    discharge_voltage = gauge(804, 0.1, signed=False, unit="V")
    discharge_current = gauge(805, 0.1, signed=False, unit="A")
    discharge_power = integer(806, signed=False, unit="W")
    charge_voltage = gauge(819, 0.1, signed=False, unit="V")
    charge_current = gauge(820, 0.1, signed=False, unit="A")
    charge_power = integer(821, signed=False, unit="W")
    # Confirmed enumeration for this document; not yet checked on hardware.
    operating_status = integer(1051, signed=False)
    """0 sleep, 1 charge, 2 discharge, 3 idle."""
    voltage = gauge(1052, 0.01, signed=False, unit="V")
    current = gauge(1053, 0.1, unit="A")
    soc = integer(1054, signed=False, unit="%")
    soh = integer(1055, signed=False, unit="%")
    temperature = integer(1056, unit="°C")
    max_cell_voltage = gauge(1061, 0.001, signed=False, unit="V")
    min_cell_voltage = gauge(1062, 0.001, signed=False, unit="V")
    max_cell_temperature = integer(1063, signed=False, unit="°C")
    min_cell_temperature = integer(1064, signed=False, unit="°C")
    power = integer(1065, unit="W")
    """Battery real-time power. Sign convention undocumented -- neither
    'positive' nor 'negative' is stated for charge vs discharge here, unlike
    register 3015 in HybridSettings, which explicitly is. Not assumed equal
    without checking; see docs/modbus-provenance.md."""
    nominal_capacity = integer(1097, signed=False)
    """Unit not stated in the document."""


class HybridEnergy(Component):
    """Lifetime energy counters. Daily counters exist too (1101-1127,
    U16 1dp) but are omitted here -- the accumulated U32 counters below
    already cover what the cloud metric vocabulary uses, and every extra
    field widens the poll."""

    register_space = "input"
    max_span = MAX_SPAN

    output_a = uint32(1128, scale=0.1, word_order=LOW_WORD_FIRST, unit="kWh")
    output_b = uint32(1130, scale=0.1, word_order=LOW_WORD_FIRST, unit="kWh")
    output_c = uint32(1132, scale=0.1, word_order=LOW_WORD_FIRST, unit="kWh")
    charge_total = uint32(1146, scale=0.1, word_order=LOW_WORD_FIRST, unit="kWh")
    discharge_total = uint32(1148, scale=0.1, word_order=LOW_WORD_FIRST, unit="kWh")


class HybridSettings(Component):
    """Writable settings, including the Modbus scheduling control block.

    Holding registers, read with 0x03, written with 0x06 (single) or 0x10
    (multiple). Registers 3000-3015 are what make local control possible at
    all -- and on this hardware family it is dramatically simpler than the
    HALO's VPP block: register 3000 hands the inverter over to Modbus
    control, and 3015 is a single signed watts value (positive discharge,
    negative charge) when the default control mode (3004=0) is in effect.

    Registers 1007 and 1009 (absolute time, time zone), 3120 (baud rate) and
    3121 (Modbus address) are deliberately absent, matching HaloSettings'
    exclusion of the equivalent registers: writing any of them can take the
    device off the bus or desync its clock, and nothing here needs to.
    """

    register_space = "holding"
    max_span = MAX_SPAN

    scheduling_enabled = integer(3000, signed=False, writable=True)
    """0 disabled, 1 enabled. Master switch for local control."""
    power_command = integer(3002, signed=False, writable=True)
    """1 power on, 2 power off, 3 restart."""
    control_mode = integer(3004, signed=False, writable=True)
    """0 battery power control (default), 1 inverter AC power control."""
    battery_power = int32(3015, word_order=LOW_WORD_FIRST, writable=True, unit="W")
    """Only takes effect while control_mode is 0. Positive: battery
    discharge. Negative: battery charge. Confirmed sign convention -- the
    document states it explicitly, unlike register 1065's read-only power
    value above."""
    max_charge_current = gauge(3112, 0.1, signed=False, writable=True, unit="A")
    """0 means no limit."""
    max_discharge_current = gauge(3113, 0.1, signed=False, writable=True, unit="A")
    """0 means no limit."""
    feed_in_enable = integer(1099, signed=False, writable=True)
    """0 disable export control, 1 enable export control."""
    feed_in_power = integer(1100, signed=False, writable=True, unit="W")
    anti_starvation_protection = integer(1101, signed=False, writable=True)
    """0 open, 1 close. Inverted sense from the HALO document's equivalent
    field (there, 0 disables and 1 enables) -- these are two different
    devices and the polarity is not assumed to match."""
    self_use_soc = integer(1102, signed=False, writable=True, unit="%")
    backup_soc = integer(1103, signed=False, writable=True, unit="%")
    forced_charge_soc = integer(1104, signed=False, writable=True, unit="%")
    feed_in_soc = integer(1105, signed=False, writable=True, unit="%")
    off_grid_soc = integer(1106, signed=False, writable=True, unit="%")


#: Components read on every poll, in the order they are read.
TELEMETRY_COMPONENTS = (
    HybridStatus,
    HybridFaults,
    HybridGrid,
    HybridBackup,
    HybridPv,
    HybridBattery,
    HybridEnergy,
)

"""HALO register model, from HYXIPower's Micro Storage RS485 document V1.0.

Transcribed from the specification shared on issue #662 with permission to
publish. Values are unverified against hardware -- no HALO has been on a bus
for anything but a live control failure (see HaloSettings) -- so everything
here is the vendor's claim, not an observation, except where individually
noted.

Two rules govern the whole map and are easy to get wrong:

Input and holding are *separate address spaces* that reuse the same numbers.
Register 4152 is grid active power read with 0x04 and VPP minimum SOC read
with 0x03. Declaring the space on each component keeps them apart
structurally rather than by convention.

Every multi-register value is *low word first*. The document says so ("high
16 bits after, low 16 bits first") and its own worked example proves it:
1715081225 = 0x663A186A arrives as 18 6A 66 3A.

Fields are scaled to the unit the document names, not to Home Assistant's.
Converting to the metric vocabulary the cloud path already uses happens in
client.py, so this module stays a faithful reading of the specification.

Deliberately kept free of Home Assistant imports: this is a candidate for
extraction into hyxi_cloud_api once a register map is hardware-confirmed.

Provenance and the list of what is still unverified: docs/modbus-provenance.md.
Read it before changing any address, scale or enumeration here.
"""

from __future__ import annotations

from modbus_connection import WordOrder
from modbus_connection.model import (
    Component,
    gauge,
    int32,
    integer,
    raw_register,
    uint32,
    uint64,
)

# The document caps a request at 100 registers and asks for more than 200ms
# between frames, so components read in blocks no wider than that.
MAX_SPAN = 100

# Word order for every 32- and 64-bit value in the HYXI maps. Annotated with
# modbus_connection's own Literal alias -- a bare `str` here doesn't satisfy
# the word_order= parameter's Literal["big", "little"] type, since mypy
# won't narrow a module-level assignment to a Literal on its own.
LOW_WORD_FIRST: WordOrder = "little"


class HaloIdentity(Component):
    """Model, serial and firmware. Static, so read once at setup."""

    register_space = "input"
    max_span = MAX_SPAN

    model_low = uint64(4002, word_order=LOW_WORD_FIRST)
    """Model name, low half. Displayed as hex per the document."""
    model_high = uint64(4006, word_order=LOW_WORD_FIRST)
    """Model name, high half."""
    serial_number = uint64(4018, word_order=LOW_WORD_FIRST)
    """Device serial number."""
    arm_version = uint32(4026, word_order=LOW_WORD_FIRST)
    """ARM software version."""
    dsp_version = uint32(4028, word_order=LOW_WORD_FIRST)
    """Main DSP software version."""
    hardware_version = uint32(4034, word_order=LOW_WORD_FIRST)
    """ARM hardware version."""
    rated_power = int32(4046, word_order=LOW_WORD_FIRST, unit="W")
    """Rated power."""
    rated_frequency = gauge(4048, 0.01, unit="Hz")
    """Rated frequency."""
    rated_voltage = gauge(4049, 0.01, signed=False, unit="V")
    """Rated voltage."""
    battery_serial_number = uint64(4962, word_order=LOW_WORD_FIRST)
    """BMS serial number. Read here rather than with the battery telemetry:
    it sits 16 registers below the rest of the BMS block, so including it
    there would widen every poll and make SOC depend on a static field."""


class HaloStatus(Component):
    """Operating state and internal temperatures."""

    register_space = "input"
    max_span = MAX_SPAN

    # Left as plain integers rather than enums on purpose. The document's
    # work-mode table contradicts the values the cloud client infers, and
    # neither has been confirmed against a device -- an enum here would bake
    # one guess in and raise on anything else.
    #
    # DO NOT "fix" work_mode to agree with hyxi_cloud_api.VPP_ACTIVE_MODES.
    # The document says 13 idle / 14 charge / 15 discharge / 16 self-use;
    # VPP_ACTIVE_MODES says 13 charge / 14 discharge / 16 standby. Per
    # hyxi_cloud_api's own comment, that's confirmed via live observation of
    # the *cloud* workMode field plus community register documentation --
    # but it has never been cross-checked against a HALO unit's actual
    # Modbus registers, so it's still independent evidence about the same
    # field, not corroboration. Editing either to match the other destroys
    # the only way to tell which is right over Modbus.
    # See docs/modbus-provenance.md, rule 1.
    switch_status = integer(4100, signed=False)
    """Power on/off state. 0 off, 1 on."""
    work_state = integer(4101, signed=False)
    """1 standby, 3 self-test, 6 running, 7 stopped."""
    work_mode = integer(4102, signed=False)
    """Grid-tie work mode. See the module docstring's caveat."""
    grid_state = integer(4103, signed=False)
    """0 grid abnormal, 1 grid normal."""
    insulation_resistance = integer(4104, signed=False)
    """Insulation resistance detection value."""
    leakage_current = integer(4105, signed=False)
    """Leakage current detection value."""
    bus_voltage = gauge(4106, 0.1, signed=False, unit="V")
    """DC bus voltage."""
    ambient_temperature = gauge(4109, 0.1, unit="°C")
    """Ambient temperature."""
    ac_temperature = gauge(4110, 0.1, unit="°C")
    """AC-side temperature."""
    dc_temperature = gauge(4111, 0.1, unit="°C")
    """DC-side temperature."""
    meter_online = integer(4123, signed=False)
    """Meter communication state. 0 offline, 1 online."""


class HaloGrid(Component):
    """Grid-side measurements. Single phase, so phase A only."""

    register_space = "input"
    max_span = MAX_SPAN

    frequency = gauge(4151, 0.01, unit="Hz")
    """Grid frequency."""
    active_power = int32(4152, scale=0.001, word_order=LOW_WORD_FIRST, unit="kW")
    """Grid active power. Sign convention unconfirmed."""
    reactive_power = int32(4154, scale=0.001, word_order=LOW_WORD_FIRST, unit="kW")
    """Grid reactive power."""
    apparent_power = int32(4156, scale=0.001, word_order=LOW_WORD_FIRST, unit="kW")
    """Grid apparent power."""
    power_factor = gauge(4158, 0.01)
    """Grid power factor."""
    voltage = gauge(4161, 0.01, signed=False, unit="V")
    """Phase A voltage."""
    current = gauge(4162, 0.1, unit="A")
    """Phase A current."""
    phase_power = int32(4163, scale=0.001, word_order=LOW_WORD_FIRST, unit="kW")
    """Phase A active power."""


class HaloBackup(Component):
    """Off-grid (EPS) side measurements."""

    register_space = "input"
    max_span = MAX_SPAN

    frequency = gauge(4200, 0.01, unit="Hz")
    """Off-grid frequency."""
    active_power = int32(4201, scale=0.001, word_order=LOW_WORD_FIRST, unit="kW")
    """Off-grid active power."""
    voltage = gauge(4210, 0.01, signed=False, unit="V")
    """Off-grid phase A voltage."""
    current = gauge(4211, 0.1, unit="A")
    """Off-grid phase A current."""
    phase_power = int32(4212, scale=0.001, word_order=LOW_WORD_FIRST, unit="kW")
    """Off-grid phase A active power."""


class HaloEnergy(Component):
    """Lifetime and daily energy counters."""

    register_space = "input"
    max_span = MAX_SPAN

    output_today = uint32(4500, scale=0.001, word_order=LOW_WORD_FIRST, unit="kWh")
    """Inverter output energy today."""
    output_total = uint32(4502, scale=0.001, word_order=LOW_WORD_FIRST, unit="kWh")
    """Inverter output energy, lifetime."""
    battery_charged_total = uint32(
        4506, scale=0.001, word_order=LOW_WORD_FIRST, unit="kWh"
    )
    """Battery charged, lifetime."""
    battery_discharged_total = uint32(
        4510, scale=0.001, word_order=LOW_WORD_FIRST, unit="kWh"
    )
    """Battery discharged, lifetime."""
    input_today = uint32(4512, scale=0.001, word_order=LOW_WORD_FIRST, unit="kWh")
    """Inverter input energy today."""
    input_total = uint32(4514, scale=0.001, word_order=LOW_WORD_FIRST, unit="kWh")
    """Inverter input energy, lifetime."""


class HaloFaults(Component):
    """Fault and alarm bitfields.

    Read as raw words rather than IntFlag: the document's own alarm tables
    disagree with its register table about where the BMS words sit (5000-5002
    against 5001-5003), and one address cell is visibly corrupt. Decoding bits
    into names is deferred until hardware settles that.
    """

    register_space = "input"
    max_span = MAX_SPAN

    hardware_fault = raw_register(4850)
    """System hardware fault word."""
    software_fault_1 = raw_register(4851)
    """DSP software fault word 1."""
    software_fault_2 = raw_register(4852)
    """DSP software fault word 2."""
    software_fault_3 = raw_register(4853)
    """DSP software fault word 3."""
    software_alarm_1 = raw_register(4857)
    """System software alarm word 1."""


class HaloBattery(Component):
    """BMS state, cell extremes and power limits."""

    register_space = "input"
    max_span = MAX_SPAN

    pack_count = integer(4978, signed=False)
    """Number of battery packs."""
    bms_state = integer(4979, signed=False)
    """BMS work state. 4 high-voltage charge, 5 high-voltage discharge."""
    soc = gauge(4980, 0.1, signed=False, unit="%")
    """State of charge."""
    soh = gauge(4981, 0.1, signed=False, unit="%")
    """State of health."""
    temperature = gauge(4982, 0.1, unit="°C")
    """Battery temperature."""
    power = int32(4985, scale=0.001, word_order=LOW_WORD_FIRST, unit="kW")
    """Battery power. Sign convention unconfirmed."""
    cell_voltage_max = gauge(4989, 0.001, signed=False, unit="V")
    """Highest cell voltage."""
    cell_voltage_min = gauge(4990, 0.001, signed=False, unit="V")
    """Lowest cell voltage."""
    cell_temperature_max = gauge(4995, 0.1, unit="°C")
    """Highest cell temperature."""
    cell_temperature_min = gauge(4996, 0.1, unit="°C")
    """Lowest cell temperature."""
    alarm_1 = raw_register(5000)
    """BMS alarm word 1."""
    alarm_2 = raw_register(5001)
    """BMS alarm word 2."""
    alarm_3 = raw_register(5002)
    """BMS alarm word 3."""
    capacity_ah = integer(5020, signed=False, unit="Ah")
    """Battery capacity. Amp-hours here, unlike the cloud's kWh."""
    max_discharge_power = uint32(
        5021, scale=0.001, word_order=LOW_WORD_FIRST, unit="kW"
    )
    """System maximum discharge capability."""
    max_charge_power = uint32(5023, scale=0.001, word_order=LOW_WORD_FIRST, unit="kW")
    """System maximum charge capability."""


class HaloSettings(Component):
    """Writable settings, including the VPP dispatch block.

    Holding registers, read with 0x03 and written with 0x10. The VPP block at
    4146-4150 is what makes local control possible at all: HYXI's cloud API
    refuses Micro ESS control for third-party applications, and there is no
    permission check on this path.

    Every writable single-register field below passes force_fc16=True: this
    document's function-code table lists only 0x03/0x04/0x10, unlike the
    hybrid one, which explicitly adds 0x06 (write single register) -- see
    docs/modbus-provenance.md. Without it, modbus_connection defaults a
    one-word field to FC 0x06, which this firmware does not implement; the
    device's non-echo response then fails tmodbus's "response must match
    request" check for that function, surfacing as "Expected response to
    match request" -- this specific function-code behavior, not the wider
    register map, is what a real device's error confirmed, when a HALO
    owner hit it clicking the discharge control button. The two-register
    fields (int32/uint32) below already write via FC 0x10 regardless, since
    modbus_connection picks it for any field wider than one register.

    Registers 4000-4005 (clock, timezone, RS485 address and baud rate) are
    deliberately absent. Writing them can take the device off the bus, and
    nothing in this integration has a reason to.
    """

    register_space = "holding"
    max_span = MAX_SPAN

    dispatch_mode = integer(4048, signed=False, writable=True, force_fc16=True)
    """0 absolute power dispatch, 1 percentage dispatch."""
    active_power_setpoint = int32(
        4049, scale=0.001, word_order=LOW_WORD_FIRST, writable=True, unit="kW"
    )
    """Active power setpoint."""
    anti_starvation = integer(4121, signed=False, writable=True, force_fc16=True)
    """Battery anti-starvation protection. 0 disabled, 1 enabled."""
    force_charge_start_soc = integer(
        4132, signed=False, writable=True, unit="%", force_fc16=True
    )
    """Anti-starvation / forced charge start SOC."""
    off_grid_min_soc = integer(
        4133, signed=False, writable=True, unit="%", force_fc16=True
    )
    """Minimum SOC while off grid."""
    self_use_soc = integer(4134, signed=False, writable=True, unit="%", force_fc16=True)
    """Self-consumption reserve SOC."""
    force_charge_stop_soc = integer(
        4140, signed=False, writable=True, unit="%", force_fc16=True
    )
    """Anti-starvation / forced charge stop SOC."""
    discharge_min_soc = integer(
        4141, signed=False, writable=True, unit="%", force_fc16=True
    )
    """Discharge floor SOC."""
    vpp_enable = integer(4146, signed=False, writable=True, force_fc16=True)
    """VPP dispatch mode 2. 0 disabled, 1 enabled."""
    vpp_mode = integer(4147, signed=False, writable=True, force_fc16=True)
    """0 idle, 1 charge, 2 discharge, 3 self-consumption."""
    vpp_charge_power = uint32(4148, word_order=LOW_WORD_FIRST, writable=True, unit="W")
    """VPP charge power."""
    vpp_discharge_power = uint32(
        4150, word_order=LOW_WORD_FIRST, writable=True, unit="W"
    )
    """VPP discharge power."""
    vpp_min_soc = integer(4152, signed=False, writable=True, unit="%", force_fc16=True)
    """Minimum SOC under VPP dispatch. Not grid power -- different space."""
    feed_in_enable = integer(4162, signed=False, writable=True, force_fc16=True)
    """Export to grid. 0 off, 1 on."""
    feed_in_power_limit = int32(
        4163, scale=0.001, word_order=LOW_WORD_FIRST, writable=True, unit="kW"
    )
    """Export power limit."""


#: Components read on every poll, in the order they are read.
TELEMETRY_COMPONENTS = (
    HaloStatus,
    HaloGrid,
    HaloBackup,
    HaloEnergy,
    HaloFaults,
    HaloBattery,
)

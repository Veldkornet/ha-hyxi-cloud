#!/usr/bin/env python3
"""A simulated HYXI inverter on Modbus TCP.

Development tooling, not part of the integration. It exists so the local
transport can be built and tested without hardware on the desk, and so a
capture from a real inverter can be replayed deterministically in CI.

Two ways to seed it:

    # a built-in register map, hand-transcribed from the vendor document
    ./tools/fake_hyxi.py --profile halo --port 5020
    ./tools/fake_hyxi.py --profile hybrid --port 5020

    # any snapshot taken from real hardware by tools/modbus_probe.py
    ./tools/fake_hyxi.py --snapshot idle.json --port 5020

The second form is the useful one long term: sweep a real inverter once and
that capture becomes a replayable device, so tests assert against register
values the hardware actually produced rather than values invented in a test.

It simulates the transport, not the physics. Writes land in the register
they address and stay there; nothing recomputes SOC or power in response.
Decode paths and plumbing can be trusted against this. Control semantics
cannot -- those still need the real device.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import NamedTuple

try:
    from pymodbus.server import StartAsyncTcpServer
    from pymodbus.simulator import DataType, SimData, SimDevice
except ImportError:  # pragma: no cover - dev tool, not shipped
    sys.exit(
        "pymodbus is not installed.\n"
        "  uv sync --extra modbus\n"
        "(the probe uses tmodbus as its client; this fake device is a server, "
        "which tmodbus does not provide)"
    )


class Reg(NamedTuple):
    """One register from a HYXI protocol table.

    `kind` and `value` mirror the document's own columns, so a row here can
    be read straight across from the vendor table. Scaling is left applied:
    a value the table marks as 1 decimal place is stored as the raw integer
    the device would return, i.e. 78.0% is 780.
    """

    address: int
    kind: str
    value: int


def encode(kind: str, value: int) -> list[int]:
    """Encode a value into register words, low word first.

    HYXI's documents put the low 16 bits in the first register for every
    32-bit type, which is the opposite of the Modbus convention most tools
    assume. Getting this wrong here would make the fake device disagree with
    real hardware in exactly the way that is hardest to notice.
    """
    if kind in ("u16", "i16"):
        return [value & 0xFFFF]
    words = 4 if kind in ("u64", "i64") else 2
    raw = value & ((1 << (16 * words)) - 1)
    return [(raw >> (16 * i)) & 0xFFFF for i in range(words)]


# --- HALO / HYX-MS3000AC, from the Micro Storage RS485 document V1.0 -------
# Values are a plausible steady state: 78% charged, importing 811 W, running
# in self-consumption. Read with function code 0x04.
HALO_INPUT: tuple[Reg, ...] = (
    # Identity. The document renders H32/H64 values as hexadecimal, so the
    # serial below reads back as "10201234567810" -- its own worked example.
    Reg(4002, "u64", 0x48595849_4D533330),  # model name, low half
    Reg(4018, "u64", 0x00102012_34567810),  # device serial number
    Reg(4026, "u32", 0x02030021),  # ARM software version
    Reg(4028, "u32", 0x02030014),  # main DSP software version
    Reg(4034, "u32", 0x00000101),  # ARM hardware version
    Reg(4046, "i32", 3000),  # rated power, W
    Reg(4048, "i16", 5000),  # rated frequency, 2dp -> 50.00 Hz
    Reg(4049, "u16", 23000),  # rated voltage, 2dp -> 230.00 V
    Reg(4100, "u16", 1),  # power on/off state: 1 = on
    Reg(4101, "u16", 6),  # work state: 6 = running
    Reg(4102, "u16", 1),  # grid-tie work mode: 1 = self-consumption
    Reg(4103, "u16", 1),  # grid status: 1 = normal
    Reg(4106, "u16", 3801),  # DC bus voltage, 1dp -> 380.1 V
    Reg(4109, "i16", 214),  # ambient temperature, 1dp -> 21.4 C
    Reg(4110, "i16", 305),  # AC-side temperature, 1dp -> 30.5 C
    Reg(4111, "i16", 288),  # DC-side temperature, 1dp -> 28.8 C
    Reg(4123, "u16", 1),  # meter comms: 1 = online
    Reg(4151, "i16", 5001),  # grid frequency, 2dp -> 50.01 Hz
    Reg(4152, "i32", 811),  # grid active power, 3dp kW -> 811 W
    Reg(4158, "i16", 98),  # grid power factor, 2dp -> 0.98
    Reg(4161, "u16", 23012),  # phase A voltage, 2dp -> 230.12 V
    Reg(4162, "i16", 35),  # phase A current, 1dp -> 3.5 A
    Reg(4163, "i32", 805),  # phase A active power, 3dp kW -> 805 W
    Reg(4200, "i16", 5000),  # off-grid frequency, 2dp -> 50.00 Hz
    Reg(4201, "i32", 0),  # off-grid active power
    Reg(4210, "u16", 23005),  # off-grid phase A voltage, 2dp -> 230.05 V
    Reg(4211, "i16", 12),  # off-grid phase A current, 1dp -> 1.2 A
    Reg(4212, "i32", 276),  # off-grid phase A active power, 3dp kW -> 276 W
    Reg(4500, "u32", 4820),  # inverter output today, 3dp kWh -> 4.820
    Reg(4502, "u32", 1263400),  # inverter output total -> 1263.400 kWh
    Reg(4506, "u32", 843200),  # battery charged total -> 843.200 kWh
    Reg(4510, "u32", 796100),  # battery discharged total -> 796.100 kWh
    Reg(4512, "u32", 2110),  # inverter input today -> 2.110 kWh
    Reg(4514, "u32", 901500),  # inverter input total -> 901.500 kWh
    Reg(4850, "u16", 0),  # hardware fault word
    Reg(4851, "u16", 0),  # DSP software fault word 1
    Reg(4852, "u16", 0),  # DSP software fault word 2
    Reg(4853, "u16", 0),  # DSP software fault word 3
    Reg(4857, "u16", 0),  # system software alarm word 1
    Reg(4962, "u64", 0x00000042_13571357),  # BMS serial number
    Reg(4978, "u16", 1),  # BMS pack count
    Reg(4979, "u16", 5),  # BMS work state: 5 = high-voltage discharge
    Reg(4980, "u16", 780),  # BMS SOC, 1dp -> 78.0 %
    Reg(4981, "u16", 985),  # BMS SOH, 1dp -> 98.5 %
    Reg(4982, "i16", 231),  # BMS temperature, 1dp -> 23.1 C
    Reg(4985, "i32", -420),  # BMS power, 3dp kW -> -420 W (discharging)
    Reg(4989, "u16", 3312),  # max cell voltage, 3dp -> 3.312 V
    Reg(4990, "u16", 3298),  # min cell voltage, 3dp -> 3.298 V
    Reg(4995, "i16", 240),  # max cell temperature, 1dp -> 24.0 C
    Reg(4996, "i16", 228),  # min cell temperature, 1dp -> 22.8 C
    Reg(5000, "u16", 0),  # BMS alarm word 1
    Reg(5001, "u16", 0),  # BMS alarm word 2
    Reg(5002, "u16", 0),  # BMS alarm word 3
    Reg(5020, "u16", 100),  # battery capacity, Ah
    Reg(5021, "u32", 3000),  # max discharge capability, 3dp kW -> 3000 W
    Reg(5023, "u32", 3000),  # max charge capability, 3dp kW -> 3000 W
)

# Settings, read with 0x03 and written with 0x10. Same numbering as the
# input map and a completely separate address space -- 4152 is grid power
# above and VPP minimum SOC here.
HALO_HOLDING: tuple[Reg, ...] = (
    Reg(4048, "u16", 0),  # dispatch mode: 0 = absolute power
    Reg(4049, "i32", 0),  # active power setpoint, 3dp kW
    Reg(4051, "u16", 10000),  # active power percentage, 2dp -> 100.00 %
    Reg(4121, "u16", 1),  # anti-starvation enabled
    Reg(4132, "u16", 10),  # force-charge start SOC
    Reg(4133, "u16", 15),  # off-grid minimum SOC
    Reg(4134, "u16", 10),  # self-consumption SOC
    Reg(4135, "u16", 20),  # green backup SOC
    Reg(4136, "u16", 30),  # grid backup SOC
    Reg(4137, "u16", 90),  # forced feed-in SOC
    Reg(4138, "u32", 0),  # forced feed-in power, W
    Reg(4140, "u16", 60),  # force-charge stop SOC
    Reg(4141, "u16", 10),  # discharge floor SOC
    Reg(4146, "u16", 0),  # VPP mode 2 enable
    Reg(4147, "u16", 0),  # VPP mode 2 work mode: 0 = idle
    Reg(4148, "u32", 0),  # VPP mode 2 charge power, W
    Reg(4150, "u32", 0),  # VPP mode 2 discharge power, W
    Reg(4152, "u16", 10),  # VPP minimum SOC
    Reg(4153, "u16", 0),  # VPP backup enable
    Reg(4154, "u16", 20),  # VPP backup SOC
    Reg(4155, "u16", 15),  # VPP off-grid SOC
    Reg(4162, "u16", 1),  # feed-in switch: 1 = on
    Reg(4163, "i32", 3000),  # feed-in power limit, 3dp kW -> 3000 W
    Reg(4178, "u16", 0),  # TOU period count
    Reg(4179, "u16", 0),  # TOU weekday bitmask
    Reg(4180, "u16", 0),  # TOU slot count
)

# --- HYX-H(5~25)K-HT/HTA/HTAC hybrid, from the RS485_MODBUS RTU Hybrid
# Inverter Protocol V4.1 -- registers.py's docstring. Values are a plausible
# steady state: grid-connected, self-use mode, PV producing ~5.9 kW, battery
# charging at 2.1 kW from the excess, 62% SOC. Read with function code 0x04.
#
# battery_serial_number (input 1015, S20) is omitted -- it's the only string
# register either family exposes, and encode() above has no string-packing
# convention to get right or wrong for it.
HYBRID_INPUT: tuple[Reg, ...] = (
    # Identity. Confirmed H32/H64 register widths and layout, but not
    # confirmed *values* -- unlike HALO's worked example, this document
    # doesn't give one, so these are synthetic, not vendor-quoted.
    Reg(0, "u16", 1),  # protocol version
    Reg(1, "u32", 0x01020034),  # main DSP program version, low/high H16 pair
    Reg(1001, "u32", 0x01030012),  # main program version
    Reg(1007, "u64", 0x00001234_56789012),  # inverter serial number
    # Status
    Reg(19, "i16", 352),  # inverter temperature, 1dp -> 35.2 C
    Reg(20, "i16", 410),  # boost converter temperature, 1dp -> 41.0 C
    Reg(21, "i16", 388),  # DSP temperature, 1dp -> 38.8 C
    Reg(22, "u16", 6),  # operation status: 6 = steady state operation
    Reg(23, "u16", 0),  # self-test status
    Reg(25, "u16", 1),  # grid mode: 1 = grid-connected
    Reg(26, "u16", 1),  # run command: 1 = start
    Reg(53, "u16", 1),  # grid connection success flag: 1 = successful
    Reg(1265, "u16", 1),  # current operating mode: 1 = self-use
    # Faults -- all clear
    Reg(38, "u16", 0),  # software fault word 1
    Reg(39, "u16", 0),  # software fault word 2
    Reg(40, "u16", 0),  # software fault word 3
    Reg(41, "u16", 0),  # software fault word 4
    Reg(42, "u16", 0),  # software fault word 5
    Reg(43, "u16", 0),  # software fault word 6
    Reg(44, "u16", 0),  # software alarm word 1
    Reg(45, "u16", 0),  # software alarm word 2
    Reg(1041, "u16", 0),  # DSP comm fault
    Reg(1042, "u16", 0),  # device comm fault
    Reg(1043, "u16", 0),  # device alarm
    # Grid, three phase
    Reg(300, "u16", 23120),  # phase A voltage, 2dp -> 231.20 V
    Reg(301, "u16", 23095),  # phase B voltage, 2dp -> 230.95 V
    Reg(302, "u16", 23108),  # phase C voltage, 2dp -> 231.08 V
    Reg(303, "i16", 5002),  # grid frequency, 2dp -> 50.02 Hz
    Reg(311, "i16", 45),  # phase A current, 2dp -> 0.45 A
    Reg(312, "i16", 42),  # phase B current, 2dp -> 0.42 A
    Reg(313, "i16", 44),  # phase C current, 2dp -> 0.44 A
    Reg(316, "i16", 310),  # active power, W
    Reg(317, "i16", 15),  # reactive power, var
    Reg(318, "i16", 311),  # apparent power, VA
    Reg(370, "i16", 105),  # phase A power, W
    Reg(371, "i16", 100),  # phase B power, W
    Reg(372, "i16", 105),  # phase C power, W
    # Off-grid (EPS) -- inactive, grid-connected in this scenario
    Reg(500, "u16", 0),  # phase A voltage
    Reg(501, "u16", 0),  # phase B voltage
    Reg(502, "u16", 0),  # phase C voltage
    Reg(503, "i16", 0),  # frequency
    Reg(507, "u16", 0),  # active power
    Reg(520, "u16", 0),  # phase A power
    Reg(521, "u16", 0),  # phase B power
    Reg(522, "u16", 0),  # phase C power
    # PV, two strings, daytime production
    Reg(600, "u16", 3805),  # bus voltage, 1dp -> 380.5 V
    Reg(604, "u16", 3650),  # PV1 voltage, 1dp -> 365.0 V
    Reg(605, "u16", 82),  # PV1 current, 1dp -> 8.2 A
    Reg(606, "u16", 2993),  # PV1 power, W
    Reg(610, "u16", 3680),  # PV2 voltage, 1dp -> 368.0 V
    Reg(611, "u16", 79),  # PV2 current, 1dp -> 7.9 A
    Reg(612, "u16", 2907),  # PV2 power, W
    # Battery -- charging from excess solar
    Reg(802, "u16", 3798),  # LLC bus voltage, 1dp -> 379.8 V
    Reg(804, "u16", 0),  # discharge voltage (not discharging)
    Reg(805, "u16", 0),  # discharge current
    Reg(806, "u16", 0),  # discharge power
    Reg(819, "u16", 3512),  # charge voltage, 1dp -> 351.2 V
    Reg(820, "u16", 152),  # charge current, 1dp -> 15.2 A
    Reg(821, "u16", 2100),  # charge power, W
    Reg(1051, "u16", 1),  # operating status: 1 = charge
    Reg(1052, "u16", 35120),  # voltage, 2dp -> 351.20 V
    Reg(1053, "i16", 152),  # current, 1dp -> 15.2 A
    Reg(1054, "u16", 62),  # SOC, % (unscaled, unlike HALO's 1dp gauge)
    Reg(1055, "u16", 99),  # SOH, %
    Reg(1056, "i16", 29),  # temperature, C (unscaled)
    Reg(1061, "u16", 3315),  # max cell voltage, 3dp -> 3.315 V
    Reg(1062, "u16", 3298),  # min cell voltage, 3dp -> 3.298 V
    Reg(1063, "u16", 31),  # max cell temperature, C
    Reg(1064, "u16", 27),  # min cell temperature, C
    # Sign convention undocumented for this field (unlike settings register
    # 3015 below, which is explicit) -- positive picked arbitrarily here,
    # consistent with the charging scenario above.
    Reg(1065, "i16", 2100),  # battery power, W
    Reg(1097, "u16", 200),  # nominal capacity, unit unstated in the document
    # Energy, lifetime totals
    Reg(1128, "u32", 45230),  # output A, 1dp -> 4523.0 kWh
    Reg(1130, "u32", 44980),  # output B, 1dp -> 4498.0 kWh
    Reg(1132, "u32", 45110),  # output C, 1dp -> 4511.0 kWh
    Reg(1146, "u32", 28450),  # charge total, 1dp -> 2845.0 kWh
    Reg(1148, "u32", 26890),  # discharge total, 1dp -> 2689.0 kWh
)

# Settings, read with 0x03, written with 0x06/0x10. Registers 1007, 1009
# (absolute time, time zone), 3120 (baud rate) and 3121 (Modbus address) are
# deliberately absent here too, matching HybridSettings' own exclusion of
# them -- see its docstring.
HYBRID_HOLDING: tuple[Reg, ...] = (
    Reg(3000, "u16", 1),  # scheduling enabled: 1 = Modbus control active
    Reg(3002, "u16", 1),  # power command: 1 = power on
    Reg(3004, "u16", 0),  # control mode: 0 = battery power control (default)
    Reg(3015, "i32", -2100),  # battery power, W -- negative = charging
    Reg(3112, "u16", 0),  # max charge current, A (0 = no limit)
    Reg(3113, "u16", 0),  # max discharge current, A (0 = no limit)
    Reg(1099, "u16", 0),  # feed-in enable: 0 = export control disabled
    Reg(1100, "u16", 0),  # feed-in power, W
    Reg(1101, "u16", 0),  # anti-starvation protection: 0 = open (enabled)
    Reg(1102, "u16", 20),  # self-use SOC, %
    Reg(1103, "u16", 20),  # backup SOC, %
    Reg(1104, "u16", 10),  # forced-charge SOC, %
    Reg(1105, "u16", 90),  # feed-in SOC, %
    Reg(1106, "u16", 15),  # off-grid SOC, %
)

PROFILES = {
    "halo": (HALO_INPUT, HALO_HOLDING),
    "hybrid": (HYBRID_INPUT, HYBRID_HOLDING),
}


def words_from_regs(regs: tuple[Reg, ...]) -> dict[int, int]:
    """Flatten a register table into address -> word."""
    out: dict[int, int] = {}
    for reg in regs:
        for offset, word in enumerate(encode(reg.kind, reg.value)):
            out[reg.address + offset] = word
    return out


def words_from_snapshot(path: Path, space: str) -> dict[int, int]:
    """Read one register space out of a modbus_probe snapshot."""
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in snapshot["registers"].get(space, {}).items()}


# Real inverters serve a contiguous register file and return zero for the
# addresses the protocol leaves undefined, so a client that pools
# neighbouring fields into one block read succeeds even across documented
# gaps. Holes narrower than this are filled with zeros to match. Wider ones
# stay absent, so whole unimplemented regions remain genuinely unreadable and
# a probe sweep still finds real boundaries to bisect.
GAP_FILL = 64


def to_simdata(words: dict[int, int]) -> list[SimData]:
    """Turn sparse address -> word pairs into SimData blocks."""
    if not words:
        return []
    blocks: list[SimData] = []
    addresses = sorted(words)
    run_start = run_prev = addresses[0]
    run: list[int] = [words[run_start]]
    for address in addresses[1:]:
        if address - run_prev - 1 <= GAP_FILL:
            run.extend(words.get(a, 0) for a in range(run_prev + 1, address + 1))
        else:
            blocks.append(SimData(run_start, values=run, datatype=DataType.REGISTERS))
            run_start, run = address, [words[address]]
        run_prev = address
    blocks.append(SimData(run_start, values=run, datatype=DataType.REGISTERS))
    return blocks


def build_device(args: argparse.Namespace) -> SimDevice:
    """Assemble the simulated device from a profile or a snapshot."""
    if args.snapshot:
        path = Path(args.snapshot).expanduser().resolve()
        input_words = words_from_snapshot(path, "input")
        holding_words = words_from_snapshot(path, "holding")
        source = f"snapshot {path.name}"
    else:
        input_regs, holding_regs = PROFILES[args.profile]
        input_words = words_from_regs(input_regs)
        holding_words = words_from_regs(holding_regs)
        source = f"profile {args.profile}"

    print(
        f"serving {source}: "
        f"{len(input_words)} input, {len(holding_words)} holding registers",
        file=sys.stderr,
    )
    if not input_words and not holding_words:
        sys.exit("nothing to serve -- the profile or snapshot is empty")

    # The four-block form keeps input and holding as separate address
    # spaces, which is what the HYXI protocol actually does. A shared block
    # would silently merge 4152-grid-power with 4152-VPP-min-SOC.
    #
    # HYXI exposes no coils or discrete inputs, but pymodbus requires all
    # four blocks to be non-empty, so those two get a single unused bit.
    unused_bit = [SimData(0, count=1, datatype=DataType.BITS)]
    return SimDevice(
        id=args.unit,
        simdata=(
            unused_bit,
            list(unused_bit),
            to_simdata(holding_words),
            to_simdata(input_words),
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    """Define the CLI."""
    parser = argparse.ArgumentParser(
        prog="fake_hyxi",
        description="Serve a simulated HYXI inverter over Modbus TCP.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--profile",
        default="halo",
        choices=sorted(PROFILES),
        help="built-in register map (default: halo)",
    )
    source.add_argument(
        "--snapshot", metavar="FILE", help="replay a tools/modbus_probe.py capture"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--unit", type=int, default=1, help="slave address")
    return parser


async def serve(args: argparse.Namespace) -> None:
    """Run until interrupted."""
    device = build_device(args)
    print(
        f"listening on {args.host}:{args.port}, unit {args.unit} "
        f"(probe it with --framer socket)",
        file=sys.stderr,
    )
    await StartAsyncTcpServer(device, address=(args.host, args.port))


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

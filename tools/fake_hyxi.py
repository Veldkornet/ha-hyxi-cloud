#!/usr/bin/env python3
"""A simulated HYXI inverter on Modbus TCP.

Development tooling, not part of the integration. It exists so the local
transport can be built and tested without hardware on the desk, and so a
capture from a real inverter can be replayed deterministically in CI.

Two ways to seed it:

    # the HALO map, transcribed from HYXIPower's Micro Storage RS485
    # document V1.0 (shared on issue #662 with permission to publish)
    ./tools/fake_hyxi.py --profile halo --port 5020

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

PROFILES = {"halo": (HALO_INPUT, HALO_HOLDING)}


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
        path = Path(args.snapshot)
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

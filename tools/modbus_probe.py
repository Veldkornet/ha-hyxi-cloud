#!/usr/bin/env python3
"""Read-only Modbus explorer for HYXI inverters.

Development tooling, not part of the integration. It exists to derive a
register map from real hardware, because HYXI ships a different protocol
document per product family and we only hold the Micro Storage (HALO) one.

The tool never writes. Every code path is a read, and there is no argument
that makes it emit a write function code.

Typical bring-up session:

    # 1. find out which address ranges the device answers at all
    ./tools/modbus_probe.py sweep --tcp 192.168.1.50:502 -o idle.json

    # 2. change one thing (force a charge from the app or the cloud API),
    #    then snapshot again
    ./tools/modbus_probe.py sweep --tcp 192.168.1.50:502 -o charging.json

    # 3. see which registers moved, and how each one decodes
    ./tools/modbus_probe.py diff idle.json charging.json

Step 3 is the point: a register whose value tracks a state you controlled
deliberately is identified evidence, which a vendor table alone never is.

A snapshot separates three outcomes per address, because conflating them
would corrupt the map being derived:

    registers   the device returned a value
    absent      the device said no such register (a fact about the map)
    failed      we could not find out -- busy, corrupt frame, timeout
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from modbus_connection import (
        IllegalDataAddressError,
        IllegalFunctionError,
        ModbusConnectionError,
        ModbusExceptionError,
        ModbusProtocolError,
        ModbusSerialParams,
        ModbusTcpParams,
        ModbusTimeoutError,
    )
    from modbus_connection.tmodbus import ModbusConnection
except ImportError:  # pragma: no cover - dev tool, not shipped
    sys.exit(
        "modbus-connection is not installed.\n"
        "  uv pip install 'modbus-connection[tmodbus]'\n"
        "or, inside this repo:\n"
        "  uv sync --extra modbus"
    )

# The HALO protocol document asks for >200ms between frames. Nothing says the
# hybrid units are more relaxed, so the probe uses the conservative figure for
# every device until measurement says otherwise.
DEFAULT_SPACING = 0.2

# The same document caps a request at 100 registers. Sweeping starts there and
# bisects downward whenever the device objects.
DEFAULT_BLOCK = 100

# How many times to re-ask after a recoverable fault before giving up on a
# block. RS485 frame corruption and "server busy" are both normal on a real
# bus, and neither means the register is missing.
RETRIES = 2

# Ranges worth trying when the caller gives no --range. The low band is where
# the HYX-H hybrid/all-in-one registers live; the high band is the HALO micro
# storage map from the V1.0 document. Sweeping both on an unknown device costs
# a few seconds and immediately says which family it belongs to.
DEFAULT_RANGES = ((1000, 1350), (4000, 4200), (4500, 4520), (4840, 5030))

SPACES = ("input", "holding")

# A Modbus exception response that genuinely means "this address is not part
# of my map". Everything else in the exception family is a condition of the
# device or gateway at this moment, not a statement about the register.
ABSENT_ERRORS = (IllegalDataAddressError,)


class UnsupportedSpace(Exception):
    """The device rejected the function code itself, not the address.

    Raised so the caller abandons the whole register space at once. Bisecting
    on it would issue thousands of requests to re-learn the same answer.
    """


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _i16(word: int) -> int:
    return struct.unpack(">h", struct.pack(">H", word & 0xFFFF))[0]


def _pair(words: list[int], index: int, little_word: bool) -> int | None:
    """Combine two adjacent registers, honouring word order.

    HYXI's documents describe multi-register values as "high 16 bits after,
    low 16 bits first" -- little word order -- but that has only been proven
    for the HALO map, so both orders are always reported.
    """
    if index + 1 >= len(words):
        return None
    if little_word:
        high, low = words[index + 1], words[index]
    else:
        high, low = words[index], words[index + 1]
    return ((high & 0xFFFF) << 16) | (low & 0xFFFF)


def _signed32(value: int) -> int:
    return value - 0x100000000 if value >= 0x80000000 else value


def decodings(words: list[int], index: int) -> dict[str, Any]:
    """Every plausible reading of the register at `index`.

    A blind sweep cannot know a register's type, so the tool declines to guess
    and shows the alternatives instead. Comparing two snapshots is what
    collapses them to one answer.
    """
    word = words[index]
    out: dict[str, Any] = {
        "hex": f"0x{word:04X}",
        "u16": word & 0xFFFF,
        "i16": _i16(word),
    }
    for label, little in (("le", True), ("be", False)):
        raw = _pair(words, index, little)
        if raw is None:
            continue
        out[f"u32_{label}"] = raw
        out[f"i32_{label}"] = _signed32(raw)
    return out


def fixed(value: float) -> str:
    """Format without scientific notation and without inventing precision.

    The default float repr switches to exponent form around 1e16 and `:g`
    truncates at six significant digits -- which would mangle exactly the
    large lifetime-energy counters this tool exists to identify.
    """
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def scaled(value: int) -> dict[str, str]:
    """The decimal-place variants HYXI's register tables use (1, 2 and 3)."""
    return {f"d{places}": fixed(value / (10**places)) for places in (1, 2, 3)}


class Probe:
    """Owns the read/bisect loop and the three-way outcome bookkeeping."""

    def __init__(self, unit: Any, block: int, retries: int = RETRIES) -> None:
        self._unit = unit
        self._block = block
        self._retries = retries
        self.absent: dict[str, list[list[int]]] = {s: [] for s in SPACES}
        self.failed: dict[str, list[list[int]]] = {s: [] for s in SPACES}

    async def _read(self, space: str, address: int, count: int) -> list[int]:
        if space == "input":
            return await self._unit.read_input_registers(address, count)
        return await self._unit.read_holding_registers(address, count)

    async def read_range(self, space: str, start: int, end: int) -> dict[int, int]:
        """Read [start, end) in blocks, narrowing around addresses the device rejects.

        A device answering only part of a span is the normal case, not an
        error -- the gaps are as informative as the values, so they are
        recorded rather than raised.
        """
        found: dict[int, int] = {}
        address = start
        while address < end:
            count = min(self._block, end - address)
            await self._read_block(space, address, count, found)
            address += count
        return found

    async def _attempt(self, space: str, address: int, count: int) -> list[int]:
        """One block read, retrying conditions that say nothing about the map.

        Frame desync, "server busy" and timeouts are all recoverable on a real
        RS485 bus. Treating them as "register absent" would write false gaps
        into the very map this tool exists to establish.
        """
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                return await self._read(space, address, count)
            except (ModbusProtocolError, ModbusTimeoutError) as err:
                last = err
            except ModbusExceptionError as err:
                if isinstance(err, IllegalFunctionError):
                    raise UnsupportedSpace(space) from err
                if isinstance(err, ABSENT_ERRORS):
                    raise
                last = err
            if attempt < self._retries:
                await asyncio.sleep(0.5 * (attempt + 1))
        raise last if last else RuntimeError("unreachable")

    async def _read_block(
        self, space: str, address: int, count: int, found: dict[int, int]
    ) -> None:
        try:
            words = await self._attempt(space, address, count)
        except ModbusConnectionError, UnsupportedSpace:
            raise
        except ABSENT_ERRORS:
            if count == 1:
                self.absent[space].append([address, address])
                return
            half = count // 2
            await self._read_block(space, address, half, found)
            await self._read_block(space, address + half, count - half, found)
            return
        except (
            ModbusProtocolError,
            ModbusTimeoutError,
            ModbusExceptionError,
        ) as err:
            print(
                f"  {type(err).__name__} on {space} "
                f"{address}..{address + count - 1} after {self._retries} retries",
                file=sys.stderr,
            )
            self.failed[space].append([address, address + count - 1])
            return

        for offset, word in enumerate(words):
            found[address + offset] = word


def parse_ranges(values: list[str] | None) -> tuple[tuple[int, int], ...]:
    """Turn inclusive --range LO-HI arguments into half-open pairs."""
    if not values:
        return DEFAULT_RANGES
    ranges: list[tuple[int, int]] = []
    for raw in values:
        low, _, high = raw.partition("-")
        start = int(low)
        ranges.append((start, int(high) + 1 if high else start + 1))
    return tuple(ranges)


def build_connection(args: argparse.Namespace) -> ModbusConnection:
    """Construct a connection from --serial or --tcp."""
    if args.serial:
        if args.framer == "socket":
            sys.exit("--framer socket is not valid for a serial port")
        params: Any = ModbusSerialParams(
            device=args.serial,
            baudrate=args.baud,
            bytesize=8,
            parity=args.parity,
            stopbits=args.stopbits,
            framer=args.framer,
        )
    else:
        host, _, port = args.tcp.partition(":")
        params = ModbusTcpParams(
            host=host,
            port=int(port) if port else 502,
            framer=args.framer,
        )
    return ModbusConnection(
        params,
        timeout=args.timeout,
        message_spacing=args.spacing,
    )


async def run_sweep(args: argparse.Namespace) -> int:
    """Read every requested range and write a snapshot."""
    if args.block < 1:
        sys.exit("--block must be at least 1")

    ranges = parse_ranges(args.range)
    spaces = SPACES if args.space == "both" else (args.space,)

    connection = build_connection(args)
    probe = Probe(connection.for_unit(args.unit), args.block, args.retries)

    result: dict[str, dict[str, int]] = {}
    total = 0
    try:
        # Fail loudly on an unreachable device rather than writing an
        # empty-but-valid snapshot that a later diff would report as
        # "nothing changed".
        try:
            await connection.connect()
        except (ModbusConnectionError, ModbusTimeoutError, OSError) as err:
            print(f"could not reach {args.serial or args.tcp}: {err}", file=sys.stderr)
            return 1

        for space in spaces:
            merged: dict[int, int] = {}
            try:
                for start, end in ranges:
                    print(f"sweeping {space} {start}..{end - 1}", file=sys.stderr)
                    merged.update(await probe.read_range(space, start, end))
            except UnsupportedSpace:
                print(
                    f"  device rejected the {space} function code entirely, skipping",
                    file=sys.stderr,
                )
            result[space] = {str(k): v for k, v in sorted(merged.items())}
            total += len(merged)
            print(f"  {len(merged)} registers answered", file=sys.stderr)
    except ModbusConnectionError as err:
        print(f"connection lost mid-sweep: {err}", file=sys.stderr)
        return 1
    finally:
        await connection.close()

    if not total:
        print(
            "no registers answered anywhere -- check unit id, framer, baud rate "
            "and wiring before trusting this as a negative result",
            file=sys.stderr,
        )
        return 1

    snapshot = {
        "meta": {
            "captured_at": _now(),
            "target": args.serial or args.tcp,
            "unit": args.unit,
            "label": args.label,
            # Inclusive [lo, hi], matching --range and the absent/failed lists.
            "ranges": [[lo, hi - 1] for lo, hi in ranges],
            "spacing": args.spacing,
            "register_count": total,
        },
        "registers": result,
        "absent": probe.absent,
        "failed": probe.failed,
    }
    Path(args.out).write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({total} registers)", file=sys.stderr)
    return 0


def _words_at(snapshot: dict, space: str, address: int) -> list[int]:
    """Return the register at `address` plus its neighbour, for 32-bit decoding."""
    regs = snapshot["registers"].get(space, {})
    words = []
    for offset in (0, 1):
        value = regs.get(str(address + offset))
        if value is None:
            break
        words.append(value)
    return words


def _report_appearance(
    space: str, b_regs: dict, a_regs: dict, b_label: str, a_label: str
) -> int:
    """Report registers present in only one snapshot.

    A register that only answers while the device is charging is exactly the
    evidence this tool is looking for, so its arrival is reported as loudly as
    a value change.
    """
    gained = sorted(int(k) for k in a_regs.keys() - b_regs.keys())
    lost = sorted(int(k) for k in b_regs.keys() - a_regs.keys())
    for addresses, where in ((gained, a_label), (lost, b_label)):
        if addresses:
            shown = ", ".join(str(a) for a in addresses[:20])
            more = f" (+{len(addresses) - 20} more)" if len(addresses) > 20 else ""
            print(f"\n  {len(addresses)} {space} registers answered only in {where}:")
            print(f"    {shown}{more}")
    return len(gained) + len(lost)


def run_diff(args: argparse.Namespace) -> int:
    """Report registers that differ between two snapshots."""
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))

    b_label = before["meta"].get("label") or Path(args.before).stem
    a_label = after["meta"].get("label") or Path(args.after).stem

    total = 0
    for space in SPACES:
        b_regs = before["registers"].get(space, {})
        a_regs = after["registers"].get(space, {})
        total += _report_appearance(space, b_regs, a_regs, b_label, a_label)

        changed = sorted(
            int(k) for k in b_regs.keys() & a_regs.keys() if b_regs[k] != a_regs[k]
        )
        if not changed:
            continue
        total += len(changed)
        print(f"\n=== {space} registers: {len(changed)} changed ===")
        for address in changed:
            old = b_regs[str(address)]
            new = a_regs[str(address)]
            print(
                f"\n{address}  0x{old:04X} -> 0x{new:04X}"
                f"   (i16 {_i16(old)} -> {_i16(new)}, "
                f"delta {_i16(new) - _i16(old):+d})"
            )
            b_dec = decodings(_words_at(before, space, address), 0)
            a_dec = decodings(_words_at(after, space, address), 0)
            for key, was in b_dec.items():
                if key == "hex" or key not in a_dec:
                    # Absent from a_dec means the neighbouring register was
                    # never captured, not that the value became null.
                    continue
                if was == a_dec[key]:
                    continue
                print(f"    {key:<8} {b_label}={was:<14} {a_label}={a_dec[key]}")

    if not total:
        print("no registers changed between the two snapshots")
    else:
        print(f"\n{total} registers differ in total")
    return 0


def run_show(args: argparse.Namespace) -> int:
    """Print every decoding of one address in a snapshot."""
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    shown = False
    for space in SPACES:
        words = _words_at(snapshot, space, args.addr)
        if not words:
            continue
        shown = True
        print(f"\n=== {space} register {args.addr} ===")
        for key, value in decodings(words, 0).items():
            if key == "hex":
                print(f"  {key:<8} {value}")
                continue
            variants = " ".join(f"{k}={v}" for k, v in scaled(value).items())
            print(f"  {key:<8} {value:<14} {variants}")
    if not shown:
        print(f"register {args.addr} is not present in this snapshot")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Define the CLI."""
    parser = argparse.ArgumentParser(
        prog="modbus_probe",
        description="Read-only Modbus explorer for HYXI inverters.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sweep = sub.add_parser("sweep", help="read register ranges into a snapshot")
    target = sweep.add_mutually_exclusive_group(required=True)
    target.add_argument("--serial", metavar="DEVICE", help="e.g. /dev/ttyUSB0")
    target.add_argument("--tcp", metavar="HOST[:PORT]", help="e.g. 192.168.1.50:502")
    sweep.add_argument("--baud", type=int, default=115200, help="serial only")
    sweep.add_argument("--parity", default="N", choices=("N", "E", "O"))
    sweep.add_argument("--stopbits", type=int, default=1, choices=(1, 2))
    sweep.add_argument(
        "--framer",
        default="rtu",
        choices=("socket", "rtu", "ascii"),
        help="rtu (default) suits RS485 and most serial-to-Ethernet gateways; "
        "socket is native Modbus TCP and is invalid for --serial",
    )
    sweep.add_argument("--unit", type=int, default=1, help="slave address")
    sweep.add_argument("--space", default="both", choices=("input", "holding", "both"))
    sweep.add_argument(
        "--range",
        action="append",
        metavar="LO-HI",
        help="inclusive register range; repeatable. Defaults to the known "
        "HYX-H and HALO bands.",
    )
    sweep.add_argument("--block", type=int, default=DEFAULT_BLOCK)
    sweep.add_argument("--spacing", type=float, default=DEFAULT_SPACING)
    sweep.add_argument("--timeout", type=float, default=10.0)
    sweep.add_argument("--retries", type=int, default=RETRIES)
    sweep.add_argument(
        "--label", default="", help="what the device was doing, e.g. 'charging 2kW'"
    )
    sweep.add_argument("-o", "--out", required=True, metavar="FILE")

    diff = sub.add_parser("diff", help="compare two snapshots")
    diff.add_argument("before")
    diff.add_argument("after")

    show = sub.add_parser("show", help="all decodings of one address")
    show.add_argument("snapshot")
    show.add_argument("addr", type=int)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    if args.command == "sweep":
        return asyncio.run(run_sweep(args))
    if args.command == "diff":
        return run_diff(args)
    return run_show(args)


if __name__ == "__main__":
    sys.exit(main())

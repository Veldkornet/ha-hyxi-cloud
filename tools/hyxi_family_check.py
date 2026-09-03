#!/usr/bin/env python3
"""Stand-alone HYXI Modbus family check -- hand this to a user, nothing to install.

The integration decides on its own whether a local Modbus device speaks the
HALO (HYX-MS3000AC micro-ESS) register map or the HYX-H hybrid-inverter one.
It does that by reading one "signature" register per family and looking at
what comes back. When it guesses wrong -- a HALO showing up as a Hybrid
Inverter, every sensor stuck at 0 -- this script shows exactly what those two
registers return, so the guess can be understood and fixed.

Only the Python standard library is used, so it runs anywhere Python 3.9 or
newer is available: a laptop on the same network, or Home Assistant's
"Advanced SSH & Web Terminal" add-on.

    python3 hyxi_family_check.py 192.168.1.50            # port 502, unit 1
    python3 hyxi_family_check.py 192.168.1.50 502 1
    python3 hyxi_family_check.py 192.168.1.50 --expect halo

It is read-only. Every request is function code 0x04 (read input registers);
there is no code path in this file that writes anything to the device.

Copy the whole "REPORT" section it prints and paste it into the GitHub issue.
"""

from __future__ import annotations

import argparse
import platform
import socket
import sys
import time
from datetime import datetime
from typing import NamedTuple

# --- What the integration looks for -----------------------------------------
# Mirrored from custom_components/hyxi_cloud/const.py -- MODBUS_FAMILY_SIGNATURES,
# the two plausibility bounds, and the setup probe's timing. Keep them in step
# with that file; tests/test_modbus_probe.py pins the addresses and bounds.
#
# Order matters: HALO is checked first, on switch_status rather than a BMS
# register, because a HALO with an offline BMS answers nothing in the BMS
# range while still answering the hybrid protocol-version register.
FAMILY_SIGNATURES = (
    ("halo", 4100, "power on/off state"),
    ("hybrid", 0, "communication protocol version"),
)
# HALO's switch_status signature is a documented on/off flag: 0 or 1, nothing
# else.
HALO_SWITCH_SIGNATURE_MAX_RAW = 1
# The hybrid signature is a positive protocol version. Zero reads as an
# unmapped/blank register (HALO gateways commonly return it), not as a hybrid.
HYBRID_PROTOCOL_SIGNATURE_MIN_RAW = 1
# The setup probe's read wait and inter-frame gap (const.DETECTION_TIMEOUT and
# DETECTION_MESSAGE_SPACING).
DETECTION_TIMEOUT = 3.0
DETECTION_MESSAGE_SPACING = 0.5

READ_INPUT_REGISTERS = 0x04

# socket = native Modbus-TCP / MBAP framing (a gateway in "Modbus TCP to RTU"
# mode). rtu = raw RTU frames tunnelled over the TCP socket (a gateway in
# transparent / "Protocol: None" mode). With no --framer, both are tried,
# stopping at the first that a device answers.
FRAMERS = ("socket", "rtu")

MODBUS_EXCEPTIONS = {
    0x01: "IllegalFunction",
    0x02: "IllegalDataAddress",
    0x03: "IllegalDataValue",
    0x04: "ServerDeviceFailure",
    0x05: "Acknowledge",
    0x06: "ServerDeviceBusy",
    0x08: "MemoryParityError",
    0x0A: "GatewayPathUnavailable",
    0x0B: "GatewayTargetFailedToRespond",
}
# A gateway saying "I couldn't reach anything past me" is not proof a device
# is there; any other exception means something answered and rejected the
# register. Mirrors config_flow._read_modbus_signature.
GATEWAY_EXCEPTIONS = (0x0A, 0x0B)


class Target(NamedTuple):
    """Where and who to talk to."""

    host: str
    port: int
    unit: int


class RegisterRead(NamedTuple):
    """The outcome of one read request. `raw` is the bytes that came back, kept
    so the report can show exactly what an odd (non-ok) reply contained."""

    status: str  # "ok" | "exception" | "malformed" | "gateway" | "silent" | "unparsed"
    words: tuple[int, ...] = ()
    detail: str = ""
    raw: bytes = b""

    @property
    def device_present(self) -> bool:
        """Whether this counts as 'a device answered' for family detection.

        A malformed reply (a zero-length or odd-length read result) counts:
        something addressed to this request came back, it just isn't this
        family's register. Mirrors config_flow._read_modbus_signature, which
        treats ModbusProtocolError the same as an exception response.
        """
        return self.status in ("ok", "exception", "malformed")

    @property
    def signature_value(self) -> int | None:
        """The single register value, when the read succeeded."""
        return self.words[0] if self.status == "ok" else None


class Probe(NamedTuple):
    """One planned read: a label for the report, plus the address and length.

    `span` rather than `count` -- NamedTuple fields cannot shadow `tuple.count`.
    """

    label: str
    address: int
    span: int


# The two signature reads (count 1, exactly as the integration issues them),
# derived from FAMILY_SIGNATURES so the addresses have one source.
SIGNATURE_PROBES = tuple(
    Probe(f"{family:<6} signature   input {address}", address, 1)
    for family, address, _description in FAMILY_SIGNATURES
)
# Context from each map -- enough to tell whether the device genuinely
# populates that family (a model string, real temperatures, a SOC) or just
# echoes a stray value at the signature address. Only read once a signature
# has drawn some response.
CONTEXT_PROBES = (
    Probe("HALO model         input 4002-4009", 4002, 8),
    Probe("HALO status        input 4101-4103", 4101, 3),
    Probe("HALO SOC block     input 4978-4982", 4978, 5),
    Probe("hybrid DSP version input 1-2", 1, 2),
    Probe("hybrid status      input 19-23", 19, 5),
)
PROBE_PLAN = SIGNATURE_PROBES + CONTEXT_PROBES


# --- Talking Modbus over a socket ------------------------------------------


def _crc16(payload: bytes) -> bytes:
    """Modbus RTU CRC-16, returned low byte first (wire order)."""
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            carry = crc & 1
            crc >>= 1
            if carry:
                crc ^= 0xA001
    return bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def _build_request(framer: str, unit: int, address: int, count: int, tid: int) -> bytes:
    pdu = (
        bytes((READ_INPUT_REGISTERS,))
        + address.to_bytes(2, "big")
        + count.to_bytes(2, "big")
    )
    if framer == "socket":
        # transaction id, protocol id (0), length, unit id, then the PDU. The
        # transaction id is bumped per request so a late reply to a
        # timed-out earlier read can be told apart from this one's.
        return (
            tid.to_bytes(2, "big")
            + b"\x00\x00"
            + (len(pdu) + 1).to_bytes(2, "big")
            + bytes((unit,))
            + pdu
        )
    frame = bytes((unit,)) + pdu
    return frame + _crc16(frame)


def _mbap_frames(raw: bytes) -> list[bytes]:
    """Split a buffer into whole MBAP frames (it may hold a stale one first)."""
    frames = []
    offset = 0
    while offset + 7 <= len(raw):
        end = offset + 6 + int.from_bytes(raw[offset + 4 : offset + 6], "big")
        if end > len(raw):
            break
        frames.append(raw[offset:end])
        offset = end
    return frames


def _rtu_frames(raw: bytes) -> list[bytes]:
    """Split a buffer into CRC-valid RTU read responses, newest last."""
    frames = []
    offset = 0
    while offset + 5 <= len(raw):
        end = offset + (5 if raw[offset + 1] & 0x80 else 5 + raw[offset + 2])
        if end > len(raw):
            break
        frame = raw[offset:end]
        if _crc16(frame[:-2]) == frame[-2:]:
            frames.append(frame)
        offset = end
    return frames


def _extract_pdu(framer: str, raw: bytes, tid: int = 0) -> bytes | None:
    """Pull this request's response PDU out of the buffer, or None.

    The buffer can begin with a stale reply to a previous, timed-out read; for
    MBAP the transaction id picks this request's frame out, for RTU the last
    CRC-valid frame is this one's.
    """
    if framer == "socket":
        for frame in _mbap_frames(raw):
            if frame[0:2] == tid.to_bytes(2, "big") and frame[2:4] == b"\x00\x00":
                return frame[7:] or None
        return None
    rtu = _rtu_frames(raw)
    return rtu[-1][1:-2] if rtu else None


def _flush(sock: socket.socket) -> None:
    """Discard anything already waiting -- a late reply to an earlier read."""
    sock.setblocking(False)
    try:
        while sock.recv(4096):
            continue
    except OSError:
        pass  # nothing left buffered
    finally:
        sock.setblocking(True)


def _drain(sock: socket.socket, framer: str, tid: int, first_timeout: float) -> bytes:
    """Read until this request's frame is complete, or the device goes quiet.

    Returns as soon as `_extract_pdu` can make sense of what has arrived, so a
    device that answers promptly is not held up by a fixed settle delay.
    """
    sock.settimeout(first_timeout)
    try:
        buf = sock.recv(4096)
    except OSError:
        return b""
    if not buf:
        return b""
    sock.settimeout(0.2)
    while _extract_pdu(framer, buf, tid) is None:
        try:
            more = sock.recv(4096)
        except OSError:
            break
        if not more:
            break
        buf += more
    return buf


def _interpret(pdu: bytes) -> RegisterRead:
    function = pdu[0]
    if function == (READ_INPUT_REGISTERS | 0x80):
        code = pdu[1] if len(pdu) > 1 else 0
        name = MODBUS_EXCEPTIONS.get(code, f"exception 0x{code:02X}")
        kind = "gateway" if code in GATEWAY_EXCEPTIONS else "exception"
        return RegisterRead(kind, detail=name)
    if function != READ_INPUT_REGISTERS or len(pdu) < 2:
        return RegisterRead("unparsed", detail=f"unexpected function 0x{function:02X}")
    byte_count = pdu[1]
    body = pdu[2 : 2 + byte_count]
    # Every register is two bytes, so a real read reply has an even, non-zero
    # byte count that matches the body length. Anything else is a reply that
    # doesn't fit the request -- a device answering an out-of-map register
    # with a zero-length result, most often. Don't half-decode it, but it
    # still means a device replied (see RegisterRead.device_present).
    if byte_count < 2 or byte_count % 2 or len(body) != byte_count:
        return RegisterRead(
            "malformed", detail=f"function-04 reply, {byte_count} data bytes"
        )
    words = tuple(
        int.from_bytes(body[i : i + 2], "big") for i in range(0, byte_count, 2)
    )
    return RegisterRead("ok", words=words)


# --- Running the plan -----------------------------------------------------


class Attempt(NamedTuple):
    """The result of trying one framer. `reads` is keyed by register address."""

    connected: bool
    reads: dict[int, RegisterRead]
    error: str = ""

    @property
    def answered(self) -> bool:
        """True if at least one request drew any Modbus response, gateway
        target-failures included -- enough to print the read detail."""
        return any(
            r.status in ("ok", "exception", "gateway") for r in self.reads.values()
        )

    @property
    def device_replied(self) -> bool:
        """True if a device past the gateway actually answered -- a value or a
        register rejection, not the gateway reporting it could not be reached."""
        return any(r.device_present for r in self.reads.values())


def probe(target: Target, framer: str, timeout: float, spacing: float) -> Attempt:
    """Connect once with `framer` and run the plan: both signatures, then the
    context reads only if a signature drew a response."""
    try:
        sock = socket.create_connection((target.host, target.port), timeout=timeout)
    except OSError as err:
        return Attempt(connected=False, reads={}, error=str(err))

    reads: dict[int, RegisterRead] = {}
    tid = 0

    def run(step: Probe) -> None:
        nonlocal tid
        tid += 1
        if tid > 1:
            time.sleep(spacing)
        _flush(sock)
        try:
            sock.sendall(
                _build_request(framer, target.unit, step.address, step.span, tid)
            )
        except OSError as err:
            reads[step.address] = RegisterRead("silent", detail=f"send failed: {err}")
            return
        raw = _drain(sock, framer, tid, timeout)
        if not raw:
            reads[step.address] = RegisterRead(
                "silent", detail=f"no response within {timeout:.1f}s"
            )
            return
        pdu = _extract_pdu(framer, raw, tid)
        if pdu is None:
            reads[step.address] = RegisterRead(
                "unparsed", detail=f"{len(raw)} bytes, no valid {framer} reply", raw=raw
            )
            return
        reads[step.address] = _interpret(pdu)._replace(raw=raw)

    try:
        for step in SIGNATURE_PROBES:
            run(step)
        if any(r.status in ("ok", "exception", "gateway") for r in reads.values()):
            for step in CONTEXT_PROBES:
                run(step)
    finally:
        sock.close()
    return Attempt(connected=True, reads=reads)


# --- The verdict --------------------------------------------------------


def signature_reads(
    reads: dict[int, RegisterRead],
) -> list[tuple[str, int, str, RegisterRead]]:
    """The (family, address, description, read) tuples in FAMILY_SIGNATURES order."""
    return [
        (family, addr, desc, reads[addr]) for family, addr, desc in FAMILY_SIGNATURES
    ]


def classify(
    signatures: list[tuple[str, int, str, RegisterRead]],
) -> tuple[str | None, str]:
    """Reproduce config_flow._detect_family_on_unit. Returns (family, reason)."""
    device_answered = False
    skipped: list[str] = []
    for family, address, description, read in signatures:
        device_answered |= read.device_present
        value = read.signature_value
        if value is None:
            continue
        if family == "halo" and not 0 <= value <= HALO_SWITCH_SIGNATURE_MAX_RAW:
            skipped.append(
                f"input {address} = {value} is outside 0..{HALO_SWITCH_SIGNATURE_MAX_RAW}, "
                "so it cannot be a switch on/off state"
            )
            continue
        if family == "hybrid" and value < HYBRID_PROTOCOL_SIGNATURE_MIN_RAW:
            skipped.append(
                f"input {address} = {value} is not a positive protocol version"
            )
            continue
        return (
            family,
            f"input register {address} answered with {value}, a plausible {description}",
        )
    if device_answered:
        why = "; ".join(skipped) if skipped else "neither signature carried a value"
        return None, f"device is reachable but its family is unidentified ({why})"
    return None, "nothing answered either signature register"


# --- Reporting --------------------------------------------------------


def _format_read(address: int, read: RegisterRead) -> str:
    if read.status != "ok":
        labels = {
            "exception": "Modbus exception",
            "malformed": "answered, but with a zero/odd-length frame",
            "gateway": "gateway could not reach the device",
            "silent": "no reply",
            "unparsed": "unreadable reply",
        }
        text = f"      {labels.get(read.status, read.status)} ({read.detail})"
        if read.raw:
            text += f"\n      raw bytes: {read.raw.hex(' ')}"
        return text
    lines = [
        f"      input {address + offset} = {word:5d}   0x{word:04X}"
        for offset, word in enumerate(read.words)
    ]
    if len(read.words) >= 2:
        combined = (read.words[1] << 16) | read.words[0]
        lines.append(
            f"      as 32-bit (low word first) = {combined}   0x{combined:08X}"
        )
    return "\n".join(lines)


NO_REPLY_CAUSES = (
    "wrong RS485 unit / slave id -- try --unit 1 through 247",
    "wrong wire framing for this gateway -- the other --framer, if one was forced",
    "the two RS485 data wires (A/D+ and B/D-) swapped at either end",
    "the gateway's own web page set to a baud rate the inverter isn't using",
    "A and B not actually landed on the gateway's A and B terminals",
    "the inverter's RS485 port not enabled, or the inverter powered down",
)


def _render_attempt(framer: str, attempt: Attempt) -> list[str]:
    wire = "Modbus-TCP / MBAP" if framer == "socket" else "raw RTU over TCP"
    lines = [f"--- framer '{framer}' ({wire}) ---"]
    if not attempt.connected:
        lines.append(f"      TCP connection failed: {attempt.error}")
    elif not attempt.answered:
        lines.append(
            "      connected to the gateway, but no request got a Modbus reply"
        )
    else:
        for step in PROBE_PLAN:
            if step.address in attempt.reads:
                lines.append(f"  {step.label}:")
                lines.append(_format_read(step.address, attempt.reads[step.address]))
    lines.append("")
    return lines


def _render_no_device(attempts: dict[str, Attempt]) -> list[str]:
    if not any(a.connected for a in attempts.values()):
        return [
            "VERDICT: could not open a TCP connection to the gateway.",
            "",
            "Nothing accepted a connection at that address and port. Check:",
            "  - the IP and port are the gateway's, not the inverter's",
            "  - the gateway is powered on and reachable from Home Assistant",
            "  - no firewall or VLAN sits between them",
        ]

    saw_gateway = any(
        r.status == "gateway"
        for attempt in attempts.values()
        for r in attempt.reads.values()
    )
    lines = ["VERDICT: could not talk to a Modbus device on either framing.", ""]
    if saw_gateway:
        lines += [
            "The gateway replied, but only to say it could not reach anything on the",
            "RS485 side (a gateway target-failure). So the TCP half is fine and the",
            "RS485 half is not. Likely causes, roughly in order:",
        ]
    else:
        lines += [
            "The gateway answers on TCP but nothing behind it replied. Likely causes,",
            "roughly in order:",
        ]
    lines.extend(f"  - {cause}" for cause in NO_REPLY_CAUSES)
    lines += [
        "",
        "A reversed A/B pair looks exactly like silence here -- Modbus cannot tell it",
        "apart from the others above. On an idle bus A-B measures about +2 to +5 V DC;",
        "a negative reading means the pair is swapped.",
    ]
    return lines


def _mismatch_hint(expected: str, detected: str | None) -> str:
    if expected == "halo" and detected == "hybrid":
        return (
            "  This device did not give a plausible 0/1 at the HALO signature\n"
            "  (input 4100) but did answer the hybrid protocol-version register,\n"
            "  so it was called a hybrid. If it really is a HALO, the report above\n"
            "  shows what input 4100 returned -- please paste it into the issue.\n"
            "  (If input 4100 shows an old cached type in Home Assistant rather\n"
            "  than what the script sees, remove and re-add the Modbus device.)"
        )
    return (
        "  Please paste this whole report into the issue -- the signature values\n"
        "  above show why detection goes the way it does."
    )


def _render_verdict(
    expect: str | None, chosen: str, reads: dict[int, RegisterRead]
) -> list[str]:
    signatures = signature_reads(reads)
    family, reason = classify(signatures)
    lines = [f"VERDICT (framer '{chosen}'):", ""]
    for fam, address, description, read in signatures:
        if read.status == "ok":
            state = f"value {read.words[0]} (0x{read.words[0]:04X})"
        else:
            state = f"{read.status} -- {read.detail}"
            if read.raw:
                state += f"  [raw {read.raw.hex(' ')}]"
        lines.append(
            f"  {fam:<7} signature  input {address:<5d} [{description}]: {state}"
        )
    lines.append("")
    if family is None:
        lines.append("  -> family: UNIDENTIFIED")
    else:
        lines.append(
            f"  -> the integration would detect this device as: {family.upper()}"
        )
    lines.append(f"     because {reason}")
    lines.append("")

    if expect and family == expect:
        lines.append(f"  expected {expect} -- MATCH.")
    elif expect:
        lines.append(
            f"  expected {expect} but detection lands on "
            f"{family or 'unidentified'} -- MISMATCH."
        )
        lines.append("")
        lines.append(_mismatch_hint(expect, family))
    return lines


def build_report(
    target: Target, expect: str | None, attempts: dict[str, Attempt], chosen: str | None
) -> str:
    out = [
        "=" * 70,
        "HYXI Modbus family check -- REPORT",
        "=" * 70,
        f"target        : {target.host}:{target.port}  unit {target.unit}",
        f"run at        : {datetime.now().isoformat(timespec='seconds')}",
        f"python        : {platform.python_version()} on {platform.platform()}",
        "script version: 1",
        "",
    ]
    for framer, attempt in attempts.items():
        out.extend(_render_attempt(framer, attempt))

    out.append("-" * 70)
    if chosen is None:
        out.extend(_render_no_device(attempts))
    else:
        out.extend(_render_verdict(expect, chosen, attempts[chosen].reads))
    return "\n".join(out)


# --- CLI --------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hyxi_family_check",
        description="Read-only check of which HYXI Modbus register map a device speaks.",
    )
    parser.add_argument("host", help="the gateway's IP address or hostname")
    parser.add_argument(
        "port", nargs="?", type=int, default=502, help="TCP port (default 502)"
    )
    parser.add_argument(
        "unit", nargs="?", type=int, default=1, help="RS485 unit / slave id (default 1)"
    )
    parser.add_argument(
        "--framer",
        choices=FRAMERS,
        help="force the wire framing instead of trying both",
    )
    parser.add_argument(
        "--expect",
        choices=("halo", "hybrid"),
        help="the family you believe this device is, for a MATCH/MISMATCH line",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DETECTION_TIMEOUT,
        help=f"per-request read timeout in seconds (default {DETECTION_TIMEOUT})",
    )
    parser.add_argument(
        "--spacing",
        type=float,
        default=DETECTION_MESSAGE_SPACING,
        help=f"gap between requests in seconds (default {DETECTION_MESSAGE_SPACING})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = Target(args.host, args.port, args.unit)
    framers = (args.framer,) if args.framer else FRAMERS

    attempts: dict[str, Attempt] = {}
    for framer in framers:
        print(f"probing with framer '{framer}' ...", file=sys.stderr)
        attempts[framer] = probe(target, framer, args.timeout, args.spacing)
        if attempts[framer].device_replied:
            break  # this framer reached a device; no need to try the other

    chosen = next(
        (framer for framer, attempt in attempts.items() if attempt.device_replied),
        None,
    )
    print(build_report(target, args.expect, attempts, chosen))
    return 0 if chosen else 1


if __name__ == "__main__":
    sys.exit(main())

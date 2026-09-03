"""Unit-level checks for the two stand-alone Modbus diagnostic tools.

`tools/hyxi_family_check.py` (zero-dependency, handed to end users) and
`tools/modbus_probe.py`'s ``detect`` subcommand both reimplement
``config_flow._detect_family_on_unit`` outside the integration. Here we pin
their signature registers, plausibility bounds and probe timing to the shipped
constants, and exercise the frame parser.

The authoritative check -- that both classifiers reach the same verdict as the
*real* config-flow detection -- lives in
``tests/test_config_flow.py::test_diagnostic_tools_mirror_shipped_family_detection``,
which drives ``_probe_and_detect_modbus`` and reuses the helpers below.
"""

import pytest

pytest.importorskip("modbus_connection")  # tools/modbus_probe.py imports it at load

from custom_components.hyxi_cloud import const
from tools import hyxi_family_check as fc
from tools import modbus_probe as mp

# A "reading" is one signature register's result: ("value", n) for a plausible
# read, or ("exception",) / ("gateway",) / ("timeout",) for the failure modes
# config_flow._read_modbus_signature distinguishes.


def fc_signature_read(reading):
    """Turn a reading into the object hyxi_family_check.classify expects."""
    kind = reading[0]
    if kind == "value":
        return fc.RegisterRead("ok", words=(reading[1],))
    if kind == "timeout":
        return fc.RegisterRead("silent")
    return fc.RegisterRead(kind)  # "exception" | "gateway"


def mp_signature_outcome(reading):
    """Turn a reading into the object modbus_probe.classify_family expects."""
    kind = reading[0]
    if kind == "value":
        return mp.SignatureOutcome("value", value=reading[1])
    return mp.SignatureOutcome(kind)  # "exception" | "gateway" | "timeout"


def fc_signature_list(reg0, reg4980):
    return [
        ("hybrid", 0, "protocol version", fc_signature_read(reg0)),
        ("halo", 4980, "state of charge", fc_signature_read(reg4980)),
    ]


def mp_signature_list(reg0, reg4980):
    return [
        ("hybrid", 0, "protocol version", mp_signature_outcome(reg0)),
        ("halo", 4980, "state of charge", mp_signature_outcome(reg4980)),
    ]


def test_signature_registers_track_the_shipped_constant():
    shipped = [
        (family, addr) for family, _space, addr in const.MODBUS_FAMILY_SIGNATURES
    ]
    assert [(f, a) for f, a, _d in fc.FAMILY_SIGNATURES] == shipped
    assert [(f, a) for f, _s, a, _d in mp.DETECT_SIGNATURES] == shipped


@pytest.mark.parametrize("module", [fc, mp], ids=["hyxi_family_check", "modbus_probe"])
def test_plausibility_bounds_track_the_shipped_constants(module):
    assert module.HALO_SOC_SIGNATURE_MAX_RAW == const.HALO_SOC_SIGNATURE_MAX_RAW
    assert (
        module.HYBRID_PROTOCOL_SIGNATURE_MIN_RAW
        == const.HYBRID_PROTOCOL_SIGNATURE_MIN_RAW
    )


def test_setup_probe_timing_is_mirrored_from_the_shipped_constants():
    assert fc.DETECTION_TIMEOUT == const.DETECTION_TIMEOUT
    assert fc.DETECTION_MESSAGE_SPACING == const.DETECTION_MESSAGE_SPACING
    assert mp.DETECT_TIMEOUT == const.DETECTION_TIMEOUT
    assert mp.DETECT_SPACING == const.DETECTION_MESSAGE_SPACING


# --- frame parsing -----------------------------------------------------

# A read-input-registers reply for one register (value 0x0001), and the
# IllegalDataAddress exception reply, in each framing.
_MBAP_OK = bytes.fromhex("00 07 00 00 00 05 01 04 02 00 01")  # transaction id 7
_MBAP_EXC = bytes.fromhex("00 07 00 00 00 03 01 84 02")
_RTU_OK = bytes.fromhex("01 04 02 00 01 78 f0")
_RTU_EXC = bytes.fromhex("01 84 02 c2 c1")


@pytest.mark.parametrize(
    ("framer", "raw", "tid", "expected"),
    [
        ("socket", _MBAP_OK, 7, b"\x04\x02\x00\x01"),
        ("socket", _MBAP_EXC, 7, b"\x84\x02"),
        ("socket", _MBAP_OK, 8, None),  # reply to a different request
        (
            "socket",
            bytes.fromhex("00 07 00 ff 00 03 01 84 02"),
            7,
            None,
        ),  # bad proto id
        (
            "socket",
            bytes.fromhex("00 06 00 00 00 03 01 84 02") + _MBAP_OK,
            7,
            b"\x04\x02\x00\x01",  # stale reply to request 6 skipped, tid 7 picked
        ),
        ("rtu", _RTU_OK, 0, b"\x04\x02\x00\x01"),
        ("rtu", bytes.fromhex("01 04 02 00 01 ff ff"), 0, None),  # bad CRC
        ("rtu", _RTU_EXC + _RTU_OK, 0, b"\x04\x02\x00\x01"),  # newest valid frame wins
    ],
    ids=[
        "socket-ok",
        "socket-exception",
        "socket-wrong-tid",
        "socket-bad-protocol-id",
        "socket-stale-frame-skipped",
        "rtu-ok",
        "rtu-bad-crc",
        "rtu-newest-frame-wins",
    ],
)
def test_family_check_frame_parsing(framer, raw, tid, expected):
    assert fc._extract_pdu(framer, raw, tid) == expected

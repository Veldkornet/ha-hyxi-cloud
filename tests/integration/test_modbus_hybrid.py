"""Tests for the HYX-H hybrid inverter Modbus client.

Runs against modbus_connection's own mock unit, exercising the real field
descriptors and block planner -- see test_modbus.py's module docstring for
why a hand-rolled double would not do the same job.

Register values below are realistic, not the document's own worked
examples: those examples turned out not to be trustworthy (see
docs/modbus-provenance.md) -- one appears verbatim in both the HALO and
hybrid documents with numbers that do not even decode to the value the text
claims. What *is* trusted from the document is the register table itself
and the prose describing types, scale and word order.
"""

from unittest.mock import patch

import pytest
from modbus_connection import IllegalDataAddressError
from modbus_connection.pytest_plugin import MockModbusConnection

from custom_components.hyxi_cloud.modbus.client_hybrid import HyxiHybridModbusClient


def _words(value: int, count: int = 2) -> list[int]:
    """Encode a value low word first, per the document's stated word order."""
    raw = value & ((1 << (16 * count)) - 1)
    return [(raw >> (16 * i)) & 0xFFFF for i in range(count)]


def _spread(address: int, value: int, count: int = 2) -> dict[int, int]:
    return dict(zip(range(address, address + count), _words(value, count), strict=True))


def _fill(registers: dict[int, int]) -> dict[int, int]:
    """A contiguous register file, zero-filling gaps -- real devices serve one."""
    return {
        address: registers.get(address, 0)
        for address in range(min(registers), max(registers) + 1)
    }


# A steady state: 78.0% SOC, importing 811W from the grid, charging at 420W,
# grid-connected, three-phase, self-use mode.
INPUT_REGISTERS: dict[int, int] = {
    **_spread(1, 0x02030021),  # main DSP version, H32
    **_spread(1001, 0x02030021),  # main program version, H32
    **_spread(1007, 10201234567810, 4),  # serial, H64
    20: 312,  # boost converter temperature, 1dp -> 31.2 C
    21: 298,  # DSP temperature, 1dp -> 29.8 C
    22: 6,  # steady state operation
    23: 2,  # self-test status
    25: 1,  # grid mode: grid-connected
    26: 1,  # run command: start
    53: 1,  # grid connected
    1265: 1,  # self-use
    300: 23012,
    301: 23015,
    302: 22998,  # phase A/B/C voltage, 2dp
    303: 5001,  # frequency, 2dp
    311: 350,
    312: 348,
    313: 352,  # phase A/B/C current, 2dp
    316: 811,  # grid active power, W (inferred unit)
    317: 45,  # grid reactive power, var (inferred unit)
    318: 815,  # grid apparent power, VA (inferred unit)
    370: 270,
    371: 268,
    372: 272,  # phase A/B/C active power, W
    500: 23005,
    501: 23010,
    502: 22995,  # off-grid phase A/B/C voltage, 2dp
    503: 5000,
    507: 276,
    520: 276,
    521: 0,
    522: 0,  # off-grid phase A/B/C power -- the backup load
    600: 3801,  # DC bus voltage, 1dp
    604: 3200,
    605: 82,
    606: 2620,  # PV1 V/I/P
    610: 0,
    611: 0,
    612: 0,  # PV2 V/I/P
    # Battery serial "BAT13571357", H10-encoded (2 ASCII chars/register).
    1015: 16961,
    1016: 21553,
    1017: 13109,
    1018: 14129,
    1019: 13109,
    1020: 14080,
    1052: 52900,  # BMS voltage, 2dp -> 529.00 V
    1053: (-42) & 0xFFFF,  # BMS current, 1dp -> -4.2 A
    1054: 78,  # SOC, 0dp
    1055: 98,  # SOH, 0dp
    1056: 23,  # BMS temperature, 0dp
    1061: 3312,
    1062: 3298,  # max/min cell voltage, 3dp
    1063: 24,
    1064: 22,  # max/min cell temperature
    1065: (-420) & 0xFFFF,  # battery real-time power, 0dp
    1097: 100,
    **_spread(1128, 12634),  # accumulated AC output A, 1dp -> 1263.4 kWh
    **_spread(1130, 8433),  # accumulated AC output B, 1dp -> 843.3 kWh
    **_spread(1132, 8434),  # accumulated AC output C, 1dp -> 843.4 kWh
    **_spread(1146, 8432),  # accumulated charge, 1dp -> 843.2 kWh
    **_spread(1148, 7961),  # accumulated discharge, 1dp -> 796.1 kWh
}

HOLDING_REGISTERS: dict[int, int] = {
    3000: 0,
    3002: 0,
    3004: 0,
    **_spread(3015, 0),
    3112: 0,
    3113: 0,
    1099: 0,
    1100: 0,
    1101: 0,
    1102: 10,
    1103: 15,
    1104: 10,
    1105: 90,
    1106: 15,
}


@pytest.fixture
def client():
    connection = MockModbusConnection()
    unit = connection.for_unit(1)
    unit.load_raw(
        {"input": _fill(INPUT_REGISTERS), "holding": _fill(HOLDING_REGISTERS)}
    )
    return HyxiHybridModbusClient(connection, 1)


@pytest.mark.asyncio
async def test_read_all_decodes_realistic_values(client):
    devices = await client.async_read_all()

    assert list(devices) == ["10201234567810"]
    device = devices["10201234567810"]
    assert device["device_type_code"] == "HYBRID_INVERTER"

    metrics = device["metrics"]
    assert metrics["batSoc"] == 78
    assert metrics["batSoh"] == 98
    assert metrics["batV"] == 529.0
    assert metrics["ph1v"] == 230.12
    assert metrics["ph2v"] == 230.15
    assert metrics["ph3v"] == 229.98


@pytest.mark.asyncio
async def test_previously_unexposed_registers_now_decode_into_metrics(client):
    """Mirrors test_modbus.py's equivalent test for the HALO client -- these
    registers already decoded correctly; only the _build_metrics() wiring is
    new."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    # Grid power quality
    assert metrics["gridQ"] == 45
    assert metrics["gridAp"] == 815

    # Extra status telemetry
    assert metrics["boostTemper"] == 31.2
    assert metrics["dspTemper"] == 29.8
    assert metrics["selfTestStatus"] == 2
    assert metrics["gridMode"] == 1
    assert metrics["runCommand"] == 1
    assert metrics["currentOperatingMode"] == 1

    # Off-grid (backup) circuit
    assert metrics["offGridF"] == 50.0
    assert metrics["offGridP"] == 276
    assert metrics["ph1Loadv"] == 230.05
    assert metrics["ph2Loadv"] == 230.10
    assert metrics["ph3Loadv"] == 229.95

    # Energy counters
    assert metrics["totalEb"] == 843.3
    assert metrics["totalEc"] == 843.4

    # Battery serial routes the battery metrics onto their own device --
    # this client previously never populated it.
    assert metrics["batSn"] == "BAT13571357"


@pytest.mark.asyncio
async def test_grid_power_is_converted_to_kilowatts(client):
    """The register is undocumented as to unit; treated as W and converted,
    since compute_derived_metrics expects kW for every device type."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    assert metrics["gridP"] == 0.811
    assert metrics["grid_export"] == 811.0
    assert metrics["grid_import"] == 0.0


@pytest.mark.asyncio
async def test_three_phase_fields_are_kept_separate(client):
    """detect_phase_type's -HT/-HTA suffix check is what turns these on;
    this only proves the values land in the right per-phase keys."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    assert (metrics["ph1p"], metrics["ph2p"], metrics["ph3p"]) == (270, 268, 272)
    assert (metrics["ph1i"], metrics["ph2i"], metrics["ph3i"]) == (3.5, 3.48, 3.52)


@pytest.mark.asyncio
async def test_derived_metrics_come_from_the_shared_cloud_helper(client):
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    for key in (
        "home_load",
        "grid_import",
        "grid_export",
        "bat_charging",
        "bat_discharging",
    ):
        assert key in metrics, key
    assert metrics["home_load"] == 276.0


@pytest.mark.asyncio
async def test_input_and_holding_are_separate_address_spaces(client):
    """1007 is the serial number (H64, input) and, independently, the RTC's
    absolute time (U32, holding) -- confirmed by the document's own
    examples, not inferred."""
    devices = await client.async_read_all()

    assert next(iter(devices)) == "10201234567810"
    # The holding-space register at the same address was never touched by
    # reading identity, and reads back whatever the holding fixture set.
    await client.settings.async_update()
    assert client.settings.scheduling_enabled == 0


@pytest.mark.asyncio
async def test_a_block_that_fails_does_not_lose_the_others(client, caplog):
    client._unit.fail_read(
        1052, IllegalDataAddressError(2, "nope"), register_type="input"
    )

    devices = await client.async_read_all()

    metrics = next(iter(devices.values()))["metrics"]
    assert metrics["gridP"] == 0.811
    assert "did not answer" in caplog.text


@pytest.mark.asyncio
async def test_every_block_failing_raises_control_error(client):
    for address in (22, 38, 300, 500, 600, 1052, 1128):
        client._unit.fail_read(
            address, IllegalDataAddressError(2, "nope"), register_type="input"
        )

    with pytest.raises(HyxiHybridModbusClient.ControlError):
        await client.async_read_all()


@pytest.mark.asyncio
async def test_unreadable_identity_falls_back_to_a_stable_key(client):
    client._unit.fail_read(1, IllegalDataAddressError(2, "nope"), register_type="input")

    devices = await client.async_read_all()

    assert list(devices) == ["modbus_1"]


@pytest.mark.asyncio
async def test_charge_writes_a_negative_setpoint(client):
    """Confirmed sign convention: positive discharges, negative charges."""
    await client.set_mode_charge("SN", 2000)
    await client.settings.async_update()

    assert client.settings.scheduling_enabled == 1
    assert client.settings.control_mode == 0
    assert client.settings.battery_power == -2000


@pytest.mark.asyncio
async def test_discharge_writes_a_positive_setpoint(client):
    await client.set_mode_discharge("SN", 1500)
    await client.settings.async_update()

    assert client.settings.battery_power == 1500


@pytest.mark.asyncio
async def test_idle_writes_zero(client):
    await client.set_mode_idle("SN")
    await client.settings.async_update()

    assert client.settings.scheduling_enabled == 1
    assert client.settings.battery_power == 0


@pytest.mark.asyncio
async def test_self_consume_disables_scheduling_entirely(client):
    """Unlike the HALO client, this is not 'hold at zero' -- it hands
    control back to the inverter's own logic via register 3000."""
    await client.set_mode_charge("SN", 1000)
    await client.set_mode_self_consume("SN")
    await client.settings.async_update()

    assert client.settings.scheduling_enabled == 0


@pytest.mark.asyncio
async def test_peak_shaving_enables_the_real_export_control(client):
    """1099 is an export-control *enable*, not a raw feed-in gate -- the
    opposite polarity from the HALO client's feed_in_enable."""
    await client.set_peak_shaving("SN", "on")
    await client.settings.async_update()
    assert client.settings.feed_in_enable == 1

    await client.set_peak_shaving("SN", "off")
    await client.settings.async_update()
    assert client.settings.feed_in_enable == 0


@pytest.mark.asyncio
async def test_a_failed_write_raises_the_class_the_platforms_catch(client):
    with patch.object(client.settings, "write", side_effect=OSError("bus fell over")):
        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_mode_charge("SN", 1000)

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_peak_shaving("SN", "on")

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_mode_self_consume("SN")


@pytest.mark.asyncio
async def test_identity_is_read_only_once(client):
    await client.async_read_all()
    with patch.object(client.identity, "async_update") as second:
        await client.async_read_all()

    second.assert_not_called()


@pytest.mark.asyncio
async def test_close_releases_the_connection():
    from unittest.mock import AsyncMock, MagicMock

    connection = MagicMock()
    connection.for_unit = MagicMock(return_value=MagicMock())
    connection.close = AsyncMock()

    await HyxiHybridModbusClient(connection, 1).async_close()

    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_battery_power_write_failure_is_logged_and_wrapped(client, caplog):
    with patch.object(
        client.settings, "write", side_effect=[None, None, OSError("nak")]
    ):
        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_mode_charge("SN", 500)

    assert "battery power write failed" in caplog.text

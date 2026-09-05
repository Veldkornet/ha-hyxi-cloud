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

from custom_components.hyxi_cloud.modbus.client import SETTINGS_REFRESH_SECONDS
from custom_components.hyxi_cloud.modbus.client_hybrid import HyxiHybridModbusClient
from tests.integration import settings_refresh_asserts as refresh


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
    802: 3805,  # LLC bus voltage, 1dp -> 380.5 V
    804: 5285,  # discharge voltage, 1dp -> 528.5 V
    805: 42,  # discharge current, 1dp -> 4.2 A
    806: 2220,  # discharge power, 0dp -> 2220 W
    819: 5290,  # charge voltage, 1dp -> 529.0 V
    820: 38,  # charge current, 1dp -> 3.8 A
    821: 2010,  # charge power, 0dp -> 2010 W
    # Battery serial "BAT13571357", H10-encoded (2 ASCII chars/register).
    1015: 16961,
    1016: 21553,
    1017: 13109,
    1018: 14129,
    1019: 13109,
    1020: 14080,
    1051: 1,  # operating status: charge
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
    # Daily block, U16 1dp. Per-phase / per-string fields are summed by the
    # client, so these are chosen to add up to round totals.
    1101: 52,
    1102: 34,
    1103: 34,  # daily AC output A/B/C -> eToday 12.0 kWh
    1104: 8,
    1105: 6,
    1106: 6,  # daily AC input A/B/C -> eTodayIn 2.0 kWh
    1107: 40,
    1108: 30,
    1109: 30,  # daily consumption A/B/C -> home_load_today 10.0 kWh
    1110: 91,  # daily charge -> bat_charge_today 9.1 kWh
    1111: 73,  # daily discharge -> bat_discharge_today 7.3 kWh
    1112: 5,
    1113: 3,
    1114: 2,  # daily sell A/B/C -> grid_export_today 1.0 kWh
    1115: 12,
    1116: 10,
    1117: 8,  # daily buy A/B/C -> grid_import_today 3.0 kWh
    1118: 60,
    1119: 60,  # daily PV1/PV2 -> efpv 12.0 kWh
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
    return HyxiHybridModbusClient(unit, 1)


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

    # main_program (main control) and main_dsp (power electronics
    # co-processor) versions, matching the same primary/secondary split
    # "Master"/"Secondary" name.
    assert metrics["swVerMaster"] == "2030021"
    assert metrics["swVerSlave"] == "2030021"


@pytest.mark.asyncio
async def test_battery_detail_registers_decode_into_metrics(client):
    """The charge/discharge voltage-current-power split, the LLC bus
    voltage and the BMS operating status -- mirrors test_modbus.py's
    equivalent test for the HALO client."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    assert metrics["batOperatingStatus"] == 1
    assert metrics["llcBusVoltage"] == 380.5
    assert metrics["batChargeV"] == 529.0
    assert metrics["batChargeI"] == 3.8
    assert metrics["batChargeP"] == 2010
    assert metrics["batDischargeV"] == 528.5
    assert metrics["batDischargeI"] == 4.2
    assert metrics["batDischargeP"] == 2220
    # Unit not stated in the document -- passed through raw, not guessed.
    assert metrics["batNominalCapacity"] == 100

    # bat_charge_total/bat_discharge_total alias the same register as
    # totalEchg/totalEdchg and batCharge/batDisCharge -- three cloud-API
    # field names for one quantity this hardware only exposes once. Without
    # this alias the bat_charge_total sensor (created for every hybrid/
    # all-in-one device regardless of transport) would never get a live
    # value from Modbus at all.
    assert metrics["bat_charge_total"] == metrics["totalEchg"] == metrics["batCharge"]
    assert (
        metrics["bat_discharge_total"]
        == metrics["totalEdchg"]
        == metrics["batDisCharge"]
    )


@pytest.mark.asyncio
async def test_daily_energy_block_decodes_and_sums(client):
    """The 1101-1127 daily block: per-phase / per-string fields are summed
    into the same cloud-shaped keys the cloud transport would carry, and
    the battery daily counters feed the period sensors directly."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    assert metrics["eToday"] == 12.0  # 5.2 + 3.4 + 3.4
    assert metrics["eTodayIn"] == 2.0  # 0.8 + 0.6 + 0.6
    assert metrics["home_load_today"] == 10.0  # 4.0 + 3.0 + 3.0
    assert metrics["grid_export_today"] == 1.0  # 0.5 + 0.3 + 0.2
    assert metrics["grid_import_today"] == 3.0  # 1.2 + 1.0 + 0.8
    assert metrics["efpv"] == 12.0  # 6.0 + 6.0
    assert metrics["bat_charge_today"] == 9.1
    assert metrics["bat_discharge_today"] == 7.3

    # Daily and lifetime are distinct counters, not the same register.
    assert metrics["bat_charge_today"] != metrics["bat_charge_total"]


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
async def test_total_pv_power_is_summed_from_both_strings(client):
    """No single "total PV" register on this hardware family -- ppv is
    summed locally from pv1p/pv2p rather than left permanently unknown."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    assert metrics["ppv"] == metrics["pv1p"] + metrics["pv2p"]


@pytest.mark.asyncio
async def test_total_pv_power_is_absent_not_zero_when_unreadable(client):
    """A device that answers neither PV string reports no ppv at all --
    distinct from a real 0 (no panels, nighttime), which must still show."""
    client._unit.fail_read(
        600, IllegalDataAddressError(2, "nope"), register_type="input"
    )

    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    assert "ppv" not in metrics


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
async def test_feed_in_power_write_is_unscaled_watts(client):
    """Unlike the HALO client's feed_in_power_limit, this register is
    plain watts -- no kW conversion needed."""
    await client.set_feed_in_power(4200)
    await client.settings.async_update()
    assert client.settings.feed_in_power == 4200


@pytest.mark.asyncio
async def test_max_charge_and_discharge_current_writes_land_correctly(client):
    await client.set_max_charge_current(32.5)
    await client.set_max_discharge_current(28.0)
    await client.settings.async_update()
    assert client.settings.max_charge_current == 32.5
    assert client.settings.max_discharge_current == 28.0


@pytest.mark.asyncio
async def test_power_command_writes_the_documented_values(client):
    """1 power on, 2 power off, 3 restart."""
    await client.power_on("SN")
    await client.settings.async_update()
    assert client.settings.power_command == 1

    await client.power_off("SN")
    await client.settings.async_update()
    assert client.settings.power_command == 2

    await client.restart("SN")
    await client.settings.async_update()
    assert client.settings.power_command == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "field"),
    [
        ("set_self_use_soc", "self_use_soc"),
        ("set_backup_soc", "backup_soc"),
        ("set_forced_charge_soc", "forced_charge_soc"),
        ("set_feed_in_soc", "feed_in_soc"),
        ("set_off_grid_soc", "off_grid_soc"),
    ],
)
async def test_soc_setpoint_writes_land_in_the_right_field(client, call, field):
    await getattr(client, call)(12)
    await client.settings.async_update()
    assert getattr(client.settings, field) == 12


@pytest.mark.asyncio
async def test_anti_starvation_protection_write_is_inverted_polarity(client):
    """0 open (enabled), 1 close (disabled) -- the opposite sense from the
    HALO client's anti_starvation."""
    await client.set_anti_starvation_protection(True)
    await client.settings.async_update()
    assert client.settings.anti_starvation_protection == 0

    await client.set_anti_starvation_protection(False)
    await client.settings.async_update()
    assert client.settings.anti_starvation_protection == 1


@pytest.mark.asyncio
async def test_single_register_settings_writes_use_function_code_16(client):
    """The hybrid document's function-code table lists 0x06 (write single)
    alongside 0x10, but at least one real HYX-H rejects 0x06 the same way
    the HALO does -- accepting the write but not echoing a spec-compliant
    response, so tmodbus fails it with "Expected response to match request"
    (hit on write_register(3000, 1) while the battery protection tried to
    hold SOC). HybridSettings' one-register writable fields now all pass
    force_fc16=True; none may go out as FC 0x06 (6). The two-register
    battery_power (int32) already uses 0x10 and is excluded by the length
    filter below.
    """
    events = []
    client.settings.modbus_unit.on_write(events.append)

    await client.set_mode_idle("SN")  # writes 3000, 3004
    await client.set_mode_self_consume("SN")  # writes 3000
    await client.power_off("SN")  # writes 3002
    await client.set_peak_shaving("SN", "on")  # writes 1099
    await client.set_feed_in_power(4200)  # writes 1100
    await client.set_max_charge_current(30.0)  # writes 3112
    await client.set_self_use_soc(12)  # writes 1102
    await client.set_anti_starvation_protection(True)  # writes 1101

    single_register_writes = [e for e in events if len(e.values) == 1]
    assert single_register_writes  # the FC assertion below must not be vacuous
    assert all(event.function_code == 16 for event in single_register_writes)


@pytest.mark.asyncio
async def test_a_failed_write_raises_the_class_the_platforms_catch(client):
    with patch.object(client.settings, "write", side_effect=OSError("bus fell over")):
        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_mode_charge("SN", 1000)

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_peak_shaving("SN", "on")

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_mode_self_consume("SN")

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_feed_in_power(4200)

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_max_charge_current(32.5)

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_max_discharge_current(28.0)

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.power_on("SN")

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.power_off("SN")

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.restart("SN")

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_self_use_soc(12)

        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_anti_starvation_protection(True)


@pytest.mark.asyncio
async def test_identity_is_read_only_once(client):
    await client.async_read_all()
    with patch.object(client.identity, "async_update") as second:
        await client.async_read_all()

    second.assert_not_called()


@pytest.mark.asyncio
async def test_settings_are_not_reread_within_the_refresh_window(client):
    """Hybrid side of the shared refresh-cadence checks -- see
    settings_refresh_asserts, and test_modbus.py for the HALO equivalent."""
    await refresh.settings_are_not_reread_within_the_refresh_window(client)


@pytest.mark.asyncio
async def test_settings_are_reread_once_the_refresh_window_elapses(client):
    """Hybrid side of the shared refresh-cadence checks -- see
    settings_refresh_asserts."""
    await refresh.settings_are_reread_once_the_refresh_window_elapses(
        client, SETTINGS_REFRESH_SECONDS
    )


@pytest.mark.asyncio
async def test_a_failed_settings_read_retries_after_the_refresh_window(client):
    """Hybrid side of the shared refresh-cadence checks -- see
    settings_refresh_asserts. self_use_soc/1102 is this family's field;
    vpp_min_soc/4048 is the HALO equivalent."""
    await refresh.a_failed_settings_read_retries_after_the_refresh_window(
        client, SETTINGS_REFRESH_SECONDS, 1102, "self_use_soc", 10
    )


def test_settings_refresh_can_be_forced_past_the_window(client):
    """Hybrid side of the shared refresh-cadence checks -- see
    settings_refresh_asserts."""
    refresh.settings_refresh_can_be_forced_past_the_window(client)


@pytest.mark.asyncio
async def test_battery_power_write_failure_is_logged_and_wrapped(client, caplog):
    with patch.object(
        client.settings, "write", side_effect=[None, None, OSError("nak")]
    ):
        with pytest.raises(HyxiHybridModbusClient.ControlError):
            await client.set_mode_charge("SN", 500)

    assert "battery power write failed" in caplog.text

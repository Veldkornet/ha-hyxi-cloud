"""Tests for the local Modbus transport.

These run against ``modbus_connection``'s own mock unit rather than
hand-rolled doubles, so the real field descriptors, block planner and
decoders are exercised. A hand-mocked unit would happily return whatever the
test wanted and prove nothing about whether registers.py addresses and scales
the device correctly.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from modbus_connection import IllegalDataAddressError
from modbus_connection.pytest_plugin import MockModbusConnection

from custom_components.hyxi_cloud.const import DOMAIN, MODBUS_FAMILY_SIGNATURES
from custom_components.hyxi_cloud.modbus.client import (
    SETTINGS_REFRESH_SECONDS,
    HyxiModbusClient,
)
from custom_components.hyxi_cloud.modbus.registers import HaloBattery, HaloGrid
from tests.integration import settings_refresh_asserts as refresh

_SIGNATURE_ADDRESSES = {
    address for _family, _space, address in MODBUS_FAMILY_SIGNATURES
}


def _words(value: int, count: int = 2) -> list[int]:
    """Encode a multi-register value low word first, as HYXI specifies."""
    raw = value & ((1 << (16 * count)) - 1)
    return [(raw >> (16 * i)) & 0xFFFF for i in range(count)]


def _spread(address: int, value: int, count: int = 2) -> dict[int, int]:
    """Place a multi-register value at consecutive addresses."""
    return dict(zip(range(address, address + count), _words(value, count), strict=True))


# A steady state taken straight from the Micro Storage RS485 document's own
# units: 78.0% charged, importing 0.811 kW, discharging 420 W.
INPUT_REGISTERS: dict[int, int] = {
    **_spread(4002, 0x48595849_4D533330, 4),  # model name
    **_spread(4018, 0x00102012_34567810, 4),  # serial, the document's example
    **_spread(4026, 0x02030021),  # ARM software version
    **_spread(4034, 0x00000101),  # ARM hardware version
    **_spread(4046, 3000),  # rated power
    4048: 5000,  # rated frequency, 2dp
    4049: 23000,  # rated voltage, 2dp
    **_spread(4962, 0x00000042_13571357, 4),  # BMS serial
    4100: 1,
    4101: 6,
    4102: 1,
    4103: 1,
    4104: 500,  # insulation resistance, raw, unit unconfirmed
    4105: 3,  # leakage current, raw, unit unconfirmed
    4106: 3801,  # bus voltage, 1dp -> 380.1 V
    4109: 214,
    4110: 305,  # AC-side temperature, 1dp -> 30.5 C
    4111: 288,
    4123: 1,
    4151: 5001,  # grid frequency, 2dp -> 50.01 Hz
    **_spread(4152, 811),  # grid active power, 3dp kW
    **_spread(4154, 62),  # grid reactive power, 3dp kW -> 62 var
    **_spread(4156, 823),  # grid apparent power, 3dp kW -> 823 VA
    4158: 98,
    4161: 23012,  # phase A voltage, 2dp -> 230.12 V
    4162: 35,
    **_spread(4163, 805),
    4200: 5000,
    **_spread(4201, 0),
    4210: 23005,
    4211: 12,
    **_spread(4212, 276),  # off-grid phase A power -> the backup load
    **_spread(4500, 4820),
    **_spread(4502, 1263400),  # lifetime output -> 1263.400 kWh
    **_spread(4506, 843200),
    **_spread(4510, 796100),
    **_spread(4512, 2110),
    **_spread(4514, 901500),
    4850: 0,
    4851: 0,
    4852: 0,
    4853: 0,
    4857: 0,
    4978: 1,
    4979: 5,
    4980: 780,  # SOC, 1dp -> 78.0 %
    4981: 985,
    4982: 231,
    **_spread(4985, -420 & 0xFFFFFFFF),  # battery power, 3dp kW -> -420 W
    4989: 3312,
    4990: 3298,
    4995: 240,
    4996: 228,
    5000: 17,  # BMS alarm word 1, raw
    5001: 34,  # BMS alarm word 2, raw
    5002: 68,  # BMS alarm word 3, raw
    5020: 100,  # battery capacity, Ah -- not the cloud's kWh
    **_spread(5021, 3000),
    **_spread(5023, 3000),
}

HOLDING_REGISTERS: dict[int, int] = {
    4048: 0,
    **_spread(4049, 0),
    4051: 10000,
    4121: 1,
    4132: 10,
    4133: 15,
    4134: 10,
    4140: 60,
    4141: 10,
    4146: 0,
    4147: 0,
    **_spread(4148, 0),
    **_spread(4150, 0),
    4152: 10,  # VPP minimum SOC -- NOT grid power, which is 4152 input
    4162: 1,
    **_spread(4163, 3000),
}


def _fill(registers: dict[int, int]) -> dict[int, int]:
    """Return a contiguous register file, zero-filling documented gaps.

    Real devices serve a continuous address space, so a pooled block read
    spanning an undefined address still succeeds. Modelling that here keeps
    the tests about decoding rather than about block planning.
    """
    return {
        address: registers.get(address, 0)
        for address in range(min(registers), max(registers) + 1)
    }


@pytest.fixture
def client():
    """A client bound to an in-memory device holding the document's values."""
    connection = MockModbusConnection()
    unit = connection.for_unit(1)
    unit.load_raw(
        {"input": _fill(INPUT_REGISTERS), "holding": _fill(HOLDING_REGISTERS)}
    )
    return HyxiModbusClient(unit, 1)


@pytest.mark.asyncio
async def test_read_all_decodes_the_documented_values(client):
    devices = await client.async_read_all()

    # The serial is an H64 rendered as hexadecimal, which is how the
    # document's own worked example produces "10201234567810".
    assert list(devices) == ["10201234567810"]
    device = devices["10201234567810"]
    assert device["device_type_code"] == "MICRO_STORAGE_ALL_IN_ONE"

    metrics = device["metrics"]
    assert metrics["batSoc"] == 78.0
    assert metrics["batSoh"] == 98.5
    assert metrics["batTmp"] == 23.1
    assert metrics["ph1v"] == 230.12
    assert metrics["vbus"] == 380.1
    assert metrics["tinv"] == 30.5
    assert metrics["f"] == 50.01
    assert metrics["packNum"] == 1
    assert metrics["batSn"] == "4213571357"
    # ARM (main control) and DSP (power electronics co-processor) versions,
    # matching the same primary/secondary split "Master"/"Secondary" name.
    assert metrics["swVerMaster"] == "2030021"
    assert metrics["swVerSlave"] == "0"


@pytest.mark.asyncio
async def test_previously_unexposed_registers_now_decode_into_metrics(client):
    """The registers that were modeled but not yet wired into a metric key
    -- grid power quality, off-grid circuit detail, extra status telemetry,
    the input-energy-today counter and the nameplate ratings. Each already
    decoded correctly before this; only the _build_metrics() wiring is new,
    so this proves that wiring reaches the right register with the right
    scale, not the decode path itself (covered by the tests above)."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    # Grid power quality
    assert metrics["gridQ"] == 62.0
    assert metrics["gridAp"] == 823.0
    assert metrics["gridPfd"] == 0.98

    # Off-grid (backup) circuit
    assert metrics["offGridF"] == 50.0
    assert metrics["offGridP"] == 0.0
    assert metrics["offGridV"] == 230.05
    assert metrics["offGridI"] == 1.2

    # Extra status telemetry
    assert metrics["ambientTemper"] == 21.4
    assert metrics["dcSideTemper"] == 28.8
    assert metrics["insulationResistance"] == 500
    assert metrics["leakageCurrent"] == 3
    assert metrics["meterOnline"] == 1

    # Energy counters
    assert metrics["eTodayIn"] == 2.11

    # Nameplate ratings, read once with identity
    assert metrics["ratedPower"] == 3000
    assert metrics["ratedFrequency"] == 50.0
    assert metrics["ratedVoltage"] == 230.0


@pytest.mark.asyncio
async def test_battery_detail_registers_decode_into_metrics(client):
    """BMS state, raw alarm words and the Ah-scaled capacity figure."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    assert metrics["bmsState"] == 5
    assert metrics["batAlarm1"] == 17
    assert metrics["batAlarm2"] == 34
    assert metrics["batAlarm3"] == 68
    # Ah, not the cloud's kWh -- see HaloBattery.capacity_ah's docstring.
    assert metrics["batCapacityAh"] == 100
    assert "batCap" not in metrics

    # bat_charge_total/bat_discharge_total alias the same register as
    # totalEchg/totalEdchg and batCharge/batDisCharge -- three cloud-API
    # field names for one quantity this hardware only exposes once.
    assert metrics["bat_charge_total"] == metrics["totalEchg"] == metrics["batCharge"]
    assert (
        metrics["bat_discharge_total"]
        == metrics["totalEdchg"]
        == metrics["batDisCharge"]
    )


@pytest.mark.asyncio
async def test_thirty_two_bit_values_are_read_low_word_first(client):
    """The single easiest thing to get wrong in the whole map."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    # 1263400 is 0x0013_2BE8. Read big-endian it would be 0x2BE8_0013,
    # i.e. 736232979 -- three orders of magnitude out, and still plausible
    # enough on a dashboard to go unnoticed.
    assert metrics["totalE"] == 1263.4
    assert metrics["maxChargePower"] == 3000.0


@pytest.mark.asyncio
async def test_signed_battery_power_survives_the_word_swap(client):
    devices = await client.async_read_all()

    assert devices["10201234567810"]["metrics"]["batP"] == -420.0


@pytest.mark.asyncio
async def test_grid_power_is_published_in_kilowatts(client):
    """compute_derived_metrics requires kW for Micro ESS devices."""
    devices = await client.async_read_all()
    metrics = devices["10201234567810"]["metrics"]

    assert metrics["gridP"] == 0.811
    # Proof the precondition was met: the derived watt figures are sane.
    assert metrics["grid_export"] == 811.0
    assert metrics["grid_import"] == 0.0


@pytest.mark.asyncio
async def test_derived_metrics_come_from_the_shared_cloud_helper(client):
    """The keys the Energy Dashboard needs must exist on both transports."""
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
    """4152 is grid active power read with 0x04 and VPP min SOC with 0x03."""
    await client.async_read_all()
    await client.settings.async_update()

    assert client.grid.active_power == 0.811
    assert client.settings.vpp_min_soc == 10


@pytest.mark.asyncio
async def test_a_block_that_fails_does_not_lose_the_others(client, caplog):
    client._unit.fail_read(
        4962, IllegalDataAddressError(2, "nope"), register_type="input"
    )
    client._unit.fail_read(
        4200, IllegalDataAddressError(2, "nope"), register_type="input"
    )

    devices = await client.async_read_all()

    metrics = next(iter(devices.values()))["metrics"]
    assert metrics["gridP"] == 0.811
    assert "did not answer" in caplog.text


@pytest.mark.asyncio
async def test_every_block_failing_raises_control_error(client):
    # 4978 is where the battery block starts now that the BMS serial has
    # moved into the identity component.
    for address in (4100, 4151, 4200, 4500, 4850, 4978):
        client._unit.fail_read(
            address, IllegalDataAddressError(2, "nope"), register_type="input"
        )

    with pytest.raises(HyxiModbusClient.ControlError):
        await client.async_read_all()


@pytest.mark.asyncio
async def test_unreadable_identity_falls_back_to_a_stable_key(client, caplog):
    """A device that serves telemetry but not identity still gets sensors."""
    client._unit.fail_read(
        4002, IllegalDataAddressError(2, "nope"), register_type="input"
    )

    devices = await client.async_read_all()

    assert list(devices) == ["modbus_1"]
    assert devices["modbus_1"]["model"] == "HYX-MS3000AC"
    assert "falling back to unit id" in caplog.text


@pytest.mark.asyncio
async def test_identity_is_read_only_once(client):
    await client.async_read_all()
    with patch.object(client.identity, "async_update") as second:
        await client.async_read_all()

    second.assert_not_called()


@pytest.mark.asyncio
async def test_unreadable_settings_falls_back_gracefully(client, caplog):
    """A device that rejects the settings block still gets every other
    sensor -- number entities just fall back to their restored or
    minimum value instead of the device's own current value."""
    client._unit.fail_read(
        4048, IllegalDataAddressError(2, "nope"), register_type="holding"
    )

    devices = await client.async_read_all()

    metrics = devices["10201234567810"]["metrics"]
    assert "vpp_min_soc" not in metrics
    assert "feed_in_power_limit" not in metrics
    assert "will fall back to their restored or minimum value" in caplog.text


@pytest.mark.asyncio
async def test_settings_are_not_reread_within_the_refresh_window(client):
    """HALO side of the shared refresh-cadence checks -- see
    settings_refresh_asserts, and test_modbus_hybrid.py for the hybrid
    equivalent."""
    await refresh.settings_are_not_reread_within_the_refresh_window(client)


@pytest.mark.asyncio
async def test_settings_are_reread_once_the_refresh_window_elapses(client):
    """HALO side of the shared refresh-cadence checks -- see
    settings_refresh_asserts."""
    await refresh.settings_are_reread_once_the_refresh_window_elapses(
        client, SETTINGS_REFRESH_SECONDS
    )


@pytest.mark.asyncio
async def test_a_failed_settings_read_retries_after_the_refresh_window(client):
    """HALO side of the shared refresh-cadence checks -- see
    settings_refresh_asserts. vpp_min_soc/4048 is this family's field;
    self_use_soc/1102 is the hybrid equivalent."""
    await refresh.a_failed_settings_read_retries_after_the_refresh_window(
        client, SETTINGS_REFRESH_SECONDS, 4048, "vpp_min_soc", 10
    )


def test_settings_refresh_can_be_forced_past_the_window(client):
    """HALO side of the shared refresh-cadence checks -- see
    settings_refresh_asserts."""
    refresh.settings_refresh_can_be_forced_past_the_window(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "args", "expected_mode", "power_field", "expected_power"),
    [
        ("set_mode_idle", (), 0, None, None),
        ("set_mode_charge", (1500,), 1, "vpp_charge_power", 1500),
        ("set_mode_discharge", (900,), 2, "vpp_discharge_power", 900),
        ("set_mode_self_consume", (), 3, None, None),
    ],
)
# pylint: disable-next=too-many-arguments, too-many-positional-arguments
async def test_control_writes_land_in_the_vpp_block(
    client, call, args, expected_mode, power_field, expected_power
):
    await getattr(client, call)("SN", *args)
    await client.settings.async_update()

    # The enable is written every time: 4147 is only consulted while 4146
    # enables dispatch mode 2.
    assert client.settings.vpp_enable == 1
    assert client.settings.vpp_mode == expected_mode
    if power_field:
        assert getattr(client.settings, power_field) == expected_power


@pytest.mark.asyncio
async def test_peak_shaving_closes_the_real_export_switch(client):
    """The cloud approximates this; locally there is an actual register."""
    await client.set_peak_shaving("SN", "on")
    await client.settings.async_update()
    assert client.settings.feed_in_enable == 0

    await client.set_peak_shaving("SN", "off")
    await client.settings.async_update()
    assert client.settings.feed_in_enable == 1


@pytest.mark.asyncio
async def test_feed_in_power_limit_write_converts_watts_to_kilowatts(client):
    """The register is kW-scaled; the entity and client method work in
    watts to match every other power control in this integration."""
    await client.set_feed_in_power_limit(3500)
    await client.settings.async_update()
    assert client.settings.feed_in_power_limit == 3.5


@pytest.mark.asyncio
async def test_vpp_min_soc_write_lands_in_holding_space(client):
    """4152 in holding space -- not grid active power, the same address in
    input space. See registers.py's HaloGrid."""
    await client.set_vpp_min_soc(15)
    await client.settings.async_update()
    assert client.settings.vpp_min_soc == 15


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "field"),
    [
        ("set_force_charge_start_soc", "force_charge_start_soc"),
        ("set_force_charge_stop_soc", "force_charge_stop_soc"),
        ("set_off_grid_min_soc", "off_grid_min_soc"),
        ("set_self_use_soc", "self_use_soc"),
        ("set_discharge_min_soc", "discharge_min_soc"),
    ],
)
async def test_soc_setpoint_writes_land_in_the_right_field(client, call, field):
    await getattr(client, call)(12)
    await client.settings.async_update()
    assert getattr(client.settings, field) == 12


@pytest.mark.asyncio
async def test_anti_starvation_write_is_straightforward_polarity(client):
    """0 disabled, 1 enabled -- unlike the hybrid client's inverted
    equivalent."""
    await client.set_anti_starvation(True)
    await client.settings.async_update()
    assert client.settings.anti_starvation == 1

    await client.set_anti_starvation(False)
    await client.settings.async_update()
    assert client.settings.anti_starvation == 0


@pytest.mark.asyncio
async def test_set_dispatch_enabled_toggles_the_vpp_enable_register(client):
    await client.set_dispatch_enabled(False)
    await client.settings.async_update()
    assert client.settings.vpp_enable == 0

    await client.set_dispatch_enabled(True)
    await client.settings.async_update()
    assert client.settings.vpp_enable == 1


@pytest.mark.asyncio
async def test_single_register_settings_writes_use_function_code_16(client):
    """The HALO document's function-code table lists only 0x03/0x04/0x10 --
    unlike the hybrid one, this firmware does not implement 0x06 (write
    single register). Before HaloSettings' single-register fields carried
    force_fc16=True, modbus_connection defaulted a one-word field to FC
    0x06, and the device's non-echoing response to it failed tmodbus's
    "response must match request" check -- surfacing to a user as "Expected
    response to match request" on every single-register control write, first
    reported by a HALO owner clicking the discharge control button. Every
    field written here is one register; none may go out as FC 0x06 (6).
    """
    events = []
    client.settings.modbus_unit.on_write(events.append)

    await client.set_mode_discharge("SN", 1000)
    await client.set_peak_shaving("SN", "on")
    await client.set_vpp_min_soc(15)
    await client.set_force_charge_start_soc(12)
    await client.set_force_charge_stop_soc(12)
    await client.set_off_grid_min_soc(12)
    await client.set_self_use_soc(12)
    await client.set_discharge_min_soc(12)
    await client.set_anti_starvation(True)

    single_register_writes = [e for e in events if len(e.values) == 1]
    assert single_register_writes  # the FC assertion below must not be vacuous
    assert all(event.function_code == 16 for event in single_register_writes)


@pytest.mark.asyncio
async def test_a_failed_write_raises_the_class_the_platforms_catch(client):
    """button.py and friends catch HyxiApiClient.ControlError by name."""
    with patch.object(client.settings, "write", side_effect=OSError("bus fell over")):
        with pytest.raises(HyxiModbusClient.ControlError):
            await client.set_mode_charge("SN", 1000)

        with pytest.raises(HyxiModbusClient.ControlError):
            await client.set_peak_shaving("SN", "on")

        with pytest.raises(HyxiModbusClient.ControlError):
            await client.set_feed_in_power_limit(3500)

        with pytest.raises(HyxiModbusClient.ControlError):
            await client.set_vpp_min_soc(15)

        with pytest.raises(HyxiModbusClient.ControlError):
            await client.set_force_charge_start_soc(12)

        with pytest.raises(HyxiModbusClient.ControlError):
            await client.set_anti_starvation(True)


def test_register_model_declares_the_right_spaces():
    assert HaloGrid.register_space == "input"
    assert HaloBattery.register_space == "input"
    # The document caps a request at 100 registers.
    assert HaloGrid.max_span <= 100


# --- Coordinator and setup wiring -----------------------------------------


def _modbus_entry(hass, *, options=None, **overrides):
    """A config entry describing a local Modbus device."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.hyxi_cloud.const import DOMAIN

    data = {
        "transport": "modbus",
        "modbus_type": "tcp",
        "modbus_host": "192.168.1.50",
        "modbus_port": 502,
        "modbus_unit": 1,
    }
    data.update(overrides)
    entry = MockConfigEntry(domain=DOMAIN, data=data, options=options or {})
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_coordinator_publishes_the_cloud_data_shape(hass, client):
    """Entity platforms read coordinator.data; it must match either transport."""
    from custom_components.hyxi_cloud.modbus_coordinator import HyxiModbusCoordinator

    entry = _modbus_entry(hass)
    coordinator = HyxiModbusCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()

    device = data["10201234567810"]
    assert device["device_type_code"] == "MICRO_STORAGE_ALL_IN_ONE"
    assert device["metrics"]["batSoc"] == 78.0
    assert coordinator.hyxi_metadata["api_status"] == "Online"
    assert coordinator.hyxi_metadata["last_error"] is None
    assert coordinator.hyxi_metadata["cache_active"] is False


@pytest.mark.asyncio
async def test_coordinator_polls_in_seconds_not_minutes(hass, client):
    """The cloud coordinator reads update_interval as minutes. On a wire that
    would be absurd, so the same option means seconds here."""
    from custom_components.hyxi_cloud.modbus_coordinator import HyxiModbusCoordinator

    entry = _modbus_entry(hass)
    hass.config_entries.async_update_entry(entry, options={"update_interval": 20})

    coordinator = HyxiModbusCoordinator(hass, client, entry)

    assert coordinator.update_interval.total_seconds() == 20
    # Push is a cloud webhook subscription with no local equivalent.
    assert coordinator.push_status == "unavailable"
    assert coordinator.alarm_push_status == "unavailable"


@pytest.mark.asyncio
async def test_coordinator_reports_update_failed_when_the_bus_dies(hass, client):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    from custom_components.hyxi_cloud.modbus_coordinator import HyxiModbusCoordinator

    entry = _modbus_entry(hass)
    coordinator = HyxiModbusCoordinator(hass, client, entry)

    with (
        patch.object(client, "async_read_all", side_effect=OSError("bus gone")),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()

    assert coordinator.hyxi_metadata["api_status"] == "Error"
    assert "bus gone" in coordinator.hyxi_metadata["last_error"]


@pytest.mark.asyncio
async def test_a_failing_cache_write_does_not_break_the_poll(hass, client, caplog):
    from custom_components.hyxi_cloud.modbus_coordinator import HyxiModbusCoordinator

    entry = _modbus_entry(hass)
    coordinator = HyxiModbusCoordinator(hass, client, entry)

    with patch.object(
        coordinator.device_store, "async_save", side_effect=OSError("disk full")
    ):
        data = await coordinator._async_update_data()

    assert data["10201234567810"]["metrics"]["batSoc"] == 78.0
    assert "Failed to persist devices" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "expected_params"),
    [
        ({}, "ModbusTcpParams"),
        (
            {
                "modbus_type": "serial",
                "modbus_device": "/dev/ttyUSB0",
                "modbus_baudrate": 115200,
            },
            "ModbusSerialParams",
        ),
    ],
)
async def test_setup_builds_the_right_connection_for_each_type(
    hass, overrides, expected_params
):
    from custom_components.hyxi_cloud import _build_modbus_coordinator

    entry = _modbus_entry(hass, **overrides)

    with patch("homeassistant.components.modbus.async_get_unit") as get_unit:
        coordinator = await _build_modbus_coordinator(hass, entry)

    # async_get_unit(hass, entry, params, unit_id) -- the params object is
    # what carries the serial-vs-TCP distinction downstream.
    params = get_unit.call_args.args[2]
    assert type(params).__name__ == expected_params
    assert coordinator.client is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "expected_client", "expected_spacing"),
    [
        ("halo", "HyxiModbusClient", 0.2),
        ("hybrid", "HyxiHybridModbusClient", 0.5),
        # No stored family at all -- entries created before family detection
        # existed. Defaults to hybrid, the stronger-evidenced document.
        (None, "HyxiHybridModbusClient", 0.5),
    ],
)
async def test_setup_selects_the_client_class_and_spacing_for_the_family(
    hass, family, expected_client, expected_spacing
):
    """The two documents disagree on minimum frame spacing (HALO >200ms,
    hybrid >500ms), so using the wrong client class would also mean using
    the wrong timing against real hardware."""
    from custom_components.hyxi_cloud import _build_modbus_coordinator

    overrides = {} if family is None else {"modbus_family": family}
    entry = _modbus_entry(hass, **overrides)

    with patch("homeassistant.components.modbus.async_get_unit") as get_unit:
        coordinator = await _build_modbus_coordinator(hass, entry)

    unit = get_unit.return_value
    assert type(coordinator.client).__name__ == expected_client
    # The shared connection carries no spacing of its own, so the per-family
    # inter-frame gap is set on the unit.
    unit.set_message_spacing.assert_called_once_with(expected_spacing)


@pytest.mark.asyncio
async def test_a_bus_held_on_other_link_settings_is_surfaced_not_retried(hass):
    """A second entry on a gateway another entry already holds with
    incompatible link settings can't share the one connection. HA's
    async_get_unit raises HomeAssistantError; we surface it as
    ConfigEntryError (the user must reconcile the two entries) rather than
    let a raw traceback land in SETUP_ERROR.
    """
    from homeassistant.exceptions import ConfigEntryError, HomeAssistantError

    from custom_components.hyxi_cloud import _build_modbus_coordinator

    entry = _modbus_entry(hass)

    with (
        patch(
            "homeassistant.components.modbus.async_get_unit",
            side_effect=HomeAssistantError(
                "Modbus device ('tcp', '192.168.1.50', 502) is already in use "
                "with different link settings"
            ),
        ),
        pytest.raises(ConfigEntryError),
    ):
        await _build_modbus_coordinator(hass, entry)


@pytest.mark.asyncio
async def test_the_shared_connection_closes_only_when_the_last_entry_unloads(hass):
    """Two Modbus entries on one gateway share one connection; it closes only
    when the second unloads.

    Both entries go through `_build_modbus_coordinator`, so this exercises
    our call into `async_get_unit` against Home Assistant's real refcount
    and `entry.async_on_unload` machinery -- which a hand-mocked connection
    cannot stand in for.
    """
    from homeassistant.components.modbus.connection import DATA_MODBUS_CONNECTIONS

    from custom_components.hyxi_cloud import _build_modbus_coordinator

    conn = MockModbusConnection()
    conn.close = AsyncMock()

    first = _modbus_entry(hass, modbus_unit=1)
    second = _modbus_entry(hass, modbus_unit=2)

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=conn,
    ):
        await _build_modbus_coordinator(hass, first)
        await _build_modbus_coordinator(hass, second)

    endpoint = ("tcp", "192.168.1.50", 502)
    shared = hass.data[DATA_MODBUS_CONNECTIONS][endpoint]
    assert shared.connection is conn
    assert shared.consumers == 2

    await first._async_process_on_unload(hass)
    assert shared.consumers == 1
    conn.close.assert_not_awaited()

    await second._async_process_on_unload(hass)
    conn.close.assert_awaited_once()


def _seeded_connection() -> MockModbusConnection:
    """A connection serving the document's register file."""
    connection = MockModbusConnection()
    connection.for_unit(1).load_raw(
        {"input": _fill(INPUT_REGISTERS), "holding": _fill(HOLDING_REGISTERS)}
    )
    return connection


def _seeded_hybrid_connection() -> MockModbusConnection:
    """A connection serving the hybrid document's register file -- shared
    by every test in this module that needs a full hybrid device rather
    than the HALO-shaped fixtures above."""
    from tests.integration.test_modbus_hybrid import (
        HOLDING_REGISTERS as HYBRID_HOLDING_REGISTERS,
    )
    from tests.integration.test_modbus_hybrid import (
        INPUT_REGISTERS as HYBRID_INPUT_REGISTERS,
    )
    from tests.integration.test_modbus_hybrid import _fill as _hybrid_fill

    connection = MockModbusConnection()
    connection.for_unit(1).load_raw(
        {
            "input": _hybrid_fill(HYBRID_INPUT_REGISTERS),
            "holding": _hybrid_fill(HYBRID_HOLDING_REGISTERS),
        }
    )
    return connection


# --- Reconfigure probe: a reconfigure that keeps the same bus must detect
# the family on the connection the loaded coordinator is already polling,
# not open a second master on the wire. -----------------------------------


async def _setup_and_start_reconfigure_tcp(hass, entry):
    """Set `entry` up for real, then open its reconfigure flow at the TCP step."""
    from homeassistant.config_entries import SOURCE_RECONFIGURE

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    return await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"modbus_type": "tcp"}
    )


@pytest.mark.asyncio
async def test_reconfigure_that_keeps_the_bus_probes_on_the_shared_connection(hass):
    """A reconfigure that leaves host/port unchanged detects the family on
    the coordinator's own connection -- no second connection is opened, and
    the family is still re-verified."""
    from homeassistant.components.modbus.connection import DATA_MODBUS_CONNECTIONS
    from homeassistant.data_entry_flow import FlowResultType

    entry = _modbus_entry(
        hass, modbus_family="halo", modbus_framer="socket", modbus_unit=1
    )
    hass.config_entries.async_update_entry(entry, unique_id="192.168.1.50:502:1")

    built: list[object] = []
    conn = _seeded_connection()

    def _make(*_args, **_kwargs):
        built.append(conn)
        return conn

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection", side_effect=_make
    ):
        flow = await _setup_and_start_reconfigure_tcp(hass, entry)

        endpoint = ("tcp", "192.168.1.50", 502)
        assert hass.data[DATA_MODBUS_CONNECTIONS][endpoint].consumers == 1

        with (
            patch(
                "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow."
                "_probe_and_detect_modbus_tcp"
            ) as standalone,
            patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
        ):
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {
                    "modbus_host": "192.168.1.50",
                    "modbus_port": 502,
                    "modbus_unit": 1,
                },
            )
            await hass.async_block_till_done()

    assert flow["type"] is FlowResultType.ABORT
    assert flow["reason"] == "reconfigure_successful"
    # The standalone probe was never used...
    standalone.assert_not_called()
    # ...only the coordinator's connection was ever built...
    assert len(built) == 1
    # ...and detection ran on it: a 1-register read at a family signature
    # address, which the coordinator's block polling never issues.
    reads = conn.for_unit(1).read_events
    assert any(
        e.register_type == "input"
        and e.address in _SIGNATURE_ADDRESSES
        and e.count == 1
        for e in reads
    )
    assert entry.data["modbus_family"] == "halo"
    assert entry.data["modbus_framer"] == "socket"


@pytest.mark.asyncio
async def test_reconfigure_to_a_different_bus_uses_the_standalone_probe(hass):
    """Moving to another host has no overlap to avoid, so the standalone
    probe (its own connection, framer sweep) still runs."""
    entry = _modbus_entry(hass, modbus_family="halo", modbus_framer="socket")
    hass.config_entries.async_update_entry(entry, unique_id="192.168.1.50:502:1")

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        flow = await _setup_and_start_reconfigure_tcp(hass, entry)

        with (
            patch(
                "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow."
                "_probe_and_detect_modbus_tcp",
                return_value=(None, "halo", "socket"),
            ) as standalone,
            patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
        ):
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {
                    "modbus_host": "192.168.1.60",
                    "modbus_port": 502,
                    "modbus_unit": 1,
                },
            )
            await hass.async_block_till_done()

    standalone.assert_called_once()
    assert flow["reason"] == "reconfigure_successful"
    assert entry.data["modbus_host"] == "192.168.1.60"


@pytest.mark.asyncio
async def test_reconfigure_falls_back_to_standalone_probe_when_the_bus_is_down(hass):
    """A held connection that is not up right now -- a transient outage, or a
    device swapped in under an unchanged address -- means nothing is polling
    successfully, so the standalone probe (framer sweep, fast fail) takes over."""
    entry = _modbus_entry(hass, modbus_family="halo", modbus_framer="socket")
    hass.config_entries.async_update_entry(entry, unique_id="192.168.1.50:502:1")

    conn = _seeded_connection()
    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection", return_value=conn
    ):
        flow = await _setup_and_start_reconfigure_tcp(hass, entry)
        conn.simulate_connection_lost()

        with (
            patch(
                "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow."
                "_probe_and_detect_modbus_tcp",
                return_value=(None, "halo", "socket"),
            ) as standalone,
            patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
        ):
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {
                    "modbus_host": "192.168.1.50",
                    "modbus_port": 502,
                    "modbus_unit": 1,
                },
            )
            await hass.async_block_till_done()

    standalone.assert_called_once()
    assert flow["reason"] == "reconfigure_successful"


@pytest.mark.asyncio
async def test_reconfigure_to_another_unit_on_the_same_bus_uses_the_shared_connection(
    hass,
):
    """Pointing the entry at a different slave on the same wire probes that
    slave on the shared connection, leaving every unit's pacing -- the
    coordinator's, and any other consumer's -- exactly as it was."""
    entry = _modbus_entry(
        hass, modbus_family="halo", modbus_framer="socket", modbus_unit=1
    )
    hass.config_entries.async_update_entry(entry, unique_id="192.168.1.50:502:1")

    conn = _seeded_connection()
    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection", return_value=conn
    ):
        flow = await _setup_and_start_reconfigure_tcp(hass, entry)
        # The coordinator set the HALO inter-frame gap on unit 1.
        assert conn.for_unit(1).message_spacing == 0.2

        with (
            patch(
                "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow."
                "_probe_and_detect_modbus_tcp"
            ) as standalone,
            patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
        ):
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {
                    "modbus_host": "192.168.1.50",
                    "modbus_port": 502,
                    "modbus_unit": 2,
                },
            )
            await hass.async_block_till_done()

    standalone.assert_not_called()
    assert flow["reason"] == "reconfigure_successful"
    assert entry.data["modbus_unit"] == 2
    # The probe read unit 2 on the shared connection...
    assert any(
        e.register_type == "input"
        and e.address in _SIGNATURE_ADDRESSES
        and e.count == 1
        for e in conn.for_unit(2).read_events
    )
    # ...without touching any unit's pacing -- unit 2 was never set, and the
    # coordinator's unit-1 gap is intact.
    assert conn.for_unit(2).message_spacing == 0.0
    assert conn.for_unit(1).message_spacing == 0.2


@pytest.mark.asyncio
async def test_reconfigure_changing_the_baud_rate_uses_the_standalone_probe(hass):
    """New link settings on a bus a coordinator holds can't share its
    connection -- async_get_temporary_unit raises, and the standalone probe
    opens its own at the new settings."""
    entry = _modbus_entry(
        hass,
        modbus_type="serial",
        modbus_device="/dev/ttyUSB0",
        modbus_baudrate=9600,
        modbus_family="halo",
        modbus_unit=1,
    )
    hass.config_entries.async_update_entry(entry, unique_id="/dev/ttyUSB0:1")

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        from homeassistant.config_entries import SOURCE_RECONFIGURE

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"modbus_type": "serial"}
        )

        with (
            patch(
                "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow."
                "_probe_and_detect_modbus",
                return_value=(None, "halo"),
            ) as standalone,
            patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
        ):
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {
                    "modbus_device": "/dev/ttyUSB0",
                    "modbus_baudrate": "115200",
                    "modbus_unit": 1,
                },
            )
            await hass.async_block_till_done()

    standalone.assert_called_once()
    assert flow["reason"] == "reconfigure_successful"
    assert entry.data["modbus_baudrate"] == 115200


@pytest.mark.asyncio
async def test_reconfigure_shared_probe_bounds_its_reads_then_defers(hass):
    """The shared connection's read timeout is longer than the standalone
    probe's, so the shared-bus probe bounds its own reads: a device that
    stops answering mid-reconfigure hits the bound and falls back to the
    standalone probe rather than hanging the form."""
    from homeassistant.data_entry_flow import FlowResultType

    entry = _modbus_entry(
        hass, modbus_family="halo", modbus_framer="socket", modbus_unit=1
    )
    hass.config_entries.async_update_entry(entry, unique_id="192.168.1.50:502:1")

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(5)

    conn = _seeded_connection()
    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection", return_value=conn
    ):
        flow = await _setup_and_start_reconfigure_tcp(hass, entry)

        with (
            patch("custom_components.hyxi_cloud.config_flow.DETECTION_TIMEOUT", 0.01),
            patch(
                "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow."
                "_detect_family_on_unit",
                side_effect=_hang,
            ),
            patch(
                "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow."
                "_probe_and_detect_modbus_tcp",
                return_value=(None, "halo", "socket"),
            ) as standalone,
            patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
        ):
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {"modbus_host": "192.168.1.50", "modbus_port": 502, "modbus_unit": 1},
            )
            await hass.async_block_till_done()

    # The bounded read gave up and the standalone probe took over.
    standalone.assert_called_once()
    assert flow["type"] is FlowResultType.ABORT
    assert flow["reason"] == "reconfigure_successful"


@pytest.mark.asyncio
async def test_setting_up_a_modbus_entry_creates_entities(hass):
    """The whole point: existing platforms light up over RS485 unchanged.

    This runs Home Assistant's real setup path -- coordinator, device
    registry, every entity platform -- against an in-memory device, so it
    exercises the code a user actually hits rather than the coordinator
    in isolation.
    """
    # This module's fixtures are HALO-shaped; family must be explicit rather
    # than left to the default (hybrid, since that's the stronger-evidenced
    # document) or this seeded connection would be read with the wrong map.
    entry = _modbus_entry(hass, modbus_family="halo")

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    # Platforms finish adding entities after setup returns.
    await hass.async_block_till_done()

    states = [
        state
        for state in hass.states.async_all()
        if state.entity_id.startswith(("sensor.", "binary_sensor."))
    ]
    assert states, "no entities were created from the Modbus device"

    by_id = {s.entity_id: s.state for s in states}
    sn = "10201234567810"
    # 0.811 kW, published at the sensor's declared display precision.
    assert by_id[f"sensor.hyxi_{sn}_gridp"] == "0.81"
    assert by_id[f"sensor.hyxi_{sn}_ph1v"] == "230.12"
    # Derived locally by the same helper the cloud path uses.
    assert by_id[f"sensor.hyxi_{sn}_grid_export"] == "811.0"
    assert by_id[f"sensor.hyxi_{sn}_home_load"] == "276.0"

    # Battery sensors are identified by the inverter serial (stable across
    # restarts -- see _migrate_battery_sensor_unique_ids) but still hang off
    # a sub-device keyed by the BMS serial, exactly as on the cloud path.
    # SOC is published as an integer -- batsoc is in INT_SENSOR_KEYS.
    assert by_id[f"sensor.hyxi_{sn}_batsoc"] == "78"
    assert by_id[f"sensor.hyxi_{sn}_batp"] == "-420.0"

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    bat_soc = entity_registry.async_get(f"sensor.hyxi_{sn}_batsoc")
    assert bat_soc.unique_id == f"hyxi_{sn}_batSoc"
    bat_device = device_registry.async_get(bat_soc.device_id)
    assert (DOMAIN, "4213571357") in bat_device.identifiers

    # last_seen is a cloud heartbeat timestamp Modbus never populates --
    # must not be created at all rather than sit frozen or unavailable.
    assert f"sensor.hyxi_{sn}_last_seen" not in by_id


@pytest.mark.asyncio
async def test_setup_releases_the_bus_when_the_device_never_answers(hass):
    """Failing setup must not leave the port or socket held."""
    from homeassistant.config_entries import ConfigEntryState

    connection = MockModbusConnection()
    connection.close = AsyncMock()
    connection.for_unit(1).fail_requests(OSError("nothing there"))

    entry = _modbus_entry(hass)

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=connection,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    connection.close.assert_awaited()


def test_mask_never_returns_the_raw_value():
    from custom_components.hyxi_cloud.modbus.client import _mask

    assert _mask(None) == "****"
    assert _mask("10201234567810") == "a3eb0d55"
    assert "10201234567810" not in _mask("10201234567810")


# --- HALO control entities: the fix that lets set_mode_* actually be
# reached, since a HALO has no phase 2/3 registers and was previously
# unreachable through button.py/number.py/protection.py's phase-based
# routing regardless of how well the client itself worked. -----------------


def _entity_id(hass, platform: str, sn: str, key: str) -> str | None:
    """Look up an entity by its unique_id, the same way _get_power_value
    does in production -- entity_id is slugified from the device name, not
    derived from the serial number, so guessing the string directly is
    fragile."""
    from homeassistant.helpers import entity_registry as er

    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"hyxi_{sn}_{key}")


@pytest.mark.asyncio
async def test_halo_control_entities_appear_when_control_is_enabled(hass):
    """HALO (micro_ess) gets the same four mode buttons a three-phase cloud
    device gets, plus the power numbers they read their wattage from --
    despite detect_phase_type never resolving past "unknown" for it, since
    the HALO document has no phase 2/3 registers at all to detect."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    for key in ("mode_idle", "mode_charge", "mode_discharge", "mode_self_consume"):
        assert _entity_id(hass, "button", sn, key) is not None, key

    # The mode buttons read their wattage from these; without them a charge
    # command would silently fall back to a hardcoded 100W.
    assert _entity_id(hass, "number", sn, "charge_power") is not None
    assert _entity_id(hass, "number", sn, "discharge_power") is not None

    # No local equivalent of the cloud's 5-state peak-shaving surface --
    # HALO must never get those buttons.
    assert _entity_id(hass, "button", sn, "peak_shaving_hold") is None


@pytest.mark.asyncio
async def test_halo_mode_button_press_calls_the_modbus_client(hass):
    """Pressing a mode button on a HALO entry must reach the real Modbus
    client's set_mode_* methods, not silently do nothing."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    coordinator = next(iter(hass.data[DOMAIN].values()))
    entity_id = _entity_id(hass, "button", sn, "mode_idle")
    assert entity_id is not None

    with patch.object(
        coordinator.client, "set_mode_idle", wraps=coordinator.client.set_mode_idle
    ) as spy:
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )
    spy.assert_awaited_once_with(sn)


@pytest.mark.asyncio
async def test_halo_protection_controller_starts(hass):
    """The automatic protection controller must actually start for a HALO
    entry -- __init__.py's setup previously required a recognized phase,
    which a HALO never has."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    coordinator = next(iter(hass.data[DOMAIN].values()))
    assert coordinator.protection_controllers, "no protection controller started"


@pytest.mark.asyncio
async def test_hybrid_control_entities_still_appear_unaffected(hass):
    """The three-phase hybrid path this already worked for must be
    unaffected by routing HALO differently."""
    from custom_components.hyxi_cloud.modbus.client_hybrid import (
        HYBRID_DEVICE_CODE,
    )

    entry = _modbus_entry(
        hass, modbus_family="hybrid", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_hybrid_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    assert HYBRID_DEVICE_CODE == "HYBRID_INVERTER"
    assert _entity_id(hass, "button", sn, "mode_idle") is not None
    assert _entity_id(hass, "number", sn, "charge_power") is not None


@pytest.mark.asyncio
async def test_halo_setting_numbers_appear_and_write_through(hass):
    """HaloSettings fields with an unambiguous numeric range and no overlap
    with the software-side protection numbers -- feed-in power limit, the
    VPP dispatch block's minimum SOC, and the five firmware SOC setpoints."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    coordinator = next(iter(hass.data[DOMAIN].values()))

    feed_in_id = _entity_id(hass, "number", sn, "feed_in_power_limit")
    soc_id = _entity_id(hass, "number", sn, "vpp_min_soc")
    assert feed_in_id is not None
    assert soc_id is not None
    for key in (
        "force_charge_start_soc",
        "force_charge_stop_soc",
        "off_grid_min_soc",
        "self_use_soc",
        "discharge_min_soc",
    ):
        assert _entity_id(hass, "number", sn, key) is not None, key

    # Hybrid-only settings must not appear on a HALO entry.
    assert _entity_id(hass, "number", sn, "feed_in_power") is None
    assert _entity_id(hass, "number", sn, "max_charge_current") is None
    assert _entity_id(hass, "number", sn, "backup_soc") is None

    with patch.object(
        coordinator.client,
        "set_feed_in_power_limit",
        wraps=coordinator.client.set_feed_in_power_limit,
    ) as spy:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": feed_in_id, "value": 3500},
            blocking=True,
        )
    spy.assert_awaited_once_with(3500)

    with patch.object(
        coordinator.client, "set_vpp_min_soc", wraps=coordinator.client.set_vpp_min_soc
    ) as spy:
        await hass.services.async_call(
            "number", "set_value", {"entity_id": soc_id, "value": 15}, blocking=True
        )
    spy.assert_awaited_once_with(15)

    self_use_id = _entity_id(hass, "number", sn, "self_use_soc")
    with patch.object(
        coordinator.client,
        "set_self_use_soc",
        wraps=coordinator.client.set_self_use_soc,
    ) as spy:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": self_use_id, "value": 20},
            blocking=True,
        )
    spy.assert_awaited_once_with(20)


@pytest.mark.asyncio
async def test_hybrid_setting_numbers_appear_and_write_through(hass):
    """feed-in power limit, the two current caps and the five firmware SOC
    setpoints -- the HybridSettings equivalent of the HALO test above."""
    entry = _modbus_entry(
        hass, modbus_family="hybrid", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_hybrid_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    coordinator = next(iter(hass.data[DOMAIN].values()))

    feed_in_id = _entity_id(hass, "number", sn, "feed_in_power")
    max_charge_id = _entity_id(hass, "number", sn, "max_charge_current")
    max_discharge_id = _entity_id(hass, "number", sn, "max_discharge_current")
    assert feed_in_id is not None
    assert max_charge_id is not None
    assert max_discharge_id is not None
    for key in (
        "self_use_soc",
        "backup_soc",
        "forced_charge_soc",
        "feed_in_soc",
        "off_grid_soc",
    ):
        assert _entity_id(hass, "number", sn, key) is not None, key

    # HALO-only settings must not appear on a hybrid entry.
    assert _entity_id(hass, "number", sn, "feed_in_power_limit") is None
    assert _entity_id(hass, "number", sn, "vpp_min_soc") is None
    assert _entity_id(hass, "number", sn, "off_grid_min_soc") is None

    with patch.object(
        coordinator.client,
        "set_max_charge_current",
        wraps=coordinator.client.set_max_charge_current,
    ) as spy:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_charge_id, "value": 32.5},
            blocking=True,
        )
    spy.assert_awaited_once_with(32.5)

    backup_soc_id = _entity_id(hass, "number", sn, "backup_soc")
    with patch.object(
        coordinator.client, "set_backup_soc", wraps=coordinator.client.set_backup_soc
    ) as spy:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": backup_soc_id, "value": 25},
            blocking=True,
        )
    spy.assert_awaited_once_with(25)


@pytest.mark.asyncio
async def test_hybrid_power_command_buttons_appear_and_write_through(hass):
    """power_command has no cloud equivalent and no HALO register -- these
    three buttons must exist only for a hybrid Modbus entry."""
    entry = _modbus_entry(
        hass, modbus_family="hybrid", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_hybrid_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    coordinator = next(iter(hass.data[DOMAIN].values()))

    for key in ("power_on", "power_off", "restart"):
        assert _entity_id(hass, "button", sn, key) is not None, key

    restart_id = _entity_id(hass, "button", sn, "restart")
    with patch.object(
        coordinator.client, "restart", wraps=coordinator.client.restart
    ) as spy:
        await hass.services.async_call(
            "button", "press", {"entity_id": restart_id}, blocking=True
        )
    spy.assert_awaited_once_with(sn)


@pytest.mark.asyncio
async def test_halo_has_no_power_command_buttons(hass):
    """HALO has no power_command register -- see HaloSettings' docstring."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    for key in ("power_on", "power_off", "restart"):
        assert _entity_id(hass, "button", sn, key) is None, key


@pytest.mark.asyncio
async def test_halo_anti_starvation_switch_appears_and_writes_through(hass):
    """The one boolean HaloSettings field, calling set_anti_starvation --
    the straightforward-polarity client method."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    coordinator = next(iter(hass.data[DOMAIN].values()))
    entity_id = _entity_id(hass, "switch", sn, "anti_starvation")
    assert entity_id is not None

    with patch.object(
        coordinator.client,
        "set_anti_starvation",
        wraps=coordinator.client.set_anti_starvation,
    ) as spy:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )
    spy.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_halo_anti_starvation_switch_shows_the_devices_real_value(hass):
    """anti_starvation_enabled is resolved from the same settings block the
    setting numbers already seed from -- the switch should show the
    device's real value from the first poll, not stay unknown until this
    session writes it, and should pick up a change made outside HA once
    the settings refresh window reopens."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )
    connection = _seeded_connection()

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=connection,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    coordinator = next(iter(hass.data[DOMAIN].values()))
    entity_id = _entity_id(hass, "switch", sn, "anti_starvation")
    self_use_id = _entity_id(hass, "number", sn, "self_use_soc")

    # HOLDING_REGISTERS seeds 4121 = 1 (enabled) and 4134 = 10.
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get(self_use_id).state == "10"

    # Simulate a change made outside HA (the app, another Modbus master),
    # and force the refresh window open so the next poll notices it.
    connection.for_unit(1).load_raw({"holding": {4121: 0, 4134: 55}})
    coordinator.client._settings_read_at -= SETTINGS_REFRESH_SECONDS + 1

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "off"
    assert hass.states.get(self_use_id).state == "55"


@pytest.mark.asyncio
async def test_halo_dispatch_switch_reflects_and_writes_the_vpp_enable(hass):
    """Seeded 4146 = 0, so the switch shows "off"; toggling it writes 4146
    through the client, and a change made outside HA is picked up on the
    next settings refresh."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )
    connection = _seeded_connection()

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=connection,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    coordinator = next(iter(hass.data[DOMAIN].values()))
    entity_id = _entity_id(hass, "switch", sn, "dispatch")
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "off"  # seed 4146 = 0

    with patch.object(
        coordinator.client,
        "set_dispatch_enabled",
        wraps=coordinator.client.set_dispatch_enabled,
    ) as spy:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )
    spy.assert_awaited_once_with(True)
    await coordinator.client.settings.async_update()
    assert coordinator.client.settings.vpp_enable == 1

    # A change made outside HA is adopted once the refresh window reopens.
    connection.for_unit(1).load_raw({"holding": {4146: 0}})
    coordinator.client._settings_read_at -= SETTINGS_REFRESH_SECONDS + 1
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"


@pytest.mark.asyncio
async def test_unloading_a_modbus_entry_releases_dispatch(hass):
    """Turning off Device Control (a reload) or deleting the entry must hand
    the battery back -- otherwise a HALO left mid-discharge stays there with
    no entity to stop it. The bus is still open here: HA fires the
    connection-close callback only after async_unload_entry returns."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    coordinator = next(iter(hass.data[DOMAIN].values()))
    assert coordinator.protection_controllers  # control is active this run

    with patch.object(
        coordinator.client,
        "set_dispatch_enabled",
        wraps=coordinator.client.set_dispatch_enabled,
    ) as spy:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    # Released during unload (the register write itself is covered by
    # test_set_dispatch_enabled_toggles_the_vpp_enable_register; the bus is
    # closed by the time control returns here).
    spy.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_refresh_settings_button_forces_an_immediate_settings_read(hass):
    """Pressing the button must both reset the client's refresh throttle
    and request an immediate poll -- proving the two halves actually work
    together, not just that force_settings_refresh() exists. Unlike
    test_halo_anti_starvation_switch_shows_the_devices_real_value above,
    nothing here manually opens the refresh window first."""
    entry = _modbus_entry(
        hass, modbus_family="halo", options={"enable_battery_control": True}
    )
    connection = _seeded_connection()

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=connection,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    self_use_id = _entity_id(hass, "number", sn, "self_use_soc")
    assert hass.states.get(self_use_id).state == "10"

    button_id = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_refresh_settings"
    )
    assert button_id is not None

    # Simulate a change made outside HA -- without pressing the button,
    # this wouldn't show up until the hourly refresh window reopens.
    connection.for_unit(1).load_raw({"holding": {4134: 55}})

    await hass.services.async_call(
        "button", "press", {"entity_id": button_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert hass.states.get(self_use_id).state == "55"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "connection_factory", "fail_register"),
    [
        ("halo", _seeded_connection, 4134),
        ("hybrid", _seeded_hybrid_connection, 1102),
    ],
    ids=["halo", "hybrid"],
)
async def test_a_write_survives_a_failed_settings_reread_past_the_refresh_window(
    hass, family, connection_factory, fail_register
):
    """A write's optimistic value must outlive a settings re-read that
    started after it but failed -- Component.write() never updates the
    client's own cached settings fields, only the device, so a failed
    async_update() leaves self_use_soc at its last successfully-read value
    (10, from the fixture) even though the device now holds 20. Without
    _settings_confirmed_at staying put on a failed attempt, that stale 10
    would be re-published with a timestamp newer than the write and
    SettingsSyncMixin would wrongly adopt it, reverting the entity. Checked
    on both device families -- self_use_soc is register 4134 on HALO, 1102
    on hybrid."""
    entry = _modbus_entry(
        hass, modbus_family=family, options={"enable_battery_control": True}
    )
    connection = connection_factory()

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=connection,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    coordinator = next(iter(hass.data[DOMAIN].values()))
    self_use_id = _entity_id(hass, "number", sn, "self_use_soc")
    assert hass.states.get(self_use_id).state == "10"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": self_use_id, "value": 20}, blocking=True
    )
    assert hass.states.get(self_use_id).state == "20.0"

    # Force the refresh window open, then make the re-read fail -- the
    # device's real value (20) is never in question here, only whether a
    # failed re-attempt can make this session's own view of it regress.
    coordinator.client._settings_read_at -= SETTINGS_REFRESH_SECONDS + 1
    coordinator.client._unit.fail_read(
        fail_register, IllegalDataAddressError(2, "nope"), register_type="holding"
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(self_use_id).state == "20.0"


@pytest.mark.asyncio
async def test_hybrid_anti_starvation_switch_appears_and_writes_through(hass):
    """The HybridSettings equivalent, calling the inverted-polarity client
    method -- proving the right method got wired to the right family, not
    just that some method got called."""
    entry = _modbus_entry(
        hass, modbus_family="hybrid", options={"enable_battery_control": True}
    )

    with patch(
        "homeassistant.components.modbus.connection.ModbusConnection",
        return_value=_seeded_hybrid_connection(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_block_till_done()

    sn = "10201234567810"
    coordinator = next(iter(hass.data[DOMAIN].values()))
    entity_id = _entity_id(hass, "switch", sn, "anti_starvation")
    assert entity_id is not None

    with patch.object(
        coordinator.client,
        "set_anti_starvation_protection",
        wraps=coordinator.client.set_anti_starvation_protection,
    ) as spy:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": entity_id}, blocking=True
        )
    spy.assert_awaited_once_with(False)

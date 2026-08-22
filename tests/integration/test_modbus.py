"""Tests for the local Modbus transport.

These run against ``modbus_connection``'s own mock unit rather than
hand-rolled doubles, so the real field descriptors, block planner and
decoders are exercised. A hand-mocked unit would happily return whatever the
test wanted and prove nothing about whether registers.py addresses and scales
the device correctly.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from modbus_connection import IllegalDataAddressError
from modbus_connection.pytest_plugin import MockModbusConnection

from custom_components.hyxi_cloud.modbus.client import HyxiModbusClient
from custom_components.hyxi_cloud.modbus.registers import HaloBattery, HaloGrid


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
    4106: 3801,  # bus voltage, 1dp -> 380.1 V
    4109: 214,
    4110: 305,  # AC-side temperature, 1dp -> 30.5 C
    4111: 288,
    4123: 1,
    4151: 5001,  # grid frequency, 2dp -> 50.01 Hz
    **_spread(4152, 811),  # grid active power, 3dp kW
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
    5000: 0,
    5001: 0,
    5002: 0,
    5020: 100,
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
    return HyxiModbusClient(connection, 1)


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
async def test_a_failed_write_raises_the_class_the_platforms_catch(client):
    """button.py and friends catch HyxiApiClient.ControlError by name."""
    with patch.object(client.settings, "write", side_effect=OSError("bus fell over")):
        with pytest.raises(HyxiModbusClient.ControlError):
            await client.set_mode_charge("SN", 1000)

        with pytest.raises(HyxiModbusClient.ControlError):
            await client.set_peak_shaving("SN", "on")


@pytest.mark.asyncio
async def test_close_releases_the_connection():
    connection = MagicMock()
    connection.for_unit = MagicMock(return_value=MagicMock())
    connection.close = AsyncMock()

    await HyxiModbusClient(connection, 1).async_close()

    connection.close.assert_awaited_once()


def test_register_model_declares_the_right_spaces():
    assert HaloGrid.register_space == "input"
    assert HaloBattery.register_space == "input"
    # The document caps a request at 100 registers.
    assert HaloGrid.max_span <= 100


# --- Coordinator and setup wiring -----------------------------------------


def _modbus_entry(hass, **overrides):
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
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={})
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
    from custom_components.hyxi_cloud import _async_build_modbus_coordinator

    entry = _modbus_entry(hass, **overrides)

    with patch("modbus_connection.tmodbus.ModbusConnection") as connection_class:
        coordinator = await _async_build_modbus_coordinator(hass, entry)

    params = connection_class.call_args.args[0]
    assert type(params).__name__ == expected_params
    assert coordinator.client is not None


@pytest.mark.asyncio
async def test_unload_releases_the_bus(hass):
    """A reload that leaves the port held cannot open it again."""
    from custom_components.hyxi_cloud import async_unload_entry
    from custom_components.hyxi_cloud.const import DOMAIN

    entry = _modbus_entry(hass)
    coordinator = MagicMock()
    coordinator.engine = None
    coordinator.protection_controllers = {}
    coordinator.client.async_close = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
        assert await async_unload_entry(hass, entry) is True

    coordinator.client.async_close.assert_awaited_once()


def _seeded_connection() -> MockModbusConnection:
    """A connection serving the document's register file."""
    connection = MockModbusConnection()
    connection.for_unit(1).load_raw(
        {"input": _fill(INPUT_REGISTERS), "holding": _fill(HOLDING_REGISTERS)}
    )
    return connection


@pytest.mark.asyncio
async def test_setting_up_a_modbus_entry_creates_entities(hass):
    """The whole point: existing platforms light up over RS485 unchanged.

    This runs Home Assistant's real setup path -- coordinator, device
    registry, every entity platform -- against an in-memory device, so it
    exercises the code a user actually hits rather than the coordinator
    in isolation.
    """
    entry = _modbus_entry(hass)

    with patch(
        "modbus_connection.tmodbus.ModbusConnection",
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

    # Battery sensors hang off a sub-device keyed by the BMS serial, exactly
    # as they do on the cloud path -- confirmation that reusing the metric
    # vocabulary reuses the device layout too.
    # SOC is published as an integer -- batsoc is in INT_SENSOR_KEYS.
    assert by_id["sensor.hyxi_4213571357_batsoc"] == "78"
    assert by_id["sensor.hyxi_4213571357_batp"] == "-420.0"


@pytest.mark.asyncio
async def test_setup_releases_the_bus_when_the_device_never_answers(hass):
    """Failing setup must not leave the port or socket held."""
    from homeassistant.config_entries import ConfigEntryState

    connection = MockModbusConnection()
    connection.close = AsyncMock()
    connection.for_unit(1).fail_requests(OSError("nothing there"))

    entry = _modbus_entry(hass)

    with patch("modbus_connection.tmodbus.ModbusConnection", return_value=connection):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    connection.close.assert_awaited()


def test_mask_never_returns_the_raw_value():
    from custom_components.hyxi_cloud.modbus.client import _mask

    assert _mask(None) == "****"
    assert _mask("10201234567810") == "a3eb0d55"
    assert "10201234567810" not in _mask("10201234567810")

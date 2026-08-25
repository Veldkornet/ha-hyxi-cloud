"""Integration tests for the HYXI Cloud integration using pytest-homeassistant-custom-component."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyxi_cloud.const import (
    CONF_ACCESS_KEY,
    CONF_MODBUS_BAUDRATE,
    CONF_MODBUS_DEVICE,
    CONF_MODBUS_FAMILY,
    CONF_MODBUS_FRAMER,
    CONF_MODBUS_HOST,
    CONF_MODBUS_PORT,
    CONF_MODBUS_TYPE,
    CONF_MODBUS_UNIT,
    CONF_SECRET_KEY,
    CONF_TRANSPORT,
    DOMAIN,
    MODBUS_TYPE_SERIAL,
    MODBUS_TYPE_TCP,
    TRANSPORT_CLOUD,
    TRANSPORT_MODBUS,
)


@pytest.mark.asyncio
async def test_config_flow_success(hass: HomeAssistant):
    """Test standard successful config flow."""
    # 1. Initialize user step
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    # The first step chooses a transport; cloud leads to the credentials form.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRANSPORT: TRANSPORT_CLOUD}
    )
    assert result["step_id"] == "cloud"

    # 2. Configure with mock credentials and mock API client
    with patch(
        "custom_components.hyxi_cloud.config_flow.HyxiApiClient"
    ) as mock_client_class:
        mock_client = AsyncMock()
        mock_client._refresh_token.return_value = True
        mock_client.get_all_device_data.return_value = {
            "data": {"SOME_SN": {}},
            "attempts": 1,
        }
        mock_client_class.return_value = mock_client

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ACCESS_KEY: "test_access_key",
                CONF_SECRET_KEY: "test_secret_key",
            },
        )
        await hass.async_block_till_done()

        # 3. Assert config entry is created successfully
        assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result2["title"] == "HYXI Cloud"
        assert result2["data"] == {
            CONF_ACCESS_KEY: "test_access_key",
            CONF_SECRET_KEY: "test_secret_key",
            "region": "eu",
            "base_url": "https://open.hyxicloud.com",
            CONF_TRANSPORT: TRANSPORT_CLOUD,
        }


@pytest.mark.asyncio
async def test_config_flow_invalid_auth(hass: HomeAssistant):
    """Test config flow failure due to invalid authentication."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    # The first step chooses a transport; cloud leads to the credentials form.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRANSPORT: TRANSPORT_CLOUD}
    )

    with patch(
        "custom_components.hyxi_cloud.config_flow.HyxiApiClient"
    ) as mock_client_class:
        mock_client = AsyncMock()
        mock_client._refresh_token.return_value = False
        mock_client_class.return_value = mock_client

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ACCESS_KEY: "test_access_key",
                CONF_SECRET_KEY: "test_secret_key",
            },
        )
        await hass.async_block_till_done()

        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_setup_entry_and_sensors(hass: HomeAssistant):
    """Test full setup of config entry and verified entity registration."""
    # 1. Create mock config entry
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ACCESS_KEY: "test_access_key",
            CONF_SECRET_KEY: "test_secret_key",
        },
        options={
            "update_interval": 30,
        },
        unique_id="test_access_key",
    )
    entry.add_to_hass(hass)

    # 2. Patch the client used by the coordinator and config flow
    mock_data = {
        "TEST_SN_123": {
            "device_name": "Bonenakker Inverter",
            "model": "HYX-H10K-HT",
            "sw_version": "v1.2.3",
            "hw_version": "V1",
            "device_type": 1,
            "metrics": {
                "tinv": "45",
                "totalE": "100.5",
            },
        }
    }

    with patch("custom_components.hyxi_cloud.HyxiApiClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client._refresh_token.return_value = True
        mock_client.get_all_device_data.return_value = {
            "data": mock_data,
            "attempts": 1,
        }
        mock_client_class.return_value = mock_client

        # 3. Setup config entry
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Verify entry is loaded
        assert entry.state == ConfigEntryState.LOADED

        # 4. Check that entities are registered and have the correct states in the HA state machine
        state_tinv = hass.states.get("sensor.hyxi_test_sn_123_tinv")
        assert state_tinv is not None
        assert state_tinv.state == "45.0"

        state_total_e = hass.states.get("sensor.hyxi_test_sn_123_totale")
        assert state_total_e is not None
        assert state_total_e.state == "100.5"

        # 5. Unload entry
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.NOT_LOADED


@pytest.mark.asyncio
async def test_em_parameter_number_survives_platform_poll(hass: HomeAssistant):
    """Regression test: EMParameterNumber values written by the real
    engine._set_param() (e.g. the adaptive avg_night_consumption estimate)
    must survive Home Assistant's normal per-platform poll cycle AND a
    manually forced entity update.

    EMParameterNumber has nothing to poll (no device, no coordinator), but
    without an explicit should_poll=False it inherits Entity's default of
    True -- and even with should_poll=False, a manually forced update
    (e.g. the homeassistant.update_entity service, exercised here via the
    same _async_update_entity_states path with force_refresh=True) would
    still re-derive state from the entity's own in-memory
    _attr_native_value and revert the write, unless _set_param() also
    keeps that in-memory value in sync via the entity's
    set_computed_value(). This drives the real _set_param() call and the
    real poll handler, not mocks of either.
    """
    from custom_components.hyxi_cloud.const import (
        CONF_EM_ENABLED,
        CONF_EM_INVERTER_SN,
        CONF_EM_P1_ENTITY,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCESS_KEY: "test_access_key", CONF_SECRET_KEY: "test_secret_key"},
        options={
            "update_interval": 30,
            "enable_battery_control": True,
            CONF_EM_ENABLED: True,
            CONF_EM_INVERTER_SN: "SN123",
            CONF_EM_P1_ENTITY: "sensor.p1_meter",
        },
        unique_id="test_access_key",
    )
    entry.add_to_hass(hass)

    mock_data = {
        "SN123": {
            "device_name": "Test Inverter",
            "model": "HYX-H10K-HT",
            "device_type": 1,
            "metrics": {"batSoc": "50"},
        }
    }

    with patch("custom_components.hyxi_cloud.HyxiApiClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client._refresh_token.return_value = True
        mock_client.get_all_device_data.return_value = {
            "data": mock_data,
            "attempts": 1,
        }
        mock_client_class.return_value = mock_client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED

        entity_id = "number.energy_manager_avg_night_consumption"
        state = hass.states.get(entity_id)
        assert state is not None

        # Call the real engine._set_param(), exactly as
        # _update_night_estimate does for its adaptive EMA.
        coordinator = hass.data[DOMAIN][entry.entry_id]
        engine = coordinator.engine
        assert engine is not None
        engine._set_param("avg_night_consumption", 999.0)  # pylint: disable=protected-access
        assert hass.states.get(entity_id).state == "999.0"

        number_platform = next(
            p for p in hass.data["entity_platform"][DOMAIN] if p.domain == "number"
        )
        entity = number_platform.entities[entity_id]
        assert entity.should_poll is False
        # The entity's own in-memory value must be in sync too, not just
        # the state machine -- otherwise a forced update re-derives state
        # from this and reverts the write regardless of should_poll.
        assert entity.native_value == 999.0

        # Drive the actual per-platform poll handler that HA's
        # scan-interval timer calls automatically in production.
        await number_platform._async_update_entity_states()  # pylint: disable=protected-access
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "999.0"

        # A manually forced update (force_refresh=True, e.g. the
        # homeassistant.update_entity service) bypasses should_poll
        # entirely -- must still not revert the value.
        await entity.async_update_ha_state(force_refresh=True)
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "999.0"


@pytest.mark.asyncio
async def test_config_flow_no_devices(hass: HomeAssistant):
    """Test config flow failure when no plants or devices are found."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    # The first step chooses a transport; cloud leads to the credentials form.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRANSPORT: TRANSPORT_CLOUD}
    )

    with patch(
        "custom_components.hyxi_cloud.config_flow.HyxiApiClient"
    ) as mock_client_class:
        mock_client = AsyncMock()
        mock_client._refresh_token.return_value = True
        mock_client.get_all_device_data.return_value = {"data": {}, "attempts": 1}
        mock_client_class.return_value = mock_client

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ACCESS_KEY: "test_access_key",
                CONF_SECRET_KEY: "test_secret_key",
            },
        )
        await hass.async_block_till_done()

        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"] == {"base": "no_devices"}


@pytest.mark.asyncio
async def test_enum_sensor_survives_out_of_range_api_value(hass: HomeAssistant):
    """Regression test for the out-of-range-enum guard in sensor.py.

    Drives a real coordinator refresh through the entity's actual `.state`
    property (via `hass.states`), not a hand-rolled check of
    `_attr_native_value`, since that's the only path that goes through HA's
    options validation and would have caught the original bug.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCESS_KEY: "test_access_key", CONF_SECRET_KEY: "test_secret_key"},
        options={"update_interval": 30},
        unique_id="test_access_key",
    )
    entry.add_to_hass(hass)

    def make_data(inv_sts: str) -> dict:
        return {
            "SN123": {
                "device_name": "Test Inverter",
                "model": "HYX-H10K-HT",
                "device_type": 1,
                "metrics": {"invSts": inv_sts, "totalE": "100.5"},
            }
        }

    with patch("custom_components.hyxi_cloud.HyxiApiClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client._refresh_token.return_value = True
        mock_client.get_all_device_data.return_value = {
            "data": make_data("2"),
            "attempts": 1,
        }
        # compute_derived_metrics is synchronous on the real client (only
        # invoked by the coordinator's merge path from the second refresh
        # onward); a bare AsyncMock() mocks it as async too, which returns
        # an unawaited coroutine that then blows up as "not iterable" --
        # unrelated to what this test is regression-testing.
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        mock_client_class.return_value = mock_client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED

        entity_id = "sensor.hyxi_sn123_invsts"
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "2"

        # The API reports a status code outside the hardcoded options list --
        # e.g. new firmware. Unfixed, this doesn't crash the test process
        # (HA's coordinator catches it per-listener) but the sensor freezes
        # at its last valid state and HA logs a full traceback every single
        # refresh; the sibling sensor on the same device must keep working.
        mock_client.get_all_device_data.return_value = {
            "data": make_data("9"),
            "attempts": 1,
        }
        await hass.data[DOMAIN][entry.entry_id].async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "unknown"

        total_e_state = hass.states.get("sensor.hyxi_sn123_totale")
        assert total_e_state is not None
        assert total_e_state.state == "100.5"


@pytest.mark.asyncio
async def test_config_flow_modbus_tcp_through_real_schemas(hass: HomeAssistant):
    """Walk the local Modbus branch through Home Assistant's real flow engine.

    The mocked config-flow tests stub voluptuous out entirely, so they cannot
    tell whether the selectors actually validate the values the form submits.
    This goes through the real schemas end to end.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRANSPORT: TRANSPORT_MODBUS}
    )
    assert result["step_id"] == "modbus"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODBUS_TYPE: MODBUS_TYPE_TCP}
    )
    assert result["step_id"] == "modbus_tcp"

    # async_setup_entry is stubbed as well as the probe: creating the entry
    # makes Home Assistant set it up, which would open a real socket to the
    # gateway. This test is about the flow, not the transport.
    with (
        # _tcp_reachable does a real asyncio.open_connection, which the test
        # harness blocks outright (no sockets/DNS in tests) -- stubbed
        # reachable so this test exercises the flow, not connectivity.
        patch(
            "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._tcp_reachable",
            return_value=True,
        ),
        patch(
            "custom_components.hyxi_cloud.config_flow."
            "HyxiConfigFlow._probe_and_detect_modbus",
            return_value=(None, "halo"),
        ),
        patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MODBUS_HOST: "192.168.1.50",
                CONF_MODBUS_PORT: 502,
                CONF_MODBUS_UNIT: 1,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "HYXI Modbus (192.168.1.50)"
    assert result["data"] == {
        CONF_TRANSPORT: TRANSPORT_MODBUS,
        CONF_MODBUS_TYPE: MODBUS_TYPE_TCP,
        CONF_MODBUS_HOST: "192.168.1.50",
        CONF_MODBUS_PORT: 502,
        CONF_MODBUS_UNIT: 1,
        CONF_MODBUS_FAMILY: "halo",
        # _probe_and_detect_modbus is patched to succeed unconditionally, so
        # the wire-framing probe accepts the first framer it tries.
        CONF_MODBUS_FRAMER: "socket",
    }


@pytest.mark.asyncio
async def test_config_flow_modbus_probe_failure_shows_error(hass: HomeAssistant):
    """A bus with nothing on it must return to the form, not create an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TRANSPORT: TRANSPORT_MODBUS}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODBUS_TYPE: MODBUS_TYPE_TCP}
    )

    with (
        patch(
            "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._tcp_reachable",
            return_value=True,
        ),
        patch(
            "custom_components.hyxi_cloud.config_flow."
            "HyxiConfigFlow._probe_and_detect_modbus",
            return_value=("no_device", "hybrid"),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MODBUS_HOST: "192.168.1.50",
                CONF_MODBUS_PORT: 502,
                CONF_MODBUS_UNIT: 1,
            },
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "no_device"}


# --- Reconfigure: editing an existing Modbus entry's connection details ----


def _existing_modbus_entry(hass: HomeAssistant, **overrides) -> MockConfigEntry:
    """A Modbus TCP entry already set up, as reconfigure always starts from one."""
    data = {
        CONF_TRANSPORT: TRANSPORT_MODBUS,
        CONF_MODBUS_TYPE: MODBUS_TYPE_TCP,
        CONF_MODBUS_HOST: "192.168.1.50",
        CONF_MODBUS_PORT: 502,
        CONF_MODBUS_UNIT: 1,
        CONF_MODBUS_FAMILY: "halo",
    }
    data.update(overrides)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        unique_id="192.168.1.50:502:1",
        title="HYXI Modbus (192.168.1.50)",
    )
    entry.add_to_hass(hass)
    return entry


async def _start_reconfigure(hass: HomeAssistant, entry: MockConfigEntry):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )


@pytest.mark.asyncio
async def test_reconfigure_prefills_the_current_connection_type(hass: HomeAssistant):
    entry = _existing_modbus_entry(hass)

    result = await _start_reconfigure(hass, entry)

    assert result["step_id"] == "reconfigure"
    schema = result["data_schema"].schema
    (field,) = schema
    assert field.default() == MODBUS_TYPE_TCP


@pytest.mark.asyncio
async def test_reconfigure_prefills_the_current_host_and_port(hass: HomeAssistant):
    entry = _existing_modbus_entry(hass)

    result = await _start_reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODBUS_TYPE: MODBUS_TYPE_TCP}
    )

    assert result["step_id"] == "reconfigure_tcp"
    defaults = {f: f.default() for f in result["data_schema"].schema if f.default}
    assert defaults[CONF_MODBUS_HOST] == "192.168.1.50"
    assert defaults[CONF_MODBUS_PORT] == 502


@pytest.mark.asyncio
async def test_reconfigure_updates_the_entry_and_reloads(hass: HomeAssistant):
    """The core promise: fixing a wrong IP is an edit, not a remove-and-re-add,
    and the stored family is refreshed from a real re-probe, not carried
    over blindly from before the change."""
    entry = _existing_modbus_entry(hass)

    result = await _start_reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODBUS_TYPE: MODBUS_TYPE_TCP}
    )

    with (
        patch(
            "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._tcp_reachable",
            return_value=True,
        ),
        patch(
            "custom_components.hyxi_cloud.config_flow."
            "HyxiConfigFlow._probe_and_detect_modbus",
            return_value=(None, "hybrid"),
        ),
        patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MODBUS_HOST: "192.168.1.51",
                CONF_MODBUS_PORT: 502,
                CONF_MODBUS_UNIT: 1,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_MODBUS_HOST] == "192.168.1.51"
    # Re-detected from the new address, not left as the pre-edit "halo".
    assert entry.data[CONF_MODBUS_FAMILY] == "hybrid"
    assert entry.title == "HYXI Modbus (192.168.1.51)"
    # The entry's real identity, not just its data -- left stale here, the
    # old address (192.168.1.50) stays permanently reserved and the new
    # one was only ever checked for collisions, never actually persisted.
    assert entry.unique_id == "192.168.1.51:502:1"


@pytest.mark.asyncio
async def test_reconfigure_can_switch_from_tcp_to_serial(hass: HomeAssistant):
    """Swapping a network gateway for a USB adapter is a connection detail,
    not a transport change -- offered here rather than forcing a remove-and-re-add."""
    entry = _existing_modbus_entry(hass)

    result = await _start_reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODBUS_TYPE: MODBUS_TYPE_SERIAL}
    )
    assert result["step_id"] == "reconfigure_serial"

    with (
        patch(
            "custom_components.hyxi_cloud.config_flow."
            "HyxiConfigFlow._probe_and_detect_modbus",
            return_value=(None, "halo"),
        ),
        patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
    ):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MODBUS_DEVICE: "/dev/ttyUSB0",
                CONF_MODBUS_BAUDRATE: "115200",
                CONF_MODBUS_UNIT: 1,
            },
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_MODBUS_TYPE] == MODBUS_TYPE_SERIAL
    assert entry.data[CONF_MODBUS_DEVICE] == "/dev/ttyUSB0"
    assert CONF_MODBUS_HOST not in entry.data


@pytest.mark.asyncio
async def test_reconfigure_reprobe_failure_redisplays_the_form(hass: HomeAssistant):
    entry = _existing_modbus_entry(hass)

    result = await _start_reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODBUS_TYPE: MODBUS_TYPE_TCP}
    )

    with (
        patch(
            "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._tcp_reachable",
            return_value=True,
        ),
        patch(
            "custom_components.hyxi_cloud.config_flow."
            "HyxiConfigFlow._probe_and_detect_modbus",
            return_value=("no_device", "halo"),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MODBUS_HOST: "192.168.1.99",
                CONF_MODBUS_PORT: 502,
                CONF_MODBUS_UNIT: 1,
            },
        )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "no_device"}
    # Nothing committed -- the original entry is untouched.
    assert entry.data[CONF_MODBUS_HOST] == "192.168.1.50"


@pytest.mark.asyncio
async def test_reconfigure_with_the_address_unchanged_does_not_abort_on_itself(
    hass: HomeAssistant,
):
    """The specific bug this design avoids: an edit that leaves the address
    as-is must not read as 'already configured' against the entry's own
    existing unique ID."""
    entry = _existing_modbus_entry(hass)

    result = await _start_reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODBUS_TYPE: MODBUS_TYPE_TCP}
    )

    with (
        patch(
            "custom_components.hyxi_cloud.config_flow.HyxiConfigFlow._tcp_reachable",
            return_value=True,
        ),
        patch(
            "custom_components.hyxi_cloud.config_flow."
            "HyxiConfigFlow._probe_and_detect_modbus",
            return_value=(None, "halo"),
        ),
        patch("custom_components.hyxi_cloud.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                # Same host/port/unit as the entry already has -- only the
                # slave address is nudged to prove the rest round-trips.
                CONF_MODBUS_HOST: "192.168.1.50",
                CONF_MODBUS_PORT: 502,
                CONF_MODBUS_UNIT: 1,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


@pytest.mark.asyncio
async def test_reconfigure_aborts_when_the_new_address_belongs_to_another_entry(
    hass: HomeAssistant,
):
    """Reconfiguring one entry onto an address a *different* entry already
    owns is a real collision and must still abort."""
    entry = _existing_modbus_entry(hass)
    _existing_modbus_entry(
        hass,
        modbus_host="192.168.1.60",
    )
    # The second entry's unique_id must actually differ for this to test
    # anything -- MockConfigEntry doesn't derive it from data automatically.
    other = hass.config_entries.async_entries(DOMAIN)[1]
    hass.config_entries.async_update_entry(other, unique_id="192.168.1.60:502:1")

    result = await _start_reconfigure(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODBUS_TYPE: MODBUS_TYPE_TCP}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_MODBUS_HOST: "192.168.1.60",
            CONF_MODBUS_PORT: 502,
            CONF_MODBUS_UNIT: 1,
        },
    )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"

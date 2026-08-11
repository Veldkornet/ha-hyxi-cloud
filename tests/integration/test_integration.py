"""Integration tests for the HYXI Cloud integration using pytest-homeassistant-custom-component."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyxi_cloud.const import (
    CONF_ACCESS_KEY,
    CONF_SECRET_KEY,
    DOMAIN,
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
        }


@pytest.mark.asyncio
async def test_config_flow_invalid_auth(hass: HomeAssistant):
    """Test config flow failure due to invalid authentication."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
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

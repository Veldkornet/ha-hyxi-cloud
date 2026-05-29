"""Integration tests for the HYXI Cloud integration using pytest-homeassistant-custom-component."""

from unittest.mock import AsyncMock, patch

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

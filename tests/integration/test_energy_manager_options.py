"""Integration tests for the Energy Manager's options."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.hyxi_cloud.const import (
    CONF_EM_DRY_RUN,
    CONF_EM_EXTRA_PV_ENTITIES,
    CONF_EM_FORECAST_POWER_ENTITY,
    CONF_EM_INVERTER_SN,
    CONF_EM_P1_ENTITY,
)
from tests.integration.entries import cloud_entry, serve_devices

HYBRID = {
    "device_name": "Inverter",
    "model": "HYX-H10K-HT",
    "device_type_code": "HYBRID_INVERTER",
    "metrics": {"batSoc": "50"},
}


@pytest.fixture
def client():
    """The mocked HyxiApiClient the entry gets."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        mock_client.list_subscriptions.return_value = []
        client_class.return_value = mock_client
        yield mock_client


async def _open_energy_manager(hass: HomeAssistant, entry) -> dict:
    """Open the options and turn the Energy Manager on, returning its form."""
    form = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        form["flow_id"], form["data_schema"]({"enable_energy_manager": True})
    )
    assert form["step_id"] == "energy_manager"
    return form


def _suggested(form: dict, field: str):
    return next(
        (key.description or {}).get("suggested_value")
        for key in form["data_schema"].schema
        if key == field
    )


async def _submit_energy_manager(hass: HomeAssistant, form: dict, other_pv: list[str]):
    """Submit the Energy Manager's settings, leaving the forecast empty."""
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_EM_P1_ENTITY: "sensor.p1_meter",
            CONF_EM_EXTRA_PV_ENTITIES: other_pv,
            CONF_EM_INVERTER_SN: "SN_HYB",
            CONF_EM_DRY_RUN: True,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
@pytest.mark.usefixtures("cloud_battery_control")
async def test_other_pv_inverters_are_saved_and_reach_the_engine(
    hass: HomeAssistant, client
):
    """Other PV inverters chosen in the Energy Manager's options reach its
    engine, the form suggests them again, and clearing them removes them;
    the form saves with the optional forecast left empty."""
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={"enable_battery_control": True}
    )
    serve_devices(client, {"SN_HYB": HYBRID})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    form = await _open_energy_manager(hass, entry)
    assert _suggested(form, CONF_EM_EXTRA_PV_ENTITIES) is None
    await _submit_energy_manager(hass, form, ["sensor.solaredge_power"])

    assert entry.options[CONF_EM_EXTRA_PV_ENTITIES] == ["sensor.solaredge_power"]
    assert entry.runtime_data.engine._extra_pv_entities == ("sensor.solaredge_power",)

    form = await _open_energy_manager(hass, entry)
    assert _suggested(form, CONF_EM_EXTRA_PV_ENTITIES) == ["sensor.solaredge_power"]
    await _submit_energy_manager(hass, form, [])

    assert CONF_EM_EXTRA_PV_ENTITIES not in entry.options
    assert entry.runtime_data.engine._extra_pv_entities == ()


@pytest.mark.asyncio
async def test_migration_removes_the_former_forecast_power_option(
    hass: HomeAssistant, client
):
    """Migrating an entry from 2.1 removes the former "Solar Forecast Current
    Power" option, keeping its other options, before the coordinator reads
    them."""
    entry = cloud_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_EM_FORECAST_POWER_ENTITY: "sensor.power_production_now",
            "update_interval": 5,
        },
        version=2,
        minor_version=1,
    )
    serve_devices(client, {"SN_HYB": HYBRID})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (entry.version, entry.minor_version) == (2, 2)
    assert entry.options == {"update_interval": 5}
    assert entry.runtime_data.options == entry.options

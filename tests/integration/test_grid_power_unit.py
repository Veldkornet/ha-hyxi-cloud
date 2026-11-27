"""Integration test: the grid power sensor reads the kW the cloud reports."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.hyxi_cloud.const import DOMAIN, device_unique_id
from tests.integration.entries import cloud_entry, lookup_entity_id, serve_devices

SN = "INV123"


@pytest.fixture
def client():
    """The mocked HyxiApiClient every setup of the entry gets."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.access_key = "ak"
        client_class.return_value = mock_client
        yield mock_client


async def _grid_power_state(hass: HomeAssistant, client, grid_p: float, entry):
    """Set the entry up with a gridP poll and return the sensor's state."""
    serve_devices(
        client,
        {
            SN: {
                "device_name": "Inverter",
                "device_type_code": "HYBRID_INVERTER",
                "metrics": {"gridP": grid_p},
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.states.get(lookup_entity_id(hass, entry, "sensor", SN, "gridP"))


@pytest.mark.asyncio
async def test_grid_power_shows_the_cloud_kw_reading_in_watts(
    hass: HomeAssistant, client
):
    """gridP arrives in kW; a new entity shows it in W."""
    state = await _grid_power_state(hass, client, 8.57, cloud_entry(hass))

    assert state.attributes["unit_of_measurement"] == "W"
    assert float(state.state) == pytest.approx(8570)


@pytest.mark.asyncio
async def test_an_existing_grid_power_entity_also_shows_watts(
    hass: HomeAssistant, client
):
    """An entity registered before the unit changed, with no unit option of
    its own, shows the same reading in W rather than switching to kW."""
    entry = cloud_entry(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        device_unique_id(entry, SN, "gridP"),
        config_entry=entry,
        unit_of_measurement="W",
        original_device_class="power",
    )

    state = await _grid_power_state(hass, client, 8.57, entry)

    assert state.attributes["unit_of_measurement"] == "W"
    assert float(state.state) == pytest.approx(8570)


@pytest.mark.asyncio
async def test_grid_power_keeps_its_watt_resolution(hass: HomeAssistant, client):
    """A kW reading is not rounded to the nearest 10 W."""
    state = await _grid_power_state(hass, client, 0.8114, cloud_entry(hass))

    assert float(state.state) == pytest.approx(811.4)

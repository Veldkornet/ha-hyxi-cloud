"""Integration test: a micro-inverter gets no sensors for keys it never
reports, while a hybrid inverter still does."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from tests.integration.entries import cloud_entry, lookup_entity_id, serve_devices

MICRO_SN = "MICRO1"
HYBRID_SN = "HYBRID1"

NEVER_REPORTED_BY_MICRO = (
    "gridSts",
    "invSts",
    "home_load",
    "ph1Loadp",
    "swVerMaster",
    "swVerSlave",
    "totalEnt",
    "totalEpt",
)

# As the cloud client emits them: the firmware version keys are always
# present, with no value.
MICRO_METRICS = {
    "ph1p": 30.0,
    "ph1v": 224.9,
    "acP": 30.0,
    "f": 50.05,
    "swVerMaster": None,
    "swVerSlave": None,
}


@pytest.fixture
def client():
    """The mocked HyxiApiClient every setup of the entry gets."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.access_key = "ak"
        client_class.return_value = mock_client
        yield mock_client


async def _setup(hass: HomeAssistant, client, micro_metrics: dict):
    entry = cloud_entry(hass)
    serve_devices(
        client,
        {
            MICRO_SN: {
                "device_name": "Micro",
                "device_type_code": "MICRO_INVERTER",
                "metrics": micro_metrics,
            },
            HYBRID_SN: {
                "device_name": "Hybrid",
                "device_type_code": "HYBRID_INVERTER",
                "metrics": {"acP": 100.0},
            },
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _has_sensor(hass: HomeAssistant, entry, sn: str, key: str) -> bool:
    return lookup_entity_id(hass, entry, "sensor", sn, key) is not None


@pytest.mark.asyncio
async def test_a_micro_inverter_has_no_sensors_for_keys_it_never_reports(
    hass: HomeAssistant, client
):
    """The micro-inverter keeps its reported sensors; the eight keys it never
    reports get none, and the hybrid inverter still gets them."""
    entry = await _setup(hass, client, MICRO_METRICS)

    assert _has_sensor(hass, entry, MICRO_SN, "ph1p")
    for key in NEVER_REPORTED_BY_MICRO:
        assert not _has_sensor(hass, entry, MICRO_SN, key), key
    for key in ("gridSts", "invSts", "totalEnt", "totalEpt", "ph1Loadp"):
        assert _has_sensor(hass, entry, HYBRID_SN, key), key


@pytest.mark.asyncio
async def test_a_micro_inverter_gets_a_sensor_for_a_key_it_does_report(
    hass: HomeAssistant, client
):
    """A key in the never-reported list still gets its sensor once the
    device reports a value for it."""
    entry = await _setup(hass, client, {**MICRO_METRICS, "totalEnt": 12.5})

    assert _has_sensor(hass, entry, MICRO_SN, "totalEnt")
    assert not _has_sensor(hass, entry, MICRO_SN, "totalEpt")

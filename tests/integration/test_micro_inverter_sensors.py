"""Integration test: a micro-inverter gets no sensors for keys it never
reports, while a hybrid inverter still does."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.hyxi_cloud.const import DOMAIN, device_unique_id
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


def _register_leftovers(hass: HomeAssistant, entry, sn: str, keys) -> None:
    """Registry rows an earlier version created for the given keys."""
    registry = er.async_get(hass)
    for key in keys:
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            device_unique_id(entry, sn, key),
            config_entry=entry,
        )


@pytest.mark.asyncio
async def test_leftover_sensors_of_unreported_keys_are_removed(
    hass: HomeAssistant, client
):
    """Rows an earlier version registered for keys the micro-inverter never
    reports go; the one it reports a value for and the hybrid inverter's
    stay."""
    entry = cloud_entry(hass)
    _register_leftovers(hass, entry, MICRO_SN, NEVER_REPORTED_BY_MICRO)
    _register_leftovers(hass, entry, HYBRID_SN, ("gridSts",))
    registry = er.async_get(hass)
    serve_devices(
        client,
        {
            MICRO_SN: {
                "device_name": "Micro",
                "device_type_code": "MICRO_INVERTER",
                "metrics": {**MICRO_METRICS, "totalEnt": 12.5},
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

    for key in NEVER_REPORTED_BY_MICRO:
        kept = key == "totalEnt"
        assert _has_sensor(hass, entry, MICRO_SN, key) is kept, key
    assert _has_sensor(hass, entry, HYBRID_SN, "gridSts")
    assert registry.async_get_entity_id(
        "sensor", DOMAIN, device_unique_id(entry, MICRO_SN, "ph1p")
    )


@pytest.mark.asyncio
async def test_leftover_sensors_stay_while_the_device_reports_nothing(
    hass: HomeAssistant, client
):
    """A micro-inverter that returned no live reading may just be offline,
    so its null keys don't remove anything."""
    entry = cloud_entry(hass)
    _register_leftovers(hass, entry, MICRO_SN, ("totalEnt",))
    serve_devices(
        client,
        {
            MICRO_SN: {
                "device_name": "Micro",
                "device_type_code": "MICRO_INVERTER",
                "metrics": {"last_seen": "2026-11-27T10:00:00+00:00"},
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _has_sensor(hass, entry, MICRO_SN, "totalEnt")


@pytest.mark.asyncio
async def test_a_renamed_leftover_sensor_stays(hass: HomeAssistant, client):
    """A row the user renamed is theirs to remove."""
    entry = cloud_entry(hass)
    _register_leftovers(hass, entry, MICRO_SN, ("totalEnt", "totalEpt"))
    registry = er.async_get(hass)
    registry.async_update_entity(
        lookup_entity_id(hass, entry, "sensor", MICRO_SN, "totalEnt"),
        name="Meter in",
    )
    serve_devices(
        client,
        {
            MICRO_SN: {
                "device_name": "Micro",
                "device_type_code": "MICRO_INVERTER",
                "metrics": MICRO_METRICS,
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _has_sensor(hass, entry, MICRO_SN, "totalEnt")
    assert not _has_sensor(hass, entry, MICRO_SN, "totalEpt")


COLLECTOR_SN = "COLL1"


async def _setup_collector(hass: HomeAssistant, client, metrics: dict, leftover: bool):
    entry = cloud_entry(hass)
    if leftover:
        _register_leftovers(hass, entry, COLLECTOR_SN, ("wifiVer",))
    serve_devices(
        client,
        {
            COLLECTOR_SN: {
                "device_name": "Collector",
                "device_type_code": "COLLECTOR",
                "metrics": {"hw_version": "V00.00.01.01", **metrics},
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.asyncio
async def test_a_collector_gets_wifi_version_only_when_it_reports_one(
    hass: HomeAssistant, client
):
    """A collector with a Wi-Fi firmware version gets the sensor; one that
    reports none doesn't."""
    entry = await _setup_collector(hass, client, {"wifiVer": "3.3.0.0"}, False)
    assert _has_sensor(hass, entry, COLLECTOR_SN, "wifiVer")
    assert _has_sensor(hass, entry, COLLECTOR_SN, "last_seen")


@pytest.mark.asyncio
async def test_a_collector_without_a_wifi_version_gets_no_sensor(
    hass: HomeAssistant, client
):
    """No sensor is created, and a leftover one is removed."""
    entry = await _setup_collector(hass, client, {"wifiVer": None}, True)
    assert not _has_sensor(hass, entry, COLLECTOR_SN, "wifiVer")

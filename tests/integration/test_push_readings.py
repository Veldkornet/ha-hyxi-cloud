"""Integration tests for real-time push readings applied on top of a poll:
the reading time, repeated readings and phase powers as HYXI pushes them."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from hyxi_cloud_api import HyxiApiClient

from custom_components.hyxi_cloud import _async_handle_webhook
from custom_components.hyxi_cloud.const import DOMAIN
from tests.integration.entries import cloud_entry, lookup_entity_id, serve_devices

SN = "INV123"

# When HYXI delivered the captured reading below, and the poll before it.
ARRIVAL = datetime(2026, 10, 10, 16, 33, 16, tzinfo=UTC)
POLLED_AT = "2026-10-10T16:30:00+00:00"

# A flat reading as HYXI pushed it (trimmed): its collectTime reads
# 08:31:30Z, eight hours behind its arrival, and ph1p..ph3p repeat the
# backup port's ph1peps..ph3peps.
CAPTURED_READING = {
    "deviceSn": SN,
    "collectTime": 1791621090000,
    "batSoc": 89,
    "ph1p": 1,
    "ph2p": 3,
    "ph3p": 2,
    "ph1peps": 1,
    "ph2peps": 3,
    "ph3peps": 2,
}


@pytest.fixture
def client():
    """The mocked HyxiApiClient every setup of the entry gets, parsing
    pushes with the real client."""
    real = HyxiApiClient("ak", "sk", "https://api.com", MagicMock())
    real._discovery_cache["device_info"] = {
        SN: {"model": "HYX-H10K-HT", "device_type_code": "HYBRID_INVERTER"}
    }
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.access_key = "ak"
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        mock_client.process_push_data = real.process_push_data
        client_class.return_value = mock_client
        yield mock_client


async def _push(hass: HomeAssistant, coordinator, reading: dict) -> None:
    request = MagicMock()
    request.headers = {"accessKey": "ak"}
    request.text = AsyncMock(return_value=json.dumps({"dataList": [reading]}))
    response = await _async_handle_webhook("hyxi_cloud_test", request, coordinator)
    assert response.status == 200
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, key: str) -> str:
    return hass.states.get(lookup_entity_id(hass, "sensor", SN, key)).state


@pytest.mark.asyncio
async def test_a_push_after_a_poll_keeps_time_and_phase_powers(
    hass: HomeAssistant, client, freezer
):
    """A pushed reading updates the device with its corrected time but
    keeps the polled phase powers. The same reading sent again changes
    nothing but still counts as a received push; a later reading is
    applied."""
    freezer.move_to(ARRIVAL)
    entry = cloud_entry(hass)
    serve_devices(
        client,
        {
            SN: {
                "device_name": "Inverter",
                "device_type_code": "HYBRID_INVERTER",
                "metrics": {
                    "batSoc": 88,
                    "ph1p": 1201.0,
                    "ph2p": 1189.0,
                    "ph3p": 1195.0,
                    "last_seen": POLLED_AT,
                },
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]

    await _push(hass, coordinator, CAPTURED_READING)

    assert _state(hass, "last_seen") == "2026-10-10T16:31:30+00:00"
    assert _state(hass, "batSoc") == "89"
    assert float(_state(hass, "ph1p")) == 1201.0

    freezer.tick(60)
    await _push(hass, coordinator, {**CAPTURED_READING, "batSoc": 70})

    assert _state(hass, "batSoc") == "89"
    assert coordinator.last_push_received == ARRIVAL + timedelta(seconds=60)

    later = CAPTURED_READING["collectTime"] + 60_000
    await _push(hass, coordinator, {**CAPTURED_READING, "collectTime": later})

    assert _state(hass, "last_seen") == "2026-10-10T16:32:30+00:00"

"""Integration tests for the battery-energy period sensors.

These go through the real Home Assistant entity platform: a
``TOTAL_INCREASING`` energy sensor is exactly the shape a hand-rolled
double would wave through while the platform's own validation rejects it,
so the checks here assert on the registered entity and its live state, not
on internal fields.
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.hyxi_cloud.const import (
    CONF_ACCESS_KEY,
    CONF_SECRET_KEY,
    DOMAIN,
)

INV_SN = "INV_ENERGY_1"
BAT_SN = "BAT_ENERGY_1"

_PERIODS = ("today", "week", "month", "year")


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"},
        options={},
        unique_id="ak",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, metrics: dict) -> None:
    device = {
        INV_SN: {
            "device_name": "Inverter",
            "model": "HYX-H10K-HT",
            "device_type_code": "HYBRID_INVERTER",
            "metrics": {"deviceSn": INV_SN, "batSn": BAT_SN, **metrics},
        }
    }
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client._refresh_token.return_value = True
        mock_client.get_all_device_data.return_value = {
            "data": device,
            "attempts": 1,
        }
        mock_client_class.return_value = mock_client
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_period_sensors_register_with_valid_state_class(hass: HomeAssistant):
    entry = _entry(hass)
    await _setup(
        hass, entry, {"bat_charge_total": "100.0", "bat_discharge_total": "80.0"}
    )

    registry = er.async_get(hass)
    for direction in ("charge", "discharge"):
        for period in _PERIODS:
            unique_id = f"hyxi_{INV_SN}_bat_{direction}_{period}"
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            assert entity_id is not None, unique_id
            entry_row = registry.async_get(entity_id)
            # today + month ship enabled, week + year disabled.
            assert (entry_row.disabled_by is None) == (period in ("today", "month"))

    state = hass.states.get(f"sensor.hyxi_{INV_SN}_bat_charge_today")
    assert state is not None
    assert state.attributes["state_class"] == "total_increasing"
    assert state.attributes["device_class"] == "energy"
    assert state.attributes["unit_of_measurement"] == "kWh"
    # First poll anchors the counter, so the period total starts at zero.
    assert float(state.state) == 0.0


@pytest.mark.asyncio
async def test_today_sensor_prefers_the_device_daily_counter(hass: HomeAssistant):
    entry = _entry(hass)
    await _setup(
        hass,
        entry,
        {
            "bat_charge_total": "900.0",
            "bat_discharge_total": "800.0",
            "bat_charge_today": "6.2",
            "bat_discharge_today": "4.1",
        },
    )

    assert float(hass.states.get(f"sensor.hyxi_{INV_SN}_bat_charge_today").state) == 6.2
    # This-month has no device-native counter, so it derives (starts at 0).
    month = hass.states.get(f"sensor.hyxi_{INV_SN}_bat_charge_month")
    assert float(month.state) == 0.0


@pytest.mark.asyncio
async def test_period_sensors_track_the_lifetime_counter_delta(hass: HomeAssistant):
    entry = _entry(hass)
    await _setup(hass, entry, {"bat_charge_total": "100.0", "bat_discharge_total": "0"})

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.data[INV_SN]["metrics"]["bat_charge_total"] = 105.5
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert float(hass.states.get(f"sensor.hyxi_{INV_SN}_bat_charge_month").state) == 5.5


@pytest.mark.asyncio
async def test_period_sensor_restores_its_anchor_across_a_restart(hass: HomeAssistant):
    entity_id = f"sensor.hyxi_{INV_SN.lower()}_bat_charge_month"
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(entity_id, "8.0"),
                {
                    "anchor_total": 90.0,
                    "anchor_dt": dt_util.now().isoformat(),
                    "last_total": 98.0,
                },
            )
        ],
    )

    entry = _entry(hass)
    await _setup(hass, entry, {"bat_charge_total": "100.0", "bat_discharge_total": "0"})

    # 100 - restored anchor 90, not 100 - 100 = 0.
    assert float(hass.states.get(entity_id).state) == 10.0


@pytest.mark.asyncio
async def test_period_sensor_ignores_corrupt_restore_data(hass: HomeAssistant):
    entity_id = f"sensor.hyxi_{INV_SN.lower()}_bat_charge_month"
    mock_restore_cache_with_extra_data(
        hass,
        [(State(entity_id, "8.0"), {"anchor_total": "not-a-float"})],
    )

    entry = _entry(hass)
    await _setup(hass, entry, {"bat_charge_total": "100.0", "bat_discharge_total": "0"})

    # Corrupt anchor is discarded; the sensor re-anchors from scratch.
    assert float(hass.states.get(entity_id).state) == 0.0

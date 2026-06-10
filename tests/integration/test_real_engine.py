"""Integration tests for the HYXI Energy Manager decision engine."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyxi_cloud.const import (
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    CONF_EM_P1_ENTITY,
    DOMAIN,
)
from custom_components.hyxi_cloud.engine import (
    EMEntityConfig,
    EnergyManagerEngine,
)


@pytest.mark.asyncio
async def test_engine_lifecycle_and_helpers(hass: HomeAssistant):
    """Test engine initialization, lifecycle, properties, and helper methods."""
    # 1. Setup mock config entry and coordinator
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"access_key": "test_ak", "secret_key": "test_sk"},
        options={
            CONF_EM_ENABLED: True,
            CONF_EM_INVERTER_SN: "SN123",
            CONF_EM_P1_ENTITY: "sensor.p1_meter",
            "em_battery_capacity_override": True,
            "em_battery_capacity_wh": 5000,
        },
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.entry = entry
    coordinator.protection_controllers = {}
    coordinator.data = {
        "SN123": {
            "device_name": "Test Inverter",
            "model": "HYX-H10K-HT",
            "device_type_code": "1",
            "metrics": {
                "batSoc": "55.0",
                "ppv": "1000.0",
                "home_load": "800.0",
                "batCap": "10.0",  # 10 kWh
            },
        }
    }

    config = EMEntityConfig(
        sn="SN123",
        p1_entity="sensor.p1_meter",
        forecast_entity="sensor.solar_forecast",
        forecast_power_entity="sensor.solar_forecast_power",
    )

    engine = EnergyManagerEngine(hass, coordinator, config)

    # Test basic properties when stopped
    assert engine.enabled is False
    assert engine.status == "stopped"
    assert engine.decision == ""
    assert engine.last_action == ""
    assert engine.current_mode is None
    assert engine.p1_avg == 0.0

    # Start the engine
    await engine.async_start()
    assert engine.enabled is True
    # Default status is running unless disabled by switch
    assert engine.status == "running"

    # Register/unregister update callback
    cb_called = False

    def my_cb():
        nonlocal cb_called
        cb_called = True

    engine.register_update_callback(my_cb)
    engine._notify_sensors()
    assert cb_called is True
    cb_called = False

    engine.unregister_update_callback(my_cb)
    engine._notify_sensors()
    assert cb_called is False

    # Test get_coordinator_metric helper
    assert engine._get_coordinator_metric("batSoc") == 55.0
    assert engine._get_coordinator_metric("nonexistent", 10.0) == 10.0
    # Try invalid metric float conversion
    coordinator.data["SN123"]["metrics"]["batSoc"] = "invalid"
    assert engine._get_coordinator_metric("batSoc", 50.0) == 50.0
    coordinator.data["SN123"]["metrics"]["batSoc"] = "55.0"

    # Test state readers (float and bool)
    hass.states.async_set("sensor.p1_meter", "250.5")
    assert engine._get_p1() == 250.5
    hass.states.async_set("sensor.p1_meter", "invalid")
    assert engine._get_p1() == 0.0

    hass.states.async_set("switch.hyxi_SN123_em_enabled", "off")
    assert engine._get_ha_state_bool("switch.hyxi_SN123_em_enabled") is False
    hass.states.async_set("switch.hyxi_SN123_em_enabled", "on")
    assert engine._get_ha_state_bool("switch.hyxi_SN123_em_enabled") is True
    hass.states.async_set("switch.hyxi_SN123_em_enabled", "unknown")
    assert engine._get_ha_state_bool("switch.hyxi_SN123_em_enabled") is False

    # Test battery capacity wh
    assert engine._get_battery_capacity() == 5000.0
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "em_battery_capacity_override": False}
    )
    assert engine._get_battery_capacity() == 10000.0  # batCap 10.0 * 1000
    coordinator.data["SN123"]["metrics"]["batCap"] = 0
    assert engine._get_battery_capacity() == 2000.0

    # Test get_param helper
    # It should fallback to EM_DEFAULTS when no number/switch entity exists
    assert engine._get_param("charge_margin") == 150.0  # default is 150
    # Create number entity
    registry = er.async_get(hass)
    num_entry = registry.async_get_or_create(
        "number",
        DOMAIN,
        "hyxi_SN123_em_charge_margin",
        suggested_object_id="hyxi_SN123_em_charge_margin",
    )
    hass.states.async_set(num_entry.entity_id, "200.0")
    assert engine._get_param("charge_margin") == 200.0

    # Switch entity parameter
    sw_entry = registry.async_get_or_create(
        "switch",
        DOMAIN,
        "hyxi_SN123_em_grid_charge_allowed",
        suggested_object_id="hyxi_SN123_em_grid_charge_allowed",
    )
    hass.states.async_set(sw_entry.entity_id, "on")
    assert engine._get_param("grid_charge_allowed") == 1.0

    # Test is_night, hours_until_sunrise, hours_until_sunset
    # Set solar to <= 50 to allow is_night to return True when sun is below horizon
    coordinator.data["SN123"]["metrics"]["ppv"] = "0.0"
    # Elevation < 0 -> night
    hass.states.async_set(
        "sun.sun",
        "below_horizon",
        {
            "elevation": -5.0,
            "next_rising": "2026-06-02T10:00:00Z",
            "next_setting": "2026-06-02T22:00:00Z",
        },
    )
    assert engine._is_night() is True
    # Elevation > 0 -> not night
    hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "elevation": 10.0,
            "next_rising": "2026-06-02T10:00:00Z",
            "next_setting": "2026-06-02T22:00:00Z",
        },
    )
    assert engine._is_night() is False

    with patch(
        "homeassistant.util.dt.utcnow",
        return_value=dt_util.parse_datetime("2026-06-02T08:00:00Z"),
    ):
        assert engine._hours_until_sunrise() == 2.0
        assert engine._hours_until_sunset() == 14.0

    # Test peak shaving support
    # Three-phase device
    assert engine._has_peak_shaving() is False
    # Set to single-phase (change model string so detect_phase_type matches single-phase)
    coordinator.data["SN123"]["model"] = "HYX-H5K-LS"
    assert engine._has_peak_shaving() is True

    # Test night estimates and available battery energy wh
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "em_battery_capacity_wh": 2000}
    )
    soc_min_entry = registry.async_get_or_create(
        "number", DOMAIN, "hyxi_SN123_soc_min", suggested_object_id="hyxi_SN123_soc_min"
    )
    hass.states.async_set(soc_min_entry.entity_id, "15")
    # available energy above soc_min: (55 - 15) * 2000 / 100 = 800 Wh
    assert engine.battery_energy_available_wh() == 800.0

    # Stop the engine
    await engine.async_stop()
    await hass.async_block_till_done()
    assert engine.enabled is False


@pytest.mark.asyncio
async def test_engine_decisions_and_actions(hass: HomeAssistant):
    """Test engine decision-making branches (SOC limits, export limits, load assist, solar, etc.)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"access_key": "test_ak", "secret_key": "test_sk"},
        options={
            CONF_EM_ENABLED: True,
            CONF_EM_INVERTER_SN: "SN123",
            CONF_EM_P1_ENTITY: "sensor.p1_meter",
            "em_dry_run": True,
            "em_battery_capacity_override": True,
            "em_battery_capacity_wh": 10000,
        },
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.entry = entry
    coordinator.protection_controllers = {}
    # Use single-phase for full coverage of export limiting
    coordinator.data = {
        "SN123": {
            "device_name": "Test Inverter",
            "model": "HYX-H5K-LS",
            "device_type_code": "1",
            "metrics": {
                "batSoc": 50,
                "ppv": 0,
                "home_load": 300,
            },
        }
    }

    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(hass, coordinator, config)
    await engine.async_start()

    # Define common entities for tests and register them in the entity registry
    registry = er.async_get(hass)

    # 1. Protection entities (soc_min and soc_max)
    for key, val in [("soc_min", "20"), ("soc_max", "90")]:
        entry_p = registry.async_get_or_create(
            "number",
            DOMAIN,
            f"hyxi_SN123_{key}",
            suggested_object_id=f"hyxi_SN123_{key}",
        )
        hass.states.async_set(entry_p.entity_id, val)

    # 2. EM Number parameters
    em_nums = {
        "max_charge_power": "2000",
        "max_discharge_power": "3000",
        "high_load_threshold": "2500",
        "avg_night_consumption": "0",
        "max_grid_export": "1000",
        "charge_reentry_delay": "90",  # ensures readings_needed is 2 instead of 6
    }
    for key, val in em_nums.items():
        entry_em = registry.async_get_or_create(
            "number",
            DOMAIN,
            f"hyxi_SN123_em_{key}",
            suggested_object_id=f"hyxi_SN123_em_{key}",
        )
        hass.states.async_set(entry_em.entity_id, val)

    # 3. EM Switch parameters
    em_sws = {
        "grid_charge_allowed": "on",
        "export_limiting": "off",
        "high_load_battery_assist": "on",
        "night_mode": "off",
        "enabled": "on",
    }
    for key, val in em_sws.items():
        entry_sw = registry.async_get_or_create(
            "switch",
            DOMAIN,
            f"hyxi_SN123_em_{key}",
            suggested_object_id=f"hyxi_SN123_em_{key}",
        )
        hass.states.async_set(entry_sw.entity_id, val)

    # 1. Test SOC safety limit: emergency solar charge (when solar is producing and SOC <= soc_min)
    coordinator.data["SN123"]["metrics"]["batSoc"] = 15
    coordinator.data["SN123"]["metrics"]["ppv"] = 600
    hass.states.async_set("sensor.p1_meter", "-200")  # exporting 200W
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "emergency_solar_charge"
    assert engine.current_mode == "charge"

    # 2. Test SOC safety limit: emergency grid charge (when solar not producing, switch enabled)
    coordinator.data["SN123"]["metrics"]["ppv"] = 0
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "grid_charge_emergency"

    # If switch is disabled
    sw_grid_entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, "hyxi_SN123_em_grid_charge_allowed"
    )
    hass.states.async_set(sw_grid_entity_id, "off")
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "low_soc_idle"

    # Restore grid charge allowed
    hass.states.async_set(sw_grid_entity_id, "on")

    # 3. Test SOC safety limit: forced discharge (when SOC > soc_max)
    coordinator.data["SN123"]["metrics"]["batSoc"] = 95
    hass.states.async_set("sensor.p1_meter", "1500")  # importing 1500W
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "forced_discharge_over_max"
    assert engine.current_mode == "discharge"

    # Restore normal SOC
    coordinator.data["SN123"]["metrics"]["batSoc"] = 50

    # 4. Test export limiting (requires single-phase + export limiting switch on)
    sw_export_entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, "hyxi_SN123_em_export_limiting"
    )
    hass.states.async_set(sw_export_entity_id, "on")

    # Exporting 1500W (P1 = -1500), limit is 1000W
    hass.states.async_set("sensor.p1_meter", "-1500")
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "export_limit_charge"
    assert engine.current_mode == "charge"

    # Exporting 1500W, but battery is full (SOC >= soc_max) -> curtail PV
    coordinator.data["SN123"]["metrics"]["batSoc"] = 90
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "export_limit_pv_curtail"
    assert engine._pv_curtailed is True

    # Export within limit -> resume PV
    hass.states.async_set("sensor.p1_meter", "-500")
    # Allow time toggle cooldown by bypassing it or waiting
    engine._last_pv_curtail_toggle = -999999.0
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "export_limit_pv_resume"
    assert engine._pv_curtailed is False

    # Turn off export limiting for remaining tests
    hass.states.async_set(sw_export_entity_id, "off")

    # 5. Test high-load assist
    sw_assist_entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, "hyxi_SN123_em_high_load_battery_assist"
    )
    hass.states.async_set(sw_assist_entity_id, "on")

    # Load exceeds threshold, battery has enough energy
    coordinator.data["SN123"]["metrics"]["home_load"] = 3000
    coordinator.data["SN123"]["metrics"]["batSoc"] = 80
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "high_load_battery_assist"
    assert engine.current_mode == "self_consume"

    # Load exceeds threshold, battery depleted (relative to night target + cost) -> grid only
    coordinator.data["SN123"]["metrics"]["batSoc"] = 22
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "high_load_grid_only"
    assert engine.current_mode == "idle"

    # Restore load
    coordinator.data["SN123"]["metrics"]["home_load"] = 500
    hass.states.async_set(sw_assist_entity_id, "off")

    # 6. Test night mode
    sw_night_entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, "hyxi_SN123_em_night_mode"
    )
    hass.states.async_set(sw_night_entity_id, "on")
    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -10.0})
    coordinator.data["SN123"]["metrics"]["ppv"] = 0

    # Night self consume
    coordinator.data["SN123"]["metrics"]["batSoc"] = 40
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "night_self_consume"
    assert engine.current_mode == "self_consume"

    # Night reserve hold
    # Turn off grid charge allowed so safety limit doesn't override night reserve hold
    sw_grid_entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, "hyxi_SN123_em_grid_charge_allowed"
    )
    hass.states.async_set(sw_grid_entity_id, "off")

    # Patch _check_soc_limits to return False so we can reach the otherwise unreachable else block in _check_night
    with patch.object(engine, "_check_soc_limits", return_value=False):
        coordinator.data["SN123"]["metrics"]["batSoc"] = 20
        engine._last_mode_switch = -999999.0
        engine._last_power_adjust = -999999.0
        await engine._make_decision()
        assert engine.decision == "night_reserve_hold"
        assert engine.current_mode == "idle"

    hass.states.async_set(sw_night_entity_id, "off")

    # 7. Test solar optimization (charging and power tuning)
    coordinator.data["SN123"]["metrics"]["batSoc"] = 60
    coordinator.data["SN123"]["metrics"]["ppv"] = 1500
    hass.states.async_set("sensor.p1_meter", "-800")  # export 800W
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 20.0})
    await hass.async_block_till_done()

    # Trigger solar logic
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    # It takes readings_needed (default is 2) to enter charge mode
    assert engine.decision == "solar_export_waiting"

    # Call it again to exceed readings_needed
    engine._last_mode_switch = -999999.0
    engine._last_power_adjust = -999999.0
    await engine._make_decision()
    assert engine.decision == "solar_charge"
    assert engine.current_mode == "charge"

    # Fine tuning charge power while in charge mode: excess export -> increase charge
    hass.states.async_set("sensor.p1_meter", "-1200")
    engine._last_power_adjust = -999999.0  # reset cooldown
    engine._last_mode_switch = -999999.0
    await engine._make_decision()
    assert engine.decision == "solar_charge"

    # Importing -> reduce charge
    hass.states.async_set("sensor.p1_meter", "400")
    engine._last_power_adjust = -999999.0  # reset cooldown
    engine._last_mode_switch = -999999.0
    await engine._make_decision()
    assert engine.decision == "solar_charge_reduced"

    # Clean up
    await engine.async_stop()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_engine_callbacks_and_staleness(hass: HomeAssistant):
    """Test fast-path callbacks, night consumption estimate, staleness auto-reload, and error fallback."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"access_key": "test_ak", "secret_key": "test_sk"},
        options={
            CONF_EM_ENABLED: True,
            CONF_EM_INVERTER_SN: "SN123",
            CONF_EM_P1_ENTITY: "sensor.p1_meter",
            "em_dry_run": False,
        },
    )
    entry.add_to_hass(hass)

    client = AsyncMock()
    # Mock set_mode methods
    client.set_mode_self_consume = AsyncMock()
    client.set_mode_idle = AsyncMock()
    client.set_mode_charge = AsyncMock()
    client.set_mode_discharge = AsyncMock()

    coordinator = MagicMock()
    coordinator.entry = entry
    coordinator.client = client
    coordinator.hyxi_metadata = {"last_success": dt_util.utcnow()}
    coordinator.protection_controllers = {}
    coordinator.data = {
        "SN123": {
            "device_name": "Test Inverter",
            "model": "HYX-H10K-HT",
            "device_type_code": "1",
            "metrics": {"batSoc": 50, "ppv": 0},
        }
    }

    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(hass, coordinator, config)
    await engine.async_start()

    # 1. Test fast-path callback via P1 change event (high load)
    hass.states.async_set("sensor.p1_meter", "3500.0")
    # Trigger event listener callback
    await hass.async_block_till_done()

    # 2. Test fast-path callback via SOC change event
    soc_entity_id = "sensor.hyxi_sn123_batsoc"
    hass.states.async_set(soc_entity_id, "18.0")
    await hass.async_block_till_done()

    # 3. Test coordinator data staleness check (> 10 minutes)
    coordinator.hyxi_metadata["last_success"] = dt_util.utcnow() - timedelta(minutes=15)
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload", return_value=True
    ) as mock_reload:
        await engine._loop_tick(None)
        mock_reload.assert_called_once_with(entry.entry_id)

    # Reset metadata last success to avoid reloading in next ticks
    coordinator.hyxi_metadata["last_success"] = dt_util.utcnow()

    # 4. Test em_enabled switch turn off -> forces self_consume
    sw_em = er.async_get(hass).async_get_or_create(
        "switch",
        DOMAIN,
        "hyxi_SN123_em_enabled",
        suggested_object_id="hyxi_SN123_em_enabled",
    )
    hass.states.async_set(sw_em.entity_id, "off")
    engine._current_mode = "charge"  # simulate currently charging
    await engine._loop_tick(None)
    assert engine.decision == "disabled"
    client.set_mode_self_consume.assert_called_once_with("SN123")

    # 5. Test error fallback (when decision loop raises exception)
    hass.states.async_set(sw_em.entity_id, "on")
    # Make get_soc raise exception
    with patch.object(engine, "_get_soc", side_effect=ValueError("Test exception")):
        await engine._loop_tick(None)
        assert engine.decision == "error"

    await engine.async_stop()
    await hass.async_block_till_done()


def test_engine_p1_avg():
    """Test the p1_avg property of EnergyManagerEngine."""
    coordinator = MagicMock()
    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(MagicMock(), coordinator, config)

    assert engine.p1_avg == 0.0

    # Add one value
    engine._p1_buffer.append((1.0, 100.0))
    assert engine.p1_avg == 100.0

    # Add second value
    engine._p1_buffer.append((2.0, 300.0))
    assert engine.p1_avg == 200.0

    # Add third value
    engine._p1_buffer.append((3.0, -100.0))
    assert engine.p1_avg == 100.0


@pytest.mark.asyncio
async def test_engine_get_param_fallback(hass: HomeAssistant):
    """Test fallback logic in _get_param when _get_ha_state_float returns non-float values."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={"access_key": "test_ak", "secret_key": "test_sk"}
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(hass, coordinator, config)

    # 1. Test fallback when entity state is non-float by mocking _get_ha_state_float
    # We mock _find_entity_id to return a fake ID so it gets to _get_ha_state_float
    with patch.object(engine, "_find_entity_id", return_value="number.fake_id"):
        with patch.object(engine, "_get_ha_state_float", return_value=0.0) as mock_get:
            # high_load_threshold default in EM_DEFAULTS is 6500
            val = engine._get_param("high_load_threshold")
            assert (
                val == 0.0
            )  # mock returns 0.0, which means _get_ha_state_float caught exception and returned default
            mock_get.assert_called_once_with("number.fake_id", 6500.0)

    # 2. Test boolean fallback logic
    with patch.object(engine, "_find_entity_id", side_effect=[None, "switch.fake_id"]):
        with patch.object(engine, "_get_ha_state_bool", return_value=True):
            val = engine._get_param("test_bool_key")
            assert val == 1.0


@pytest.mark.asyncio
async def test_engine_has_peak_shaving_edge_cases(hass: HomeAssistant):
    """Test edge cases for _has_peak_shaving in engine.py."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={"access_key": "test_ak", "secret_key": "test_sk"}
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.data = None
    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(hass, coordinator, config)

    # Test 1: coordinator.data is falsy (None, empty dict)
    assert engine._has_peak_shaving() is False

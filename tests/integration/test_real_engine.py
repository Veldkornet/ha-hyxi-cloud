"""Integration tests for the HYXI Energy Manager decision engine."""

import time
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from homeassistant.core import HassJob, HassJobType, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from hyxi_cloud_api import HyxiApiClient
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
    engine.start()
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
    engine.stop()
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
    engine.start()

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
    engine.stop()
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
    engine.start()

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

    engine.stop()
    await hass.async_block_till_done()


def test_engine_current_mode():
    """Test the current_mode property of EnergyManagerEngine."""
    coordinator = MagicMock()
    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(MagicMock(), coordinator, config)

    assert engine.current_mode is None

    engine._current_mode = "charge"
    assert engine.current_mode == "charge"

    engine._current_mode = "discharge"
    assert engine.current_mode == "discharge"


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


@pytest.mark.asyncio
async def test_engine_status_property(hass: HomeAssistant):
    """Test all branches of the engine status property."""
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

    coordinator = MagicMock()
    coordinator.entry = entry
    coordinator.protection_controllers = {}
    coordinator.data = {
        "SN123": {
            "device_name": "Test Inverter",
            "model": "HYX-H10K-HT",
            "device_type_code": "1",
            "metrics": {},
        }
    }

    config = EMEntityConfig(
        sn="SN123",
        p1_entity="sensor.p1_meter",
        forecast_entity="sensor.solar_forecast",
        forecast_power_entity="sensor.solar_forecast_power",
    )

    engine = EnergyManagerEngine(hass, coordinator, config)

    # 1. Stopped
    assert engine.status == "stopped"

    # Start engine
    engine.start()

    # 2. Disabled (via em_enabled switch off)
    with (
        patch.object(engine, "_find_entity_id", return_value="switch.hyxi_em_enabled"),
        patch.object(engine, "_get_ha_state_bool", return_value=False),
    ):
        assert engine.status == "disabled"

    # 3. Error
    engine._last_decision = "error"
    assert engine.status == "error"
    engine._last_decision = "running"  # reset for next test

    # 4. Cooldown
    engine._last_mode_switch = time.monotonic() - 10
    with patch.object(engine, "_get_param", return_value=300):
        assert engine.status == "cooldown"

    # 5. Dry run
    # Ensure it doesn't hit cooldown
    engine._last_mode_switch = time.monotonic() - 400
    with patch.object(engine, "_get_param", return_value=300):
        with patch.object(
            EnergyManagerEngine, "_dry_run", new_callable=PropertyMock
        ) as mock_dry_run:
            mock_dry_run.return_value = True
            assert engine.status == "dry_run"

    # 6. Running
    with patch.object(engine, "_get_param", return_value=300):
        with patch.object(
            EnergyManagerEngine, "_dry_run", new_callable=PropertyMock
        ) as mock_dry_run:
            mock_dry_run.return_value = False
            assert engine.status == "running"

    engine.stop()


@pytest.mark.asyncio
async def test_engine_protection_and_forecast_helpers(hass: HomeAssistant):
    """Test protection controller integration and solar forecast helpers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"access_key": "test_ak", "secret_key": "test_sk"},
        options={
            CONF_EM_ENABLED: True,
            CONF_EM_INVERTER_SN: "SN123",
            CONF_EM_P1_ENTITY: "sensor.p1_meter",
            "em_battery_capacity_override": True,
            "em_battery_capacity_wh": 2000,
        },
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.entry = entry
    coordinator.protection_controllers = {}
    coordinator.data = {
        "SN123": {"metrics": {"batSoc": "50.0", "ppv": "0.0", "home_load": "0.0"}}
    }

    config = EMEntityConfig(
        sn="SN123", p1_entity="sensor.p1_meter", forecast_entity="sensor.solar_forecast"
    )
    engine = EnergyManagerEngine(hass, coordinator, config)

    # --- _get_protection_controller / _notify_protection ---
    assert engine._get_protection_controller() is None
    engine._notify_protection("charge")  # no controller registered -> no-op, no raise

    protection_controller = MagicMock()
    coordinator.protection_controllers["SN123"] = protection_controller
    assert engine._get_protection_controller() is protection_controller
    engine._notify_protection("discharge")
    protection_controller.note_manual_mode.assert_called_once_with("discharge")

    # --- _get_forecast_remaining_wh ---
    hass.states.async_set("sensor.solar_forecast", "3.5")  # 3.5 kWh
    assert engine._get_forecast_remaining_wh() == 3500.0
    hass.states.async_set("sensor.solar_forecast", "unavailable")
    assert engine._get_forecast_remaining_wh() == 0.0

    # --- _solar_will_cover_charge ---
    # Already at/above target SOC -> True without consulting the forecast at all
    assert engine._solar_will_cover_charge(40) is True

    # Forecast present and sufficient (need 200Wh, forecast usable 6000Wh)
    hass.states.async_set("sensor.solar_forecast", "10.0")
    assert engine._solar_will_cover_charge(60) is True

    # Forecast present but insufficient (need 800Wh, forecast usable 6Wh)
    hass.states.async_set("sensor.solar_forecast", "0.01")
    assert engine._solar_will_cover_charge(90) is False

    # No forecast -> falls back to current solar output + time-to-sunset estimate
    hass.states.async_set("sensor.solar_forecast", "unavailable")
    coordinator.data["SN123"]["metrics"]["ppv"] = "3000.0"
    with patch(
        "homeassistant.util.dt.utcnow",
        return_value=dt_util.parse_datetime("2026-06-02T08:00:00Z"),
    ):
        hass.states.async_set(
            "sun.sun",
            "above_horizon",
            {"next_setting": "2026-06-02T18:00:00Z"},  # 10h to sunset
        )
        # estimated_solar_wh=(3000/2)*10=15000; avg_night_load default=400;
        # usable=(15000-400*10)*0.8=8800Wh, well above the 40Wh needed for +2%
        assert engine._solar_will_cover_charge(52) is True

    # No forecast and no solar currently producing -> cannot estimate -> False
    coordinator.data["SN123"]["metrics"]["ppv"] = "0.0"
    assert engine._solar_will_cover_charge(90) is False


@pytest.mark.asyncio
async def test_engine_set_param(hass: HomeAssistant):
    """Test _set_param writes through to the state machine and preserves any
    existing attributes on the entity's state, and is a no-op when the
    target number entity hasn't been registered yet."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={"access_key": "test_ak", "secret_key": "test_sk"}
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(hass, coordinator, config)

    # No matching number entity registered -> no-op, no raise
    engine._set_param("avg_night_consumption", 500.0)

    registry = er.async_get(hass)
    em_avg_entry = registry.async_get_or_create(
        "number",
        DOMAIN,
        "hyxi_SN123_em_avg_night_consumption",
        suggested_object_id="hyxi_SN123_em_avg_night_consumption",
    )
    hass.states.async_set(em_avg_entry.entity_id, "400", {"unit_of_measurement": "W"})

    engine._set_param("avg_night_consumption", 420.0)

    state = hass.states.get(em_avg_entry.entity_id)
    assert state.state == "420.0"
    # Existing attributes are preserved across the write
    assert state.attributes["unit_of_measurement"] == "W"


@pytest.mark.asyncio
async def test_engine_on_soc_change_fast_path(hass: HomeAssistant):
    """Test the low-SOC fast-path callback triggers/suppresses correctly."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"access_key": "test_ak", "secret_key": "test_sk"},
        options={
            CONF_EM_ENABLED: True,
            CONF_EM_INVERTER_SN: "SN123",
            CONF_EM_P1_ENTITY: "sensor.p1_meter",
        },
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.entry = entry
    coordinator.protection_controllers = {}
    coordinator.data = {"SN123": {"metrics": {"batSoc": "50.0", "ppv": "0.0"}}}

    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(hass, coordinator, config)

    registry = er.async_get(hass)
    soc_min_entry = registry.async_get_or_create(
        "number", DOMAIN, "hyxi_SN123_soc_min", suggested_object_id="hyxi_SN123_soc_min"
    )
    hass.states.async_set(soc_min_entry.entity_id, "20")

    with patch.object(engine, "_make_decision", new=AsyncMock()) as mock_decision:
        # Not started yet -> disabled guard short-circuits even a low-SOC event
        event = MagicMock()
        event.data = {"new_state": MagicMock(state="10.0")}
        engine._on_soc_change(event)
        await hass.async_block_till_done()
        mock_decision.assert_not_called()

        engine.start()

        # Missing new_state -> no-op
        event.data = {"new_state": None}
        engine._on_soc_change(event)
        await hass.async_block_till_done()
        mock_decision.assert_not_called()

        # Unavailable state -> no-op
        event.data = {"new_state": MagicMock(state="unavailable")}
        engine._on_soc_change(event)
        await hass.async_block_till_done()
        mock_decision.assert_not_called()

        # Non-numeric state -> no-op (ValueError swallowed)
        event.data = {"new_state": MagicMock(state="not-a-number")}
        engine._on_soc_change(event)
        await hass.async_block_till_done()
        mock_decision.assert_not_called()

        # SOC at/above soc_min -> no fast-path trigger needed
        event.data = {"new_state": MagicMock(state="50.0")}
        engine._on_soc_change(event)
        await hass.async_block_till_done()
        mock_decision.assert_not_called()

        # SOC below soc_min -> triggers the decision fast-path
        event.data = {"new_state": MagicMock(state="10.0")}
        engine._on_soc_change(event)
        await hass.async_block_till_done()
        mock_decision.assert_called_once()

        # A second low-SOC event within the 15s cooldown is suppressed
        mock_decision.reset_mock()
        engine._on_soc_change(event)
        await hass.async_block_till_done()
        mock_decision.assert_not_called()

        engine.stop()


def test_engine_registered_callbacks_run_on_the_event_loop(hass: HomeAssistant):
    """Every handler start() registers with async_track_time_interval /
    async_track_state_change_event must be classified by Home Assistant as
    safe to run on the event loop, not dispatched to a worker thread.

    A plain `def` handler without @callback is treated as
    HassJobType.Executor and run via loop.run_in_executor -- unsafe for
    _update_night_estimate, which reaches hass.states.async_set() and
    entity_platform.async_get_platforms() through _set_param(), both of
    which require the event loop thread. Hand-mocked tests that call these
    handlers directly (e.g. test_engine_update_night_estimate below) can't
    catch this, since they bypass HA's job-dispatch entirely.
    """
    engine, _coordinator, _entry = _make_engine(hass)

    for method in (
        engine._loop_tick,
        engine._on_p1_change,
        engine._on_soc_change,
        engine._update_night_estimate,
    ):
        job_type = HassJob(method).job_type
        assert job_type != HassJobType.Executor, (
            f"{method.__name__} would run in a worker thread, not the event loop"
        )


@pytest.mark.asyncio
async def test_engine_update_night_estimate(hass: HomeAssistant):
    """Test the hourly night-consumption EMA update, including that it now
    actually persists the new estimate back to its number entity (see
    engine._set_param — this used to be computed and logged but never
    written anywhere, so avg_night_consumption never actually adapted)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"access_key": "test_ak", "secret_key": "test_sk"},
        options={
            CONF_EM_ENABLED: True,
            CONF_EM_INVERTER_SN: "SN123",
            CONF_EM_P1_ENTITY: "sensor.p1_meter",
        },
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.entry = entry
    coordinator.protection_controllers = {}
    coordinator.data = {"SN123": {"metrics": {"batSoc": "50.0", "ppv": "0.0"}}}

    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(hass, coordinator, config)

    registry = er.async_get(hass)
    em_avg_entry = registry.async_get_or_create(
        "number",
        DOMAIN,
        "hyxi_SN123_em_avg_night_consumption",
        suggested_object_id="hyxi_SN123_em_avg_night_consumption",
    )
    hass.states.async_set(em_avg_entry.entity_id, "400")

    # Disabled engine -> no-op regardless of time/P1
    hass.states.async_set("sensor.p1_meter", "600")
    with patch(
        "homeassistant.util.dt.now",
        return_value=dt_util.parse_datetime("2026-06-02T23:00:00"),
    ):
        engine._update_night_estimate(None)
    assert hass.states.get(em_avg_entry.entity_id).state == "400"

    engine.start()

    # Outside the night window (21:00-06:00) -> no-op
    with patch(
        "homeassistant.util.dt.now",
        return_value=dt_util.parse_datetime("2026-06-02T12:00:00"),
    ):
        engine._update_night_estimate(None)
    assert hass.states.get(em_avg_entry.entity_id).state == "400"

    # Inside the night window but P1 <= 0 (not importing) -> no-op
    hass.states.async_set("sensor.p1_meter", "0")
    with patch(
        "homeassistant.util.dt.now",
        return_value=dt_util.parse_datetime("2026-06-02T23:00:00"),
    ):
        engine._update_night_estimate(None)
    assert hass.states.get(em_avg_entry.entity_id).state == "400"

    # Inside the night window with a positive P1 reading -> EMA updates and
    # persists back to the number entity's state
    hass.states.async_set("sensor.p1_meter", "700")
    with patch(
        "homeassistant.util.dt.now",
        return_value=dt_util.parse_datetime("2026-06-02T23:00:00"),
    ):
        engine._update_night_estimate(None)
    # raw new_avg = 400 * 0.9 + 700 * 0.1 = 430, quantized to the nearest
    # AVG_NIGHT_CONSUMPTION_STEP (50) -> 450
    assert float(hass.states.get(em_avg_entry.entity_id).state) == 450.0

    # The persisted value feeds back into the next read via _get_param
    assert engine._get_param("avg_night_consumption") == 450.0

    # A large P1 import spike must not push the persisted estimate above
    # AVG_NIGHT_CONSUMPTION_MAX (2000) -- the entity's own declared max.
    hass.states.async_set(em_avg_entry.entity_id, "2000")
    hass.states.async_set("sensor.p1_meter", "20000")
    with patch(
        "homeassistant.util.dt.now",
        return_value=dt_util.parse_datetime("2026-06-02T23:00:00"),
    ):
        engine._update_night_estimate(None)
    # raw = 2000 * 0.9 + 20000 * 0.1 = 3800, well above the 2000 ceiling
    assert float(hass.states.get(em_avg_entry.entity_id).state) == 2000.0

    # A stored value below AVG_NIGHT_CONSUMPTION_MIN (100) -- e.g. forced
    # in externally -- must not be allowed to drift lower still.
    hass.states.async_set(em_avg_entry.entity_id, "50")
    hass.states.async_set("sensor.p1_meter", "1")
    with patch(
        "homeassistant.util.dt.now",
        return_value=dt_util.parse_datetime("2026-06-02T23:00:00"),
    ):
        engine._update_night_estimate(None)
    # raw = 50 * 0.9 + 1 * 0.1 = 45.1, below the 100 floor
    assert float(hass.states.get(em_avg_entry.entity_id).state) == 100.0

    engine.stop()


def _make_engine(
    hass: HomeAssistant,
    *,
    options=None,
    metrics=None,
    model="H10K-HT",
    device_type_code="1",
):
    """Build a real EnergyManagerEngine with a minimal coordinator/entry,
    for tests that only need engine helper methods, not the full setup flow.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"access_key": "test_ak", "secret_key": "test_sk"},
        options={
            CONF_EM_ENABLED: True,
            CONF_EM_INVERTER_SN: "SN123",
            CONF_EM_P1_ENTITY: "sensor.p1_meter",
            **(options or {}),
        },
    )
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.entry = entry
    coordinator.protection_controllers = {}
    coordinator.data = {
        "SN123": {
            "device_name": "Test Inverter",
            "model": model,
            "device_type_code": device_type_code,
            "metrics": {
                "batSoc": "50.0",
                "ppv": "0.0",
                "home_load": "0.0",
                **(metrics or {}),
            },
        }
    }

    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    engine = EnergyManagerEngine(hass, coordinator, config)
    return engine, coordinator, entry


@pytest.mark.asyncio
async def test_engine_lifecycle_edge_cases(hass: HomeAssistant):
    """Test start/stop idempotency and the SOC-listener branch."""
    engine, _coordinator, _entry = _make_engine(hass)
    registry = er.async_get(hass)

    # Register the batSoc sensor entity so start's SOC-listener branch
    # (only taken when the entity actually exists) gets exercised.
    soc_entry = registry.async_get_or_create(
        "sensor", DOMAIN, "hyxi_SN123_batsoc", suggested_object_id="hyxi_sn123_batsoc"
    )
    hass.states.async_set(soc_entry.entity_id, "50.0")

    engine.start()
    assert engine.enabled is True

    # Calling start again while already enabled is a no-op
    engine.start()
    assert engine.enabled is True

    engine.stop()
    assert engine.enabled is False

    # Calling stop again while already stopped is a no-op
    engine.stop()
    assert engine.enabled is False


@pytest.mark.asyncio
async def test_engine_state_reading_edge_cases(hass: HomeAssistant):
    """Test defensive fallback branches across the state-reading helpers."""
    engine, coordinator, entry = _make_engine(hass)

    # _get_coordinator_metric: no coordinator data at all
    coordinator.data = None
    assert engine._get_coordinator_metric("batSoc", 42.0) == 42.0

    # _get_coordinator_metric: data present but nothing for this SN
    coordinator.data = {"OTHER_SN": {"metrics": {}}}
    assert engine._get_coordinator_metric("batSoc", 42.0) == 42.0

    # _get_ha_state_bool: no entity_id at all
    assert engine._get_ha_state_bool(None, True) is True

    # _get_battery_capacity: override enabled but the stored value can't be
    # converted to float -- falls through to the API metric instead
    coordinator.data = {"SN123": {"metrics": {"batCap": "5.0"}}}
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "em_battery_capacity_override": True,
            "em_battery_capacity_wh": "not-a-number",
        },
    )
    assert engine._get_battery_capacity() == 5000.0  # batCap 5.0 kWh -> 5000 Wh

    # _get_protection_param: no matching number entity registered
    assert engine._get_protection_param("soc_min", 17.0) == 17.0

    # _hours_until_sunrise: sun.sun exists but next_rising fails to parse
    hass.states.async_set(
        "sun.sun", "above_horizon", {"next_rising": "not-a-timestamp"}
    )
    assert engine._hours_until_sunrise() == 12.0

    # _hours_until_sunset: no sun.sun entity registered at all
    hass.states.async_remove("sun.sun")
    assert engine._hours_until_sunset() == 12.0

    # _hours_until_sunset: sun.sun exists but next_setting fails to parse
    hass.states.async_set(
        "sun.sun", "above_horizon", {"next_setting": "not-a-timestamp"}
    )
    assert engine._hours_until_sunset() == 12.0

    # _soc_needed_for_night: non-positive capacity falls back to 10000 Wh
    with patch.object(engine, "_get_param", return_value=0.0):
        # avg_night_consumption=0 and battery_capacity_wh=0 via the same
        # patched _get_param -- wh_needed comes out to 0, but capacity's
        # <= 0 branch must still be taken rather than dividing by zero.
        result = engine._soc_needed_for_night()
    assert result is not None  # didn't raise ZeroDivisionError


@pytest.mark.asyncio
async def test_engine_control_methods_live_mode(hass: HomeAssistant):
    """Test _set_mode/_adjust_power/_set_peak_shaving in live (non-dry-run)
    mode, and their cooldown/threshold guard branches."""
    engine, coordinator, _entry = _make_engine(hass, options={"em_dry_run": False})
    client = AsyncMock()
    coordinator.client = client

    # --- _set_mode: live dispatch for each mode ---
    engine._last_mode_switch = -999999.0
    assert await engine._set_mode("idle") is True
    client.set_mode_idle.assert_called_once_with("SN123")

    engine._last_mode_switch = -999999.0
    assert await engine._set_mode("charge", 500) is True
    client.set_mode_charge.assert_called_once_with("SN123", 500)
    assert engine._last_sent_power["charge"] == 500

    engine._last_mode_switch = -999999.0
    assert await engine._set_mode("discharge", 400) is True
    client.set_mode_discharge.assert_called_once_with("SN123", 400)

    # Unknown mode -> logged and rejected, no API call
    engine._last_mode_switch = -999999.0
    assert await engine._set_mode("not_a_real_mode") is False

    # --- _set_mode: live dispatch, API raises ---
    client.set_mode_idle.side_effect = HyxiApiClient.ControlError("nope")
    engine._last_mode_switch = -999999.0
    assert await engine._set_mode("idle") is False
    client.set_mode_idle.side_effect = None

    # --- _adjust_power: cooldown guard ---
    engine._last_power_adjust = time.monotonic()
    assert await engine._adjust_power("charge", 600) is False

    # --- _adjust_power: threshold-not-exceeded guard ---
    engine._last_power_adjust = -999999.0
    engine._last_sent_power["charge"] = 500
    with patch.object(engine, "_get_param", return_value=100.0):
        # target (550) within threshold (100) of current (500) -> no-op
        assert await engine._adjust_power("charge", 550) is False

    # --- _adjust_power: live dispatch ---
    engine._last_power_adjust = -999999.0
    engine._last_sent_power["charge"] = 0
    assert await engine._adjust_power("charge", 700) is True
    client.set_mode_charge.assert_called_with("SN123", 700)

    engine._last_power_adjust = -999999.0
    engine._last_sent_power["discharge"] = 0
    assert await engine._adjust_power("discharge", 300) is True
    client.set_mode_discharge.assert_called_with("SN123", 300)

    # --- _adjust_power: live dispatch, API raises ---
    client.set_mode_charge.side_effect = HyxiApiClient.ControlError("nope")
    engine._last_power_adjust = -999999.0
    engine._last_sent_power["charge"] = 0
    assert await engine._adjust_power("charge", 700) is False
    client.set_mode_charge.side_effect = None

    # --- _set_peak_shaving: cooldown guard ---
    engine._last_pv_curtail_toggle = time.monotonic()
    assert await engine._set_peak_shaving("hold") is False

    # --- _set_peak_shaving: live dispatch ---
    engine._last_pv_curtail_toggle = -999999.0
    assert await engine._set_peak_shaving("stop") is True
    client.set_peak_shaving.assert_called_once_with("SN123", "stop")
    assert engine._pv_curtailed is True

    # --- _set_peak_shaving: live dispatch, API raises ---
    client.set_peak_shaving.side_effect = HyxiApiClient.ControlError("nope")
    engine._last_pv_curtail_toggle = -999999.0
    assert await engine._set_peak_shaving("hold") is False

    # --- _release_pv_curtailment: no-op when not curtailed ---
    engine._pv_curtailed = False
    await engine._release_pv_curtailment()  # must not raise / not call the API

    # --- _get_current_power_setting: tracked value takes priority ---
    engine._last_sent_power["charge"] = 999
    assert engine._get_current_power_setting("charge") == 999

    # --- _get_current_power_setting: falls back to the number entity ---
    engine._last_sent_power["discharge"] = 0
    registry = er.async_get(hass)
    power_entry = registry.async_get_or_create(
        "number",
        DOMAIN,
        "hyxi_SN123_discharge_power",
        suggested_object_id="hyxi_sn123_discharge_power",
    )
    hass.states.async_set(power_entry.entity_id, "321")
    assert engine._get_current_power_setting("discharge") == 321.0


@pytest.mark.asyncio
async def test_engine_make_decision_guards(hass: HomeAssistant):
    """Test the concurrent-run guard and the default-fallback branch."""
    engine, coordinator, _entry = _make_engine(hass, options={"em_dry_run": True})
    registry = er.async_get(hass)

    # Concurrent-run guard: a decision already in progress is skipped
    engine._in_decision = True
    await engine._make_decision()  # returns immediately, no state read attempted
    assert engine.decision == ""  # never got past the guard to set a decision

    # Default fallback: nothing (solar/night/high-load/export) applies, and
    # the engine is currently mid charge -> forced back to self_consume.
    engine._in_decision = False
    engine._current_mode = "charge"
    coordinator.data["SN123"]["metrics"]["ppv"] = "0.0"  # not solar-producing
    coordinator.data["SN123"]["metrics"]["batSoc"] = "50.0"  # well within limits
    hass.states.async_set("sensor.p1_meter", "0")
    hass.states.async_set(
        "sun.sun", "above_horizon", {"elevation": 10.0}
    )  # daytime, not night
    for key in ("soc_min", "soc_max"):
        registry.async_get_or_create(
            "number",
            DOMAIN,
            f"hyxi_SN123_{key}",
            suggested_object_id=f"hyxi_sn123_{key}",
        )
    hass.states.async_set(
        registry.async_get_entity_id("number", DOMAIN, "hyxi_SN123_soc_min"), "20"
    )
    hass.states.async_set(
        registry.async_get_entity_id("number", DOMAIN, "hyxi_SN123_soc_max"), "90"
    )

    await engine._make_decision()

    assert engine.decision == "idle_default"


@pytest.mark.asyncio
async def test_engine_runs_a_pv_less_halo(hass: HomeAssistant):
    """A HALO (micro_ess) over local Modbus can run the Energy Manager.

    It is AC-coupled and normally PV-less, and it surfaces no battery
    capacity, so the solar paths stay inert and capacity falls back to the
    2000 Wh floor. Peak shaving stays off because it is device-type gated,
    not just phase gated -- even a (synthetic) single-phase-suffixed HALO
    does not get it.
    """
    engine, coordinator, _entry = _make_engine(
        hass,
        options={"em_dry_run": True},
        metrics={"batSoc": "50.0", "home_load": "300.0"},  # no ppv, no batCap
        model="HYX-MS3000AC",
        device_type_code="EMS",
    )
    registry = er.async_get(hass)

    assert engine._get_solar() == 0.0
    assert engine._get_battery_capacity() == 2000.0
    assert engine._has_peak_shaving() is False
    coordinator.data["SN123"]["model"] = "HYX-MS3000AC-LS"  # force single_phase
    assert engine._has_peak_shaving() is False

    engine._current_mode = "charge"
    hass.states.async_set("sensor.p1_meter", "0")
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 10.0})
    for key, val in (("soc_min", "20"), ("soc_max", "90")):
        entry = registry.async_get_or_create(
            "number",
            DOMAIN,
            f"hyxi_SN123_{key}",
            suggested_object_id=f"hyxi_sn123_{key}",
        )
        hass.states.async_set(entry.entity_id, val)

    await engine._make_decision()

    assert engine.decision == "idle_default"


def _set_soc_limits(hass, registry, soc_min="20", soc_max="90"):
    """Register and set the soc_min/soc_max protection number entities."""
    for key, val in (("soc_min", soc_min), ("soc_max", soc_max)):
        entry = registry.async_get_or_create(
            "number",
            DOMAIN,
            f"hyxi_SN123_{key}",
            suggested_object_id=f"hyxi_sn123_{key}",
        )
        hass.states.async_set(entry.entity_id, val)


@pytest.mark.asyncio
async def test_engine_check_soc_limits_mode_transitions(hass: HomeAssistant):
    """Test the 'already in the target mode -> adjust instead of switch'
    branches for all three _check_soc_limits outcomes."""
    engine, _coordinator, _entry = _make_engine(
        hass,
        options={
            "em_dry_run": True,
            "em_battery_capacity_override": True,
            "em_battery_capacity_wh": 2000,
        },
    )
    registry = er.async_get(hass)
    _set_soc_limits(hass, registry)

    s_kwargs = {
        "home_load": 0,
        "max_charge": 2000,
        "max_discharge": 2000,
        "is_night": False,
        "night_soc_target": 30,
    }
    from custom_components.hyxi_cloud.engine import DecisionState

    # emergency_solar_charge while already charging -> adjust_power branch
    engine._current_mode = "charge"
    s = DecisionState(
        soc=15,
        solar=1000,
        p1=-100,
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        **s_kwargs,
    )
    assert await engine._check_soc_limits(s) is True
    assert engine.decision == "emergency_solar_charge"

    # grid_charge_emergency while NOT already charging -> set_mode branch
    sw_entry = registry.async_get_or_create(
        "switch",
        DOMAIN,
        "hyxi_SN123_em_grid_charge_allowed",
        suggested_object_id="hyxi_sn123_em_grid_charge_allowed",
    )
    hass.states.async_set(sw_entry.entity_id, "on")
    engine._current_mode = "idle"
    engine._last_mode_switch = -999999.0
    s = DecisionState(
        soc=15,
        solar=0,
        p1=100,
        solar_producing=False,
        soc_min=20,
        soc_max=90,
        **s_kwargs,
    )
    assert await engine._check_soc_limits(s) is True
    assert engine.decision == "grid_charge_emergency"
    assert engine.current_mode == "charge"

    # forced_discharge_over_max while already discharging -> adjust_power branch
    engine._current_mode = "discharge"
    s = DecisionState(
        soc=95,
        solar=0,
        p1=500,
        solar_producing=False,
        soc_min=20,
        soc_max=90,
        **s_kwargs,
    )
    assert await engine._check_soc_limits(s) is True
    assert engine.decision == "forced_discharge_over_max"


@pytest.mark.asyncio
async def test_engine_check_export_limit_branches(hass: HomeAssistant):
    """Test every branch of the single-phase export-limiting priority check."""
    from custom_components.hyxi_cloud.engine import DecisionState

    engine, coordinator, _entry = _make_engine(
        hass,
        options={"em_dry_run": True},
        model="H5K-LS",  # single-phase
    )
    registry = er.async_get(hass)
    s_kwargs = {
        "home_load": 0,
        "max_charge": 2000,
        "max_discharge": 2000,
        "is_night": False,
        "night_soc_target": 30,
    }

    # Not single-phase / no peak shaving support -> always False
    coordinator.data["SN123"]["model"] = "H10K-HT"  # three-phase
    s = DecisionState(
        soc=50, solar=0, p1=0, solar_producing=False, soc_min=20, soc_max=90, **s_kwargs
    )
    assert await engine._check_export_limit(s) is False
    coordinator.data["SN123"]["model"] = "H5K-LS"  # restore single-phase

    # Export limiting switch off, and not currently curtailed -> False, no release
    assert await engine._check_export_limit(s) is False

    # Export limiting switch off but WAS curtailed -> releases curtailment
    sw_export = registry.async_get_or_create(
        "switch",
        DOMAIN,
        "hyxi_SN123_em_export_limiting",
        suggested_object_id="hyxi_sn123_em_export_limiting",
    )
    hass.states.async_set(sw_export.entity_id, "off")
    engine._pv_curtailed = True
    with patch.object(
        engine, "_set_peak_shaving", new=AsyncMock(return_value=True)
    ) as mock_shave:
        assert await engine._check_export_limit(s) is False
        mock_shave.assert_called_once_with("hold")
    engine._pv_curtailed = False

    # Export limiting on, but max_grid_export configured as 0 (disabled) and
    # currently curtailed -> releases curtailment
    hass.states.async_set(sw_export.entity_id, "on")
    engine._pv_curtailed = True
    with patch.object(engine, "_get_param", return_value=0.0):
        with patch.object(
            engine, "_set_peak_shaving", new=AsyncMock(return_value=True)
        ) as mock_shave:
            assert await engine._check_export_limit(s) is False
            mock_shave.assert_called_once_with("hold")
    engine._pv_curtailed = False

    # Exporting beyond the limit, battery has room -> charge to absorb (new
    # mode), also releasing any prior curtailment since we're charging again
    with patch.object(engine, "_get_param", return_value=500.0):
        s = DecisionState(
            soc=50,
            solar=1000,
            p1=-2000,
            solar_producing=True,
            soc_min=20,
            soc_max=90,
            **s_kwargs,
        )
        engine._current_mode = "idle"
        engine._last_mode_switch = -999999.0
        engine._pv_curtailed = True
        with patch.object(
            engine, "_release_pv_curtailment", new=AsyncMock()
        ) as mock_release:
            assert await engine._check_export_limit(s) is True
            mock_release.assert_called_once()
        assert engine.decision == "export_limit_charge"
        engine._pv_curtailed = False

        # Same, but already charging -> adjust_power branch
        engine._current_mode = "charge"
        engine._last_power_adjust = -999999.0
        assert await engine._check_export_limit(s) is True
        assert engine.decision == "export_limit_charge"

        # Exporting beyond the limit, battery full -> curtail via peak shaving
        s = DecisionState(
            soc=95,
            solar=1000,
            p1=-2000,
            solar_producing=True,
            soc_min=20,
            soc_max=90,
            **s_kwargs,
        )
        engine._pv_curtailed = False
        engine._last_pv_curtail_toggle = -999999.0
        assert await engine._check_export_limit(s) is True
        assert engine.decision == "export_limit_pv_curtail"

        # Export within limit while previously charging for export -> revert
        # to self_consume
        engine._current_mode = "charge"
        engine._pv_curtailed = False
        engine._set_decision("export_limit_charge")
        s = DecisionState(
            soc=50,
            solar=200,
            p1=100,
            solar_producing=False,
            soc_min=20,
            soc_max=90,
            **s_kwargs,
        )
        assert await engine._check_export_limit(s) is True
        assert engine.decision == "export_limit_ok"

        # Export within limit and nothing special going on -> False
        engine._current_mode = "self_consume"
        engine._set_decision("idle")
        assert await engine._check_export_limit(s) is False


@pytest.mark.asyncio
async def test_engine_check_high_load_disabled(hass: HomeAssistant):
    """Test _check_high_load returns False when the assist switch is off."""
    from custom_components.hyxi_cloud.engine import DecisionState

    engine, _coordinator, _entry = _make_engine(hass)
    s = DecisionState(
        soc=50,
        solar=0,
        p1=0,
        home_load=9999,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,
        solar_producing=False,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    assert await engine._check_high_load(s) is False


@pytest.mark.asyncio
async def test_engine_check_high_load_below_threshold(hass: HomeAssistant):
    """Test _check_high_load returns False when the assist switch is on but
    home load hasn't actually exceeded the threshold."""
    from custom_components.hyxi_cloud.engine import DecisionState

    engine, _coordinator, _entry = _make_engine(hass)
    registry = er.async_get(hass)
    sw_assist = registry.async_get_or_create(
        "switch",
        DOMAIN,
        "hyxi_SN123_em_high_load_battery_assist",
        suggested_object_id="hyxi_sn123_em_high_load_battery_assist",
    )
    hass.states.async_set(sw_assist.entity_id, "on")

    with patch.object(engine, "_get_param", return_value=6500.0):  # threshold
        s = DecisionState(
            soc=50,
            solar=0,
            p1=0,
            home_load=100,  # well under the threshold
            max_charge=2000,
            max_discharge=2000,
            is_night=False,
            solar_producing=False,
            soc_min=20,
            soc_max=90,
            night_soc_target=30,
        )
        assert await engine._check_high_load(s) is False


@pytest.mark.asyncio
async def test_engine_check_night_daytime_preservation(hass: HomeAssistant):
    """Test the daytime night-reserve-preservation branch of _check_night."""
    from custom_components.hyxi_cloud.engine import DecisionState

    engine, _coordinator, _entry = _make_engine(hass, options={"em_dry_run": True})
    registry = er.async_get(hass)
    sw_night = registry.async_get_or_create(
        "switch",
        DOMAIN,
        "hyxi_SN123_em_night_mode",
        suggested_object_id="hyxi_sn123_em_night_mode",
    )
    hass.states.async_set(sw_night.entity_id, "on")

    # Feed a positive rolling P1 average so p1_avg > 0
    engine._p1_buffer.append((time.monotonic(), 500.0))

    with patch.object(engine, "_solar_will_cover_charge", return_value=False):
        s = DecisionState(
            soc=25,
            solar=0,
            p1=200,
            home_load=0,
            max_charge=2000,
            max_discharge=2000,
            is_night=False,
            solar_producing=False,
            soc_min=20,
            soc_max=90,
            night_soc_target=30,  # soc(25) <= night_soc_target(30)
        )
        engine._current_mode = "self_consume"
        assert await engine._check_night(s) is True
        assert engine.decision == "night_preserve_idle"


@pytest.mark.asyncio
async def test_engine_check_night_returns_false_when_nothing_applies(
    hass: HomeAssistant,
):
    """Test _check_night falls through to False when night mode is on but
    neither the nighttime nor the daytime-preservation condition is met."""
    from custom_components.hyxi_cloud.engine import DecisionState

    engine, _coordinator, _entry = _make_engine(hass)
    registry = er.async_get(hass)
    sw_night = registry.async_get_or_create(
        "switch",
        DOMAIN,
        "hyxi_SN123_em_night_mode",
        suggested_object_id="hyxi_sn123_em_night_mode",
    )
    hass.states.async_set(sw_night.entity_id, "on")

    s = DecisionState(
        soc=80,  # well above night_soc_target -- no preservation needed
        solar=1000,
        p1=0,
        home_load=0,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,  # daytime -- nighttime branch doesn't apply either
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    assert await engine._check_night(s) is False


@pytest.mark.asyncio
async def test_engine_check_solar_branches(hass: HomeAssistant):
    """Test _check_solar's battery-full branch, bottomout-cooldown doubling,
    and sunset-urgency adjustment."""
    from custom_components.hyxi_cloud.engine import DecisionState

    engine, _coordinator, _entry = _make_engine(hass, options={"em_dry_run": True})

    # solar_battery_full: producing solar but SOC already at/above max
    s = DecisionState(
        soc=90,
        solar=1000,
        p1=0,
        home_load=0,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    engine._current_mode = "charge"
    assert await engine._check_solar(s) is True
    assert engine.decision == "solar_battery_full"

    # Bottomout-cooldown doubling + sunset urgency both flow through
    # _solar_charge_logic -> _solar_entry_logic; just confirm no crash and
    # that a decision gets set via the sunset-urgent path.
    engine._last_bottomout_exit = time.monotonic()  # inside bottomout_cooldown
    with (
        patch.object(engine, "_hours_until_sunset", return_value=2.0),  # < 4h
        patch.object(engine, "_solar_will_cover_charge", return_value=False),
    ):
        s = DecisionState(
            soc=50,
            solar=50,  # below min_solar_for_charge -> solar_self_consume path
            p1=0,
            home_load=0,
            max_charge=2000,
            max_discharge=2000,
            is_night=False,
            solar_producing=True,
            soc_min=20,
            soc_max=90,
            night_soc_target=60,  # soc(50) < night_soc_target(60)
        )
        engine._current_mode = "idle"
        assert await engine._check_solar(s) is True
        assert engine.decision == "solar_self_consume"


@pytest.mark.asyncio
async def test_engine_solar_entry_and_tune_logic(hass: HomeAssistant):
    """Test the low-solar entry branch and the tune-logic exit/balanced branches."""
    from custom_components.hyxi_cloud.engine import DecisionState, SolarConfig

    engine, _coordinator, _entry = _make_engine(hass, options={"em_dry_run": True})

    s = DecisionState(
        soc=50,
        solar=50,
        p1=0,
        home_load=0,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    sc = SolarConfig(
        min_solar_for_charge=1000,
        charge_margin=150,
        charge_entry_threshold=500,
        readings_needed=2,
        sunset_urgent=False,
    )

    # solar below threshold -> solar_self_consume, mode switches away from
    # a non-idle/self_consume mode (e.g. was discharging)
    engine._current_mode = "discharge"
    engine._charge_entry_export_count = 5
    await engine._solar_entry_logic(s, sc)
    assert engine.decision == "solar_self_consume"
    assert engine._charge_entry_export_count == 0
    assert engine.current_mode == "self_consume"

    # solar above threshold and P1 not exporting past the entry threshold
    # (neither low-solar nor sustained-export) -> falls to the else branch
    engine._current_mode = "discharge"
    engine._last_mode_switch = -999999.0  # avoid cooldown from the call above
    s_else = DecisionState(
        soc=50,
        solar=1500,
        p1=0,
        home_load=0,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    engine._charge_entry_export_count = 3
    await engine._solar_entry_logic(s_else, sc)
    assert engine.decision == "solar_self_consume"
    assert engine._charge_entry_export_count == 0
    assert engine.current_mode == "self_consume"

    # solar_export_waiting: current_mode not in (self_consume, idle) forces a switch
    engine._current_mode = "discharge"
    engine._last_mode_switch = -999999.0
    s2 = DecisionState(
        soc=50,
        solar=1500,
        p1=-600,
        home_load=0,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    engine._charge_entry_export_count = 0
    await engine._solar_entry_logic(s2, sc)
    assert engine.decision == "solar_export_waiting"
    assert engine.current_mode == "self_consume"

    # _solar_tune_logic: solar has dropped well below threshold while
    # charging -> exit to self_consume
    engine._current_mode = "charge"
    s3 = DecisionState(
        soc=50,
        solar=100,
        p1=0,
        home_load=0,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    await engine._solar_tune_logic(s3, sc)
    assert engine.decision == "solar_self_consume"

    # _solar_tune_logic: P1 within the balanced range -> stays on solar_charge
    # without calling adjust_power
    engine._current_mode = "charge"
    engine._charge_bottomout_count = 3
    s4 = DecisionState(
        soc=50,
        solar=1500,
        p1=0,
        home_load=0,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    with patch.object(engine, "_adjust_power", new=AsyncMock()) as mock_adjust:
        await engine._solar_tune_logic(s4, sc)
        mock_adjust.assert_not_called()
    assert engine.decision == "solar_charge"
    assert engine._charge_bottomout_count == 2

    # _solar_reduce_charge: normal reduction (charge_target stays > 100) --
    # reached via _solar_tune_logic when importing (p1 > charge_margin)
    engine._current_mode = "charge"
    engine._charge_bottomout_count = 3
    engine._last_sent_power["charge"] = 1000
    s5 = DecisionState(
        soc=50,
        solar=1500,
        p1=200,
        home_load=0,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    with patch.object(engine, "_adjust_power", new=AsyncMock()) as mock_adjust:
        await engine._solar_tune_logic(s5, sc)
        mock_adjust.assert_called_once()
    assert engine.decision == "solar_charge"
    assert engine._charge_bottomout_count == 0

    # _solar_reduce_charge: repeated deep imports push charge_target to the
    # 100W floor -- once bottomout_count reaches 5, exit to self_consume
    engine._current_mode = "charge"
    engine._last_mode_switch = -999999.0  # avoid cooldown from earlier calls
    engine._charge_bottomout_count = 4
    engine._last_sent_power["charge"] = 150
    s6 = DecisionState(
        soc=50,
        solar=1500,
        p1=500,
        home_load=0,
        max_charge=2000,
        max_discharge=2000,
        is_night=False,
        solar_producing=True,
        soc_min=20,
        soc_max=90,
        night_soc_target=30,
    )
    await engine._solar_tune_logic(s6, sc)
    assert engine.decision == "solar_self_consume"
    assert engine._charge_bottomout_count == 0
    assert engine.current_mode == "self_consume"


@pytest.mark.asyncio
async def test_engine_loop_tick_edge_cases(hass: HomeAssistant):
    """Test _loop_tick's disabled guard and its exception-fallback branches."""
    engine, coordinator, _entry = _make_engine(hass, options={"em_dry_run": False})
    coordinator.hyxi_metadata = {"last_success": dt_util.utcnow()}
    client = AsyncMock()
    coordinator.client = client

    # Disabled engine -> immediate no-op
    await engine._loop_tick(None)

    engine.start()

    # em_enabled switch off while mid-charge: self_consume fails too, but
    # the exception is swallowed rather than propagating. _set_mode itself
    # already catches ControlError from the API call internally, so to
    # exercise _loop_tick's OWN except clause here we need the failure to
    # come from somewhere _set_mode doesn't wrap -- patch it directly.
    sw_em = er.async_get(hass).async_get_or_create(
        "switch",
        DOMAIN,
        "hyxi_SN123_em_enabled",
        suggested_object_id="hyxi_sn123_em_enabled",
    )
    hass.states.async_set(sw_em.entity_id, "off")
    engine._current_mode = "charge"
    with patch.object(
        engine, "_set_mode", new=AsyncMock(side_effect=ValueError("api down"))
    ):
        await engine._loop_tick(None)
    assert engine.decision == "disabled"

    # Decision loop raises, and the fallback self_consume ALSO raises --
    # both exceptions are swallowed, not propagated to the caller.
    hass.states.async_set(sw_em.entity_id, "on")
    with (
        patch.object(engine, "_get_soc", side_effect=ValueError("boom")),
        patch.object(
            engine,
            "_set_mode",
            new=AsyncMock(side_effect=HyxiApiClient.ControlError("also boom")),
        ),
    ):
        await engine._loop_tick(None)  # must not raise
    assert engine.decision == "error"

    engine.stop()


@pytest.mark.asyncio
async def test_engine_on_p1_change_edge_cases(hass: HomeAssistant):
    """Test _on_p1_change's guard branches, buffer trimming, and fast-path
    trigger for sustained high load."""
    engine, coordinator, _entry = _make_engine(hass)

    # Missing/placeholder new_state -> no-op, nothing buffered
    event = MagicMock()
    event.data = {"new_state": None}
    engine._on_p1_change(event)
    assert len(engine._p1_buffer) == 0

    event.data = {"new_state": MagicMock(state="unavailable")}
    engine._on_p1_change(event)
    assert len(engine._p1_buffer) == 0

    # A stale reading outside the smoothing window gets trimmed
    with patch.object(engine, "_get_param", return_value=60):
        engine._p1_buffer.append((time.monotonic() - 999, 100.0))
        event.data = {"new_state": MagicMock(state="250.0")}
        engine._on_p1_change(event)
    assert all(v == 250.0 for _, v in engine._p1_buffer)  # the stale entry is gone

    # Engine not enabled -> stops right after buffering, no fast-path trigger
    coordinator.data["SN123"]["metrics"]["home_load"] = "9999.0"
    with patch.object(engine, "_make_decision", new=AsyncMock()) as mock_decision:
        event.data = {"new_state": MagicMock(state="300.0")}
        engine._on_p1_change(event)
        await hass.async_block_till_done()
        mock_decision.assert_not_called()

    # Engine enabled + home_load over threshold -> triggers the fast-path
    engine.start()
    with patch.object(engine, "_make_decision", new=AsyncMock()) as mock_decision:
        engine._last_fast_path_trigger = 0
        event.data = {"new_state": MagicMock(state="300.0")}
        engine._on_p1_change(event)
        await hass.async_block_till_done()
        mock_decision.assert_called_once()

    engine.stop()

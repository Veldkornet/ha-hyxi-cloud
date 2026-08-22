"""Tests for the number platform."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# 1. THE BULLETPROOF MOCK
class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    # Allow CoordinatorEntity[HyxiDataUpdateCoordinator] subscripting in class bases
    __class_getitem__ = classmethod(lambda cls, item: cls)

    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


class FakeNumberEntity(FakeBase):
    @property
    def native_value(self):
        return getattr(self, "_attr_native_value", None)


class FakeRestoreEntity(FakeBase):
    async def async_added_to_hass(self):
        pass

    async def async_get_last_state(self):
        return getattr(self, "_mock_last_state", None)


mock_ha = MagicMock()
mock_ha.__path__ = []


def ensure_mock(module_name, attributes=None, mock_obj=None):
    if module_name not in sys.modules:
        sys.modules[module_name] = mock_obj if mock_obj is not None else MagicMock()
    mod = sys.modules[module_name]
    if isinstance(mod, MagicMock) and attributes:
        for attr_name, attr_value in attributes.items():
            if not hasattr(mod, attr_name) or isinstance(
                getattr(mod, attr_name), MagicMock
            ):
                setattr(mod, attr_name, attr_value)
    return mod


ensure_mock("homeassistant", mock_obj=mock_ha)
ensure_mock("homeassistant.components")
ensure_mock("homeassistant.components.number", {"NumberEntity": FakeNumberEntity})
ensure_mock("homeassistant.components.sensor")
ensure_mock("homeassistant.components.binary_sensor")
ensure_mock("homeassistant.config_entries")
ensure_mock("homeassistant.core")
ensure_mock("homeassistant.helpers")
ensure_mock("homeassistant.helpers.entity")
ensure_mock("homeassistant.helpers.aiohttp_client")
ensure_mock("homeassistant.helpers.device_registry")
ensure_mock("homeassistant.helpers.entity_platform")
ensure_mock("homeassistant.helpers.restore_state", {"RestoreEntity": FakeRestoreEntity})
ensure_mock(
    "homeassistant.helpers.update_coordinator",
    {"CoordinatorEntity": FakeCoordinatorEntity},
)
ensure_mock("homeassistant.util")
ensure_mock("homeassistant.const")
ensure_mock("homeassistant.exceptions")


class MockClientError(Exception):
    pass


ensure_mock("aiohttp", {"ClientError": MockClientError})


class MockControlError(Exception):
    pass


mock_api_client_class = MagicMock()
mock_api_client_class.ControlError = MockControlError

mock_api_module = MagicMock()
mock_api_module.HyxiApiClient = mock_api_client_class
mock_api_module.__version__ = "1.0.4"
ensure_mock("hyxi_cloud_api", mock_obj=mock_api_module)

# Now import the modules
import custom_components.hyxi_cloud.const as const_mod
import custom_components.hyxi_cloud.number as number_mod

# Wire up real const functions
number_mod.normalize_device_type = const_mod.normalize_device_type
number_mod.get_raw_device_code = const_mod.get_raw_device_code
number_mod.detect_phase_type = const_mod.detect_phase_type
number_mod.DOMAIN = const_mod.DOMAIN
number_mod.MANUFACTURER = const_mod.MANUFACTURER


def test_safe_int():
    """Test the _safe_int utility function."""
    assert number_mod._safe_int("100", 10) == 100
    assert number_mod._safe_int(100.5, 10) == 100
    assert number_mod._safe_int("abc", 10) == 10
    assert number_mod._safe_int(None, 10) == 10
    assert number_mod._safe_int(-5, 10) == 10  # expects positive integers
    assert number_mod._safe_int(0, 10) == 10
    assert number_mod._safe_int({}, 10) == 10


@pytest.mark.asyncio
async def test_async_setup_entry():
    """Test setting up number entities with battery protection enabled."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {"enable_battery_control": True}

    dev_data_3phase: dict[str, str | dict[str, str]] = {
        "deviceCode": "HYBRID_INVERTER",
        "model": "HYXI-HT",
        "metrics": {
            "phaseType": "3",
            "acPhase": "3",
            "apPhaA": "100",
            "apPhaB": "100",
            "apPhaC": "100",
        },
    }
    dev_data_1phase: dict[str, str | dict[str, str]] = {
        "deviceCode": "HYBRID_INVERTER",
        "model": "HYXI-HS",
        "metrics": {
            "phaseType": "1",
            "acPhase": "1",
            "apPhaA": "100",
            "apPhaB": "0",
            "apPhaC": "0",
        },
    }
    dev_data_micro: dict[str, str | dict[str, str]] = {
        "deviceCode": "MICRO_INVERTER",
        "model": "MICRO-1",
        "metrics": {},
    }

    coordinator = MagicMock()
    coordinator.data = {
        "SN1": dev_data_3phase,
        "SN2": dev_data_1phase,
        "SN3": dev_data_micro,
    }

    hass.data = {number_mod.DOMAIN: {"test_entry": coordinator}}
    async_add_entities = MagicMock()

    # The setup function itself is a coroutine, so we run it using a mock coroutine trick
    # In pure unittest we use asyncio, but here we can just create an async wrapper
    # In pure unittest we use asyncio, but here we can just create an async wrapper
    await number_mod.async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]

    # Expect:
    #   SN1 (three-phase): 2 power + 4 protection = 6
    #   SN2 (single-phase): 4 protection = 4
    #   SN3 (micro): 1 micro power limit
    #   Total = 11
    assert len(entities) == 11
    assert any(
        isinstance(e, number_mod.HyxiPowerNumber)
        and e._direction == "charge"
        and e._sn == "SN1"
        for e in entities
    )
    assert any(
        isinstance(e, number_mod.HyxiPowerNumber)
        and e._direction == "discharge"
        and e._sn == "SN1"
        for e in entities
    )
    assert any(
        isinstance(e, number_mod.HyxiMicroPowerLimit) and e._sn == "SN3"
        for e in entities
    )
    # Verify protection numbers exist for both phases
    protection_entities = [
        e for e in entities if isinstance(e, number_mod.HyxiProtectionNumber)
    ]
    assert len(protection_entities) == 8  # 4 per phase x 2 devices


@pytest.mark.asyncio
async def test_async_setup_entry_no_protection():
    """Test setting up number entities with battery protection disabled."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {"enable_battery_control": False}

    dev_data_3phase: dict[str, str | dict[str, str]] = {
        "deviceCode": "HYBRID_INVERTER",
        "model": "HYXI-HT",
        "metrics": {
            "phaseType": "3",
            "acPhase": "3",
            "apPhaA": "100",
            "apPhaB": "100",
            "apPhaC": "100",
        },
    }
    dev_data_1phase: dict[str, str | dict[str, str]] = {
        "deviceCode": "HYBRID_INVERTER",
        "model": "HYXI-HS",
        "metrics": {
            "phaseType": "1",
            "acPhase": "1",
            "apPhaA": "100",
            "apPhaB": "0",
            "apPhaC": "0",
        },
    }
    dev_data_micro: dict[str, str | dict[str, str]] = {
        "deviceCode": "MICRO_INVERTER",
        "model": "MICRO-1",
        "metrics": {},
    }

    coordinator = MagicMock()
    coordinator.data = {
        "SN1": dev_data_3phase,
        "SN2": dev_data_1phase,
        "SN3": dev_data_micro,
    }

    hass.data = {number_mod.DOMAIN: {"test_entry": coordinator}}
    async_add_entities = MagicMock()

    await number_mod.async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_no_coordinator_data():
    """Test setup returns early (adds nothing) when the coordinator has no data yet."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {"enable_battery_control": True}

    coordinator = MagicMock()
    coordinator.data = {}

    hass.data = {number_mod.DOMAIN: {"test_entry": coordinator}}
    async_add_entities = MagicMock()

    await number_mod.async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_adds_em_numbers():
    """Test EM parameter numbers are added when the Energy Manager is enabled,
    and that max_grid_export is skipped for a three-phase EM inverter."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {
        "enable_battery_control": False,
        number_mod.CONF_EM_ENABLED: True,
        number_mod.CONF_EM_INVERTER_SN: "SN1",
    }

    dev_data_3phase: dict[str, str | dict[str, str]] = {
        "deviceCode": "HYBRID_INVERTER",
        "model": "HYXI-HT",
        "metrics": {"phaseType": "3", "acPhase": "3"},
    }

    coordinator = MagicMock()
    coordinator.data = {"SN1": dev_data_3phase}

    hass.data = {number_mod.DOMAIN: {"test_entry": coordinator}}
    async_add_entities = MagicMock()

    await number_mod.async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    em_entities = [e for e in entities if isinstance(e, number_mod.EMParameterNumber)]
    em_keys = {e._attr_translation_key for e in em_entities}

    # One EMParameterNumber per EM_NUMBER_DEFS entry, except max_grid_export
    # which is single-phase only and this device is three-phase.
    assert "em_max_grid_export" not in em_keys
    assert len(em_entities) == len(number_mod.EM_NUMBER_DEFS) - 1


@pytest.mark.asyncio
async def test_async_setup_entry_adds_em_numbers_single_phase_includes_export():
    """Test max_grid_export IS included for a single-phase EM inverter."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.options = {
        "enable_battery_control": False,
        number_mod.CONF_EM_ENABLED: True,
        number_mod.CONF_EM_INVERTER_SN: "SN1",
    }

    dev_data_1phase: dict[str, str | dict[str, str]] = {
        "deviceCode": "HYBRID_INVERTER",
        "model": "HYXI-HS",
        "metrics": {"phaseType": "1", "acPhase": "1"},
    }

    coordinator = MagicMock()
    coordinator.data = {"SN1": dev_data_1phase}

    hass.data = {number_mod.DOMAIN: {"test_entry": coordinator}}
    async_add_entities = MagicMock()

    await number_mod.async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    em_entities = [e for e in entities if isinstance(e, number_mod.EMParameterNumber)]
    em_keys = {e._attr_translation_key for e in em_entities}

    assert "em_max_grid_export" in em_keys
    assert len(em_entities) == len(number_mod.EM_NUMBER_DEFS)


def test_hyxi_power_number_init():
    """Test initialization of HyxiPowerNumber."""
    coordinator = MagicMock()
    dev_data: dict = {
        "metrics": {"maxChargePower": "5000", "maxDischargePower": "6000"}
    }

    entity = number_mod.HyxiPowerNumber(coordinator, "SN1", dev_data, "charge")
    assert entity._attr_unique_id == "hyxi_SN1_charge_power"
    assert entity._attr_native_max_value == 5000
    assert entity._attr_native_value == 100

    entity = number_mod.HyxiPowerNumber(coordinator, "SN1", dev_data, "discharge")
    assert entity._attr_unique_id == "hyxi_SN1_discharge_power"
    assert entity._attr_native_max_value == 6000
    assert entity._attr_native_value == 100


@pytest.mark.asyncio
async def test_hyxi_power_number_restore_state():
    """Test restoring state for HyxiPowerNumber."""
    coordinator = MagicMock()
    dev_data: dict = {"metrics": {"maxChargePower": "5000"}}
    entity = number_mod.HyxiPowerNumber(coordinator, "SN1", dev_data, "charge")

    # Mock valid state
    mock_state = MagicMock()
    mock_state.state = "150"
    from unittest.mock import AsyncMock

    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    await entity.async_added_to_hass()
    assert entity._attr_native_value == 150

    # Mock invalid state
    mock_state.state = "invalid"
    from unittest.mock import AsyncMock

    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    # Should not crash, value remains 150
    await entity.async_added_to_hass()
    assert entity._attr_native_value == 150


@pytest.mark.asyncio
async def test_hyxi_power_number_set_value():
    """Test setting value for HyxiPowerNumber."""
    coordinator = MagicMock()
    dev_data: dict = {"metrics": {"maxChargePower": "5000"}}
    entity = number_mod.HyxiPowerNumber(coordinator, "SN1", dev_data, "charge")
    entity.async_write_ha_state = MagicMock()

    await entity.async_set_native_value(250.0)
    assert entity._attr_native_value == 250
    entity.async_write_ha_state.assert_called_once()


def test_hyxi_micro_power_limit_init():
    """Test initialization of HyxiMicroPowerLimit."""
    coordinator = MagicMock()
    dev_data: dict = {"device_name": "My Microinverter"}

    entity = number_mod.HyxiMicroPowerLimit(coordinator, "SN1", dev_data)
    assert entity._attr_unique_id == "hyxi_SN1_micro_power_limit"
    assert entity._attr_native_value == 100.0


@pytest.mark.asyncio
async def test_hyxi_micro_power_limit_restore_state():
    """Test restoring state for HyxiMicroPowerLimit."""
    coordinator = MagicMock()
    dev_data: dict = {}
    entity = number_mod.HyxiMicroPowerLimit(coordinator, "SN1", dev_data)

    # Mock valid state
    mock_state = MagicMock()
    mock_state.state = "80.5"
    from unittest.mock import AsyncMock

    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    await entity.async_added_to_hass()
    assert entity._attr_native_value == 80.5

    # Mock invalid state
    mock_state.state = "invalid"
    from unittest.mock import AsyncMock

    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    # Should not crash, value remains 80.5
    await entity.async_added_to_hass()
    assert entity._attr_native_value == 80.5


@pytest.mark.asyncio
async def test_hyxi_micro_power_limit_set_value():
    """Test setting value for HyxiMicroPowerLimit."""
    coordinator = MagicMock()
    client = AsyncMock()
    coordinator.client = client
    dev_data: dict = {}
    entity = number_mod.HyxiMicroPowerLimit(coordinator, "SN1", dev_data)
    entity.async_write_ha_state = MagicMock()

    await entity.async_set_native_value(75.0)
    client.set_micro_power_limit.assert_called_once_with("SN1", 75)
    assert entity._attr_native_value == 75.0
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_hyxi_micro_power_limit_set_value_error():
    """Test setting value handles errors for HyxiMicroPowerLimit."""
    coordinator = MagicMock()
    client = AsyncMock()
    client.set_micro_power_limit.side_effect = MockControlError("API failed")
    coordinator.client = client
    dev_data: dict = {}
    entity = number_mod.HyxiMicroPowerLimit(coordinator, "SN1", dev_data)
    entity.async_write_ha_state = MagicMock()

    with pytest.raises(MockControlError):
        with patch(
            "custom_components.hyxi_cloud.number.HyxiApiClient.ControlError",
            MockControlError,
        ):
            await entity.async_set_native_value(75.0)

    # Value should not be updated and state should not be written
    assert entity._attr_native_value == 100.0
    entity.async_write_ha_state.assert_not_called()


def test_hyxi_setting_number_init():
    """Test initialization of HyxiSettingNumber."""
    coordinator = MagicMock()
    dev_data: dict = {"device_name": "My HALO"}
    definition = number_mod.HALO_SETTING_NUMBER_DEFS[0]  # feed_in_power_limit

    entity = number_mod.HyxiSettingNumber(coordinator, "SN1", dev_data, definition)
    assert entity._attr_unique_id == "hyxi_SN1_feed_in_power_limit"
    assert entity._attr_native_value == 0
    assert entity._attr_native_min_value == 0
    assert entity._attr_native_max_value == 15000


@pytest.mark.asyncio
async def test_hyxi_setting_number_restore_state():
    """Test restoring state for HyxiSettingNumber."""
    coordinator = MagicMock()
    dev_data: dict = {}
    definition = number_mod.HALO_SETTING_NUMBER_DEFS[1]  # vpp_min_soc
    entity = number_mod.HyxiSettingNumber(coordinator, "SN1", dev_data, definition)

    mock_state = MagicMock()
    mock_state.state = "12"
    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    await entity.async_added_to_hass()
    assert entity._attr_native_value == 12.0

    # Invalid stored state should not crash, value stays at the last good one
    mock_state.state = "invalid"
    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    await entity.async_added_to_hass()
    assert entity._attr_native_value == 12.0


@pytest.mark.asyncio
async def test_hyxi_setting_number_set_value():
    """Test setting value for HyxiSettingNumber writes through the client."""
    coordinator = MagicMock()
    client = AsyncMock()
    coordinator.client = client
    dev_data: dict = {}
    definition = number_mod.HALO_SETTING_NUMBER_DEFS[0]  # feed_in_power_limit
    entity = number_mod.HyxiSettingNumber(coordinator, "SN1", dev_data, definition)
    entity.async_write_ha_state = MagicMock()

    await entity.async_set_native_value(3500)
    client.set_feed_in_power_limit.assert_called_once_with(3500)
    assert entity._attr_native_value == 3500
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_hyxi_setting_number_set_value_error():
    """Test setting value handles errors for HyxiSettingNumber."""
    coordinator = MagicMock()
    client = AsyncMock()
    client.set_feed_in_power_limit.side_effect = MockControlError("write failed")
    coordinator.client = client
    dev_data: dict = {}
    definition = number_mod.HALO_SETTING_NUMBER_DEFS[0]
    entity = number_mod.HyxiSettingNumber(coordinator, "SN1", dev_data, definition)
    entity.async_write_ha_state = MagicMock()

    with pytest.raises(MockControlError):
        with patch(
            "custom_components.hyxi_cloud.number.HyxiApiClient.ControlError",
            MockControlError,
        ):
            await entity.async_set_native_value(3500)

    # Value should not be updated and state should not be written
    assert entity._attr_native_value == 0
    entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_hyxi_protection_number_set_value():
    """Test setting value for HyxiProtectionNumber triggers controller evaluation."""
    coordinator = MagicMock()
    dev_data: dict = {}
    definition = {
        "key": "soc_min",
        "unit": "%",
        "min": 5,
        "max": 50,
        "default": 20,
        "icon": "mdi:battery-20",
    }
    entity = number_mod.HyxiProtectionNumber(coordinator, "SN1", dev_data, definition)
    entity.async_write_ha_state = MagicMock()

    # Set up mock controller
    mock_controller = MagicMock()
    mock_controller.async_evaluate = AsyncMock()
    coordinator.protection_controllers = {"SN1": mock_controller}

    entity.hass = MagicMock()
    entity.hass.async_create_task = MagicMock()

    await entity.async_set_native_value(25.0)

    assert entity._attr_native_value == 25
    entity.async_write_ha_state.assert_called_once()
    entity.hass.async_create_task.assert_called_once()


@pytest.mark.asyncio
async def test_hyxi_protection_number_restore_state():
    """Test restoring state for HyxiProtectionNumber."""
    coordinator = MagicMock()
    dev_data: dict = {}
    definition = {
        "key": "soc_min",
        "unit": "%",
        "min": 5,
        "max": 50,
        "default": 20,
        "icon": "mdi:battery-20",
    }
    entity = number_mod.HyxiProtectionNumber(coordinator, "SN1", dev_data, definition)

    # Mock valid state
    mock_state = MagicMock()
    mock_state.state = "35"
    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    await entity.async_added_to_hass()
    assert entity._attr_native_value == 35

    # Mock invalid state -- should not crash, value remains 35
    mock_state.state = "invalid"
    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    await entity.async_added_to_hass()
    assert entity._attr_native_value == 35

    # No prior state at all -- should not crash, value remains 35
    entity.async_get_last_state = AsyncMock(return_value=None)
    await entity.async_added_to_hass()
    assert entity._attr_native_value == 35


def test_em_parameter_number_init():
    """Test initialization of EMParameterNumber pulls fields from its EMNumberDef."""
    coordinator = MagicMock()
    numdef = number_mod.EMNumberDef(
        "high_load_threshold", "W", 1000, 20000, 500, "mdi:flash-alert"
    )

    entity = number_mod.EMParameterNumber(coordinator, "SN1", numdef)

    assert entity._attr_unique_id == "hyxi_SN1_em_high_load_threshold"
    assert entity._attr_translation_key == "em_high_load_threshold"
    assert entity._attr_native_unit_of_measurement == "W"
    assert entity._attr_native_min_value == 1000
    assert entity._attr_native_max_value == 20000
    assert entity._attr_native_step == 500
    assert entity._attr_icon == "mdi:flash-alert"
    # Default native value comes from EM_DEFAULTS, not the EMNumberDef's own min/max
    assert entity._attr_native_value == number_mod.EM_DEFAULTS["high_load_threshold"]
    assert entity._attr_device_info == {
        "identifiers": {(number_mod.DOMAIN, "SN1_energy_manager")},
    }


def test_em_parameter_number_init_unknown_key_defaults_to_zero():
    """Test EMParameterNumber falls back to 0 when the key has no EM_DEFAULTS entry."""
    coordinator = MagicMock()
    numdef = number_mod.EMNumberDef("unmapped_key", "W", 0, 100, 1, "mdi:help")

    entity = number_mod.EMParameterNumber(coordinator, "SN1", numdef)

    assert entity._attr_native_value == 0.0


@pytest.mark.asyncio
async def test_em_parameter_number_restore_state():
    """Test restoring state for EMParameterNumber."""
    coordinator = MagicMock()
    numdef = number_mod.EMNumberDef(
        "avg_night_consumption", "W", 100, 2000, 50, "mdi:weather-night"
    )
    entity = number_mod.EMParameterNumber(coordinator, "SN1", numdef)

    # Mock valid state
    mock_state = MagicMock()
    mock_state.state = "550.5"
    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    await entity.async_added_to_hass()
    assert entity._attr_native_value == 550.5

    # Mock invalid state -- should not crash, value remains unchanged
    mock_state.state = "invalid"
    entity.async_get_last_state = AsyncMock(return_value=mock_state)

    await entity.async_added_to_hass()
    assert entity._attr_native_value == 550.5


@pytest.mark.asyncio
async def test_em_parameter_number_set_value():
    """Test setting value for EMParameterNumber writes state directly (no API call)."""
    coordinator = MagicMock()
    numdef = number_mod.EMNumberDef(
        "avg_night_consumption", "W", 100, 2000, 50, "mdi:weather-night"
    )
    entity = number_mod.EMParameterNumber(coordinator, "SN1", numdef)
    entity.async_write_ha_state = MagicMock()

    await entity.async_set_native_value(875.0)

    assert entity._attr_native_value == 875.0
    entity.async_write_ha_state.assert_called_once()

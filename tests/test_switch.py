"""Tests for the switch platform."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest


# Let conftest.py handle most of the Home Assistant mocking securely
# Local fakes to avoid metaclass conflicts
class FakeBase:
    """Fake base class for testing."""


class FakeCoordinatorEntity(FakeBase):
    """Fake coordinator entity."""

    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


class FakeSwitchEntity(FakeBase):
    """Fake switch entity."""


class FakeRestoreEntity(FakeBase):
    """Fake restore entity."""

    async def async_added_to_hass(self):
        """Mock async_added_to_hass."""
        pass


class MockHomeAssistantError(Exception):
    """Mock HomeAssistantError."""


from tests import conftest

conftest.ensure_mock(
    "homeassistant.components.switch", {"SwitchEntity": FakeSwitchEntity}
)
conftest.ensure_mock(
    "homeassistant.exceptions", {"HomeAssistantError": MockHomeAssistantError}
)
conftest.ensure_mock(
    "homeassistant.helpers.restore_state", {"RestoreEntity": FakeRestoreEntity}
)
conftest.ensure_mock(
    "homeassistant.helpers.update_coordinator",
    {"CoordinatorEntity": FakeCoordinatorEntity},
)


class MockControlError(Exception):
    pass


import hyxi_cloud_api

hyxi_cloud_api.HyxiApiClient = MagicMock()  # type: ignore[attr-defined]
hyxi_cloud_api.HyxiApiClient.ControlError = MockControlError  # type: ignore[misc]

# Now we can safely import our component code
from custom_components.hyxi_cloud import switch as switch_mod
from custom_components.hyxi_cloud.const import DOMAIN


# 2. FIXTURES
@pytest.fixture
def mock_coordinator_fixture():
    """Mock the DataUpdateCoordinator."""
    coordinator = MagicMock()
    coordinator.data = {}
    coordinator.client = MagicMock()
    coordinator.client.set_frequency_control = AsyncMock()
    coordinator.client.set_micro_power_on = AsyncMock()
    coordinator.client.set_micro_power_off = AsyncMock()
    # Ensure async methods are awaitable
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.fixture
def mock_entry_fixture():
    """Mock the ConfigEntry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    return entry


# 3. TESTS
@pytest.mark.asyncio
async def test_async_setup_entry_empty_data(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup exits early when coordinator data is empty."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {}

    async_add_entities = MagicMock()

    await switch_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)
    async_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_single_phase_hybrid(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup for single-phase hybrid inverter."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN_HYBRID_1": {"device_type_code": "HYBRID_INVERTER"}
    }

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.switch.normalize_device_type",
            return_value="hybrid_inverter",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.get_raw_device_code",
            return_value="HYBRID_INVERTER",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.detect_phase_type",
            return_value="single_phase",
        ),
    ):
        await switch_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 1
    assert isinstance(entities[0], switch_mod.HyxiFrequencyControlSwitch)
    assert entities[0]._sn == "SN_HYBRID_1"


@pytest.mark.asyncio
async def test_async_setup_entry_three_phase_hybrid(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup for three-phase hybrid inverter (skipped for frequency control)."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN_HYBRID_3": {"device_type_code": "HYBRID_INVERTER"}
    }

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.switch.normalize_device_type",
            return_value="hybrid_inverter",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.get_raw_device_code",
            return_value="HYBRID_INVERTER",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.detect_phase_type",
            return_value="three_phase",
        ),
    ):
        await switch_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_single_phase_all_in_one(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup for single-phase all-in-one inverter."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {"SN_AIO_1": {"device_type_code": "ALL_IN_ONE"}}

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.switch.normalize_device_type",
            return_value="all_in_one",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.get_raw_device_code",
            return_value="ALL_IN_ONE",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.detect_phase_type",
            return_value="single_phase",
        ),
    ):
        await switch_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 1
    assert isinstance(entities[0], switch_mod.HyxiFrequencyControlSwitch)
    assert entities[0]._sn == "SN_AIO_1"


@pytest.mark.asyncio
async def test_async_setup_entry_three_phase_all_in_one(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup for three-phase all-in-one inverter (skipped for frequency control)."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {"SN_AIO_3": {"device_type_code": "ALL_IN_ONE"}}

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.switch.normalize_device_type",
            return_value="all_in_one",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.get_raw_device_code",
            return_value="ALL_IN_ONE",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.detect_phase_type",
            return_value="three_phase",
        ),
    ):
        await switch_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_micro_inverter(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup for microinverter."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN_MICRO_1": {"device_type_code": "MICRO_INVERTER"}
    }

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.switch.normalize_device_type",
            return_value="micro_inverter",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.get_raw_device_code",
            return_value="MICRO_INVERTER",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.detect_phase_type",
            return_value="unknown",
        ),
    ):
        await switch_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 1
    assert isinstance(entities[0], switch_mod.HyxiMicroPowerSwitch)
    assert entities[0]._sn == "SN_MICRO_1"


@pytest.mark.asyncio
async def test_frequency_control_switch_turn_on(mock_coordinator_fixture):
    """Test turning on the frequency control switch."""
    switch = switch_mod.HyxiFrequencyControlSwitch(
        mock_coordinator_fixture, "SN123", {}
    )
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()

    mock_coordinator_fixture.client.set_frequency_control.assert_called_once_with(
        "SN123", enabled=True
    )
    assert switch._attr_is_on is True
    switch.async_write_ha_state.assert_called_once()
    mock_coordinator_fixture.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_frequency_control_switch_turn_off(mock_coordinator_fixture):
    """Test turning off the frequency control switch."""
    switch = switch_mod.HyxiFrequencyControlSwitch(
        mock_coordinator_fixture, "SN123", {}
    )
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()

    mock_coordinator_fixture.client.set_frequency_control.assert_called_once_with(
        "SN123", enabled=False
    )
    assert switch._attr_is_on is False
    switch.async_write_ha_state.assert_called_once()
    mock_coordinator_fixture.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_frequency_control_switch_error(mock_coordinator_fixture):
    """Test error handling for frequency control switch."""
    switch = switch_mod.HyxiFrequencyControlSwitch(
        mock_coordinator_fixture, "SN123", {}
    )
    switch.async_write_ha_state = MagicMock()

    err = switch_mod.HyxiApiClient.ControlError("Network error")
    mock_coordinator_fixture.client.set_frequency_control.side_effect = err

    with patch("custom_components.hyxi_cloud.switch._LOGGER.error") as mock_logger:
        with pytest.raises(switch_mod.HyxiApiClient.ControlError):
            await switch.async_turn_on()

        mock_logger.assert_called_once_with(
            "Failed to enable frequency control for %s: %s",
            switch_mod.mask_sn("SN123"),
            err,
        )

    switch.async_write_ha_state.assert_not_called()
    mock_coordinator_fixture.async_request_refresh.assert_not_called()
    assert switch._attr_is_on is None

    with patch("custom_components.hyxi_cloud.switch._LOGGER.error") as mock_logger:
        with pytest.raises(switch_mod.HyxiApiClient.ControlError):
            await switch.async_turn_off()

        mock_logger.assert_called_once_with(
            "Failed to disable frequency control for %s: %s",
            switch_mod.mask_sn("SN123"),
            err,
        )


@pytest.mark.asyncio
async def test_micro_power_switch_turn_on(mock_coordinator_fixture):
    """Test turning on the micro power switch."""
    switch = switch_mod.HyxiMicroPowerSwitch(mock_coordinator_fixture, "SN123", {})
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()

    mock_coordinator_fixture.client.set_micro_power_on.assert_called_once_with("SN123")
    assert switch._attr_is_on is True
    switch.async_write_ha_state.assert_called_once()
    mock_coordinator_fixture.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_micro_power_switch_turn_off(mock_coordinator_fixture):
    """Test turning off the micro power switch."""
    switch = switch_mod.HyxiMicroPowerSwitch(mock_coordinator_fixture, "SN123", {})
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()

    mock_coordinator_fixture.client.set_micro_power_off.assert_called_once_with("SN123")
    assert switch._attr_is_on is False
    switch.async_write_ha_state.assert_called_once()
    mock_coordinator_fixture.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_micro_power_switch_error(mock_coordinator_fixture):
    """Test error handling for micro power switch."""
    switch = switch_mod.HyxiMicroPowerSwitch(mock_coordinator_fixture, "SN123", {})
    switch.async_write_ha_state = MagicMock()

    err = Exception("Network error")
    mock_coordinator_fixture.client.set_micro_power_on.side_effect = err

    with pytest.raises(
        switch_mod.HomeAssistantError,
        match="Failed to turn on microinverter: Network error",
    ):
        await switch.async_turn_on()

    switch.async_write_ha_state.assert_not_called()
    mock_coordinator_fixture.async_request_refresh.assert_not_called()
    assert switch._attr_is_on is None

    mock_coordinator_fixture.client.set_micro_power_off.side_effect = err

    with pytest.raises(
        switch_mod.HomeAssistantError,
        match="Failed to turn off microinverter: Network error",
    ):
        await switch.async_turn_off()


def test_frequency_control_switch_properties(mock_coordinator_fixture):
    """Test frequency control switch properties."""
    switch = switch_mod.HyxiFrequencyControlSwitch(
        mock_coordinator_fixture, "SN123", {}
    )
    assert switch._attr_unique_id == "hyxi_SN123_frequency_control"
    assert switch._attr_translation_key == "frequency_control"
    assert switch._attr_icon == "mdi:sine-wave"
    assert switch._attr_is_on is None

    # Fake CoordinatorEntity has no `available` property, so we patch super().available
    with patch(
        "custom_components.hyxi_cloud.entity.HyxiEntity.available",
        new_callable=PropertyMock,
        return_value=True,
        create=True,
    ):
        assert switch.available is True


def test_micro_power_switch_properties(mock_coordinator_fixture):
    """Test micro power switch properties."""
    switch = switch_mod.HyxiMicroPowerSwitch(mock_coordinator_fixture, "SN123", {})
    assert switch._attr_unique_id == "hyxi_SN123_micro_power"
    assert switch._attr_translation_key == "micro_power"
    assert switch._attr_icon == "mdi:power"
    assert switch._attr_is_on is None


@pytest.mark.asyncio
async def test_async_setup_entry_em_switches(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup of Energy Manager switches."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_entry_fixture.options = {
        "em_enabled": True,
        "em_inverter_sn": "SN_EM",
    }
    mock_coordinator_fixture.data = {"SN_EM": {"device_type_code": "HYBRID_INVERTER"}}

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.switch.normalize_device_type",
            return_value="hybrid_inverter",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.get_raw_device_code",
            return_value="HYBRID_INVERTER",
        ),
        patch(
            "custom_components.hyxi_cloud.switch.detect_phase_type",
            return_value="single_phase",
        ),
    ):
        await switch_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_called()
    # Expect 1 frequency control switch + 5 EM switches (grid_charge, enabled, night_mode, high_load, export_limiting)
    entities = async_add_entities.call_args[0][0]

    em_switches = [e for e in entities if isinstance(e, switch_mod.EMToggleSwitch)]
    assert len(em_switches) == 5

    keys = {e._attr_translation_key for e in em_switches}
    assert keys == {
        "em_grid_charge_allowed",
        "em_enabled",
        "em_night_mode",
        "em_high_load_battery_assist",
        "em_export_limiting",
    }


@pytest.mark.asyncio
async def test_em_toggle_switch_turn_on_off():
    """Test turning on and off the EMToggleSwitch."""
    toggle_def = switch_mod.EMToggleDef("test_key", default_on=False)
    switch = switch_mod.EMToggleSwitch(MagicMock(), "SN123", toggle_def)
    switch.async_write_ha_state = MagicMock()

    assert switch._attr_is_on is False

    await switch.async_turn_on()
    assert switch._attr_is_on is True
    switch.async_write_ha_state.assert_called_once()

    switch.async_write_ha_state.reset_mock()

    await switch.async_turn_off()
    assert switch._attr_is_on is False
    switch.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_em_toggle_switch_restore_state():
    """Test restoring state for EMToggleSwitch."""
    toggle_def = switch_mod.EMToggleDef("test_key", default_on=False)
    switch = switch_mod.EMToggleSwitch(MagicMock(), "SN123", toggle_def)

    mock_last_state = MagicMock()
    mock_last_state.state = "on"

    # FakeRestoreEntity lacks async_get_last_state, so we mock it safely with create=True
    with patch.object(
        switch,
        "async_get_last_state",
        new_callable=AsyncMock,
        return_value=mock_last_state,
        create=True,
    ):
        await switch.async_added_to_hass()

    assert switch._attr_is_on is True


@pytest.mark.asyncio
async def test_em_toggle_switch_restore_state_none():
    """Test restoring state for EMToggleSwitch when there is no last state."""
    toggle_def = switch_mod.EMToggleDef("test_key", default_on=False)
    switch = switch_mod.EMToggleSwitch(MagicMock(), "SN123", toggle_def)

    with patch.object(
        switch,
        "async_get_last_state",
        new_callable=AsyncMock,
        return_value=None,
        create=True,
    ):
        await switch.async_added_to_hass()

    assert switch._attr_is_on is False

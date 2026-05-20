"""Tests for the button platform."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# 1. BULLETPROOF MOCKS
class FakeBase:
    """Fake base class for testing."""


class FakeCoordinatorEntity(FakeBase):
    """Fake coordinator entity."""

    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


class FakeButtonEntity(FakeBase):
    """Fake button entity."""


mock_ha = MagicMock()
mock_ha.__path__ = []
mock_ha.callback = lambda func: func
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha

mock_er = MagicMock()
sys.modules["homeassistant.helpers.entity_registry"] = mock_er

mock_ep = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = mock_ep

mock_button = MagicMock()
mock_button.ButtonEntity = FakeButtonEntity
sys.modules["homeassistant.components.button"] = mock_button

mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator

mock_api = MagicMock()
mock_api.__version__ = "1.0.4"


class ControlError(Exception):
    """Mock exception."""


mock_api.HyxiApiClient.ControlError = ControlError
sys.modules["hyxi_cloud_api"] = mock_api

# Now import the modules to test
import custom_components.hyxi_cloud.button as button_mod
from custom_components.hyxi_cloud.const import DOMAIN


@pytest.fixture
def mock_coordinator_fixture():
    """Fixture for coordinator."""
    coord = MagicMock()
    coord.client = AsyncMock()
    # Mock specific control methods
    coord.client.restart_device = AsyncMock()
    coord.client.set_mode_idle = AsyncMock()
    coord.client.set_mode_charge = AsyncMock()
    coord.client.set_mode_discharge = AsyncMock()
    coord.client.set_mode_self_consume = AsyncMock()
    coord.client.set_peak_shaving = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord


@pytest.fixture
def mock_entry_fixture():
    """Fixture for config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    return entry


@pytest.mark.asyncio
async def test_async_setup_entry_micro_inverter(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup for microinverter restart button."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN_MICRO": {"device_type_code": "MICRO_INVERTER", "model": "M-1000"}
    }

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.button.normalize_device_type",
            return_value="micro_inverter",
        ),
        patch(
            "custom_components.hyxi_cloud.button.get_raw_device_code",
            return_value="MICRO_INVERTER",
        ),
    ):
        await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 1
    assert isinstance(entities[0], button_mod.HyxiMicroRestartButton)
    assert entities[0]._sn == "SN_MICRO"


@pytest.mark.asyncio
async def test_async_setup_entry_three_phase(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup for three-phase hybrid inverter mode buttons."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN_HYBRID_3": {"device_type_code": "HYBRID_INVERTER", "model": "H-10K-HT"}
    }

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.button.normalize_device_type",
            return_value="hybrid_inverter",
        ),
        patch(
            "custom_components.hyxi_cloud.button.get_raw_device_code",
            return_value="HYBRID_INVERTER",
        ),
        patch(
            "custom_components.hyxi_cloud.button.detect_phase_type",
            return_value="three_phase",
        ),
    ):
        await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 4
    for entity in entities:
        assert isinstance(entity, button_mod.HyxiModeButton)
    modes = [e._mode for e in entities]
    assert sorted(modes) == ["charge", "discharge", "idle", "self_consume"]


@pytest.mark.asyncio
async def test_async_setup_entry_single_phase(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup for single-phase hybrid inverter peak shaving buttons."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN_HYBRID_1": {"device_type_code": "HYBRID_INVERTER", "model": "H-5K-HS"}
    }

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.button.normalize_device_type",
            return_value="hybrid_inverter",
        ),
        patch(
            "custom_components.hyxi_cloud.button.get_raw_device_code",
            return_value="HYBRID_INVERTER",
        ),
        patch(
            "custom_components.hyxi_cloud.button.detect_phase_type",
            return_value="single_phase",
        ),
    ):
        await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 5
    for entity in entities:
        assert isinstance(entity, button_mod.HyxiPeakShavingButton)
    options = [e._option for e in entities]
    assert sorted(options) == ["charge", "close", "discharge", "hold", "stop"]


@pytest.mark.asyncio
async def test_async_setup_entry_unknown_phase(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup for unknown phase hybrid inverter (no buttons)."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN_HYBRID_UNK": {"device_type_code": "HYBRID_INVERTER", "model": "UNKNOWN"}
    }

    async_add_entities = MagicMock()

    with (
        patch(
            "custom_components.hyxi_cloud.button.normalize_device_type",
            return_value="hybrid_inverter",
        ),
        patch(
            "custom_components.hyxi_cloud.button.get_raw_device_code",
            return_value="HYBRID_INVERTER",
        ),
        patch(
            "custom_components.hyxi_cloud.button.detect_phase_type",
            return_value="unknown",
        ),
    ):
        await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_micro_restart_button_press(mock_coordinator_fixture):
    """Test pressing the microinverter restart button."""
    btn = button_mod.HyxiMicroRestartButton(mock_coordinator_fixture, "SN123", {})

    await btn.async_press()

    mock_coordinator_fixture.client.restart_device.assert_called_once_with("SN123")
    mock_coordinator_fixture.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_micro_restart_button_error(mock_coordinator_fixture):
    """Test error handling when pressing microinverter restart button."""
    mock_coordinator_fixture.client.restart_device.side_effect = (
        button_mod.HyxiApiClient.ControlError("Timeout")
    )
    btn = button_mod.HyxiMicroRestartButton(mock_coordinator_fixture, "SN123", {})

    with pytest.raises(button_mod.HyxiApiClient.ControlError):
        await btn.async_press()


@pytest.mark.asyncio
async def test_mode_button_press_idle_self_consume(mock_coordinator_fixture):
    """Test pressing idle and self_consume mode buttons."""
    btn_idle = button_mod.HyxiModeButton(mock_coordinator_fixture, "SN123", {}, "idle")
    await btn_idle.async_press()
    mock_coordinator_fixture.client.set_mode_idle.assert_called_once_with("SN123")

    btn_sc = button_mod.HyxiModeButton(
        mock_coordinator_fixture, "SN123", {}, "self_consume"
    )
    await btn_sc.async_press()
    mock_coordinator_fixture.client.set_mode_self_consume.assert_called_once_with(
        "SN123"
    )


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.button._get_power_value", return_value=5000)
async def test_mode_button_press_charge_discharge(
    mock_get_power, mock_coordinator_fixture
):
    """Test pressing charge and discharge mode buttons (with power lookups)."""
    hass = MagicMock()

    btn_charge = button_mod.HyxiModeButton(
        mock_coordinator_fixture, "SN123", {}, "charge"
    )
    btn_charge.hass = hass
    await btn_charge.async_press()
    mock_coordinator_fixture.client.set_mode_charge.assert_called_once_with(
        "SN123", 5000
    )
    mock_get_power.assert_any_call(hass, "SN123", "charge")

    btn_discharge = button_mod.HyxiModeButton(
        mock_coordinator_fixture, "SN123", {}, "discharge"
    )
    btn_discharge.hass = hass
    await btn_discharge.async_press()
    mock_coordinator_fixture.client.set_mode_discharge.assert_called_once_with(
        "SN123", 5000
    )
    mock_get_power.assert_any_call(hass, "SN123", "discharge")


@pytest.mark.asyncio
async def test_mode_button_error(mock_coordinator_fixture):
    """Test error handling in mode button press."""
    mock_coordinator_fixture.client.set_mode_idle.side_effect = (
        button_mod.HyxiApiClient.ControlError("Network error")
    )
    btn = button_mod.HyxiModeButton(mock_coordinator_fixture, "SN123", {}, "idle")

    with pytest.raises(button_mod.HyxiApiClient.ControlError):
        await btn.async_press()


@pytest.mark.asyncio
async def test_peak_shaving_button_press(mock_coordinator_fixture):
    """Test pressing peak shaving buttons."""
    for option in ["close", "charge", "discharge", "stop", "hold"]:
        btn = button_mod.HyxiPeakShavingButton(
            mock_coordinator_fixture, "SN123", {}, option
        )
        await btn.async_press()
        mock_coordinator_fixture.client.set_peak_shaving.assert_any_call(
            "SN123", option
        )

    assert mock_coordinator_fixture.client.set_peak_shaving.call_count == 5


@pytest.mark.asyncio
async def test_peak_shaving_button_error(mock_coordinator_fixture):
    """Test error handling in peak shaving button press."""
    mock_coordinator_fixture.client.set_peak_shaving.side_effect = (
        button_mod.HyxiApiClient.ControlError("Fail")
    )
    btn = button_mod.HyxiPeakShavingButton(
        mock_coordinator_fixture, "SN123", {}, "hold"
    )

    with pytest.raises(button_mod.HyxiApiClient.ControlError):
        await btn.async_press()


def test_get_power_value_valid_state():
    """Test _get_power_value with a valid number state."""
    hass = MagicMock()
    registry = MagicMock()
    registry.async_get_entity_id.return_value = "number.hyxi_sn123_charge_power"

    # Mock entity registry get
    with patch(
        "custom_components.hyxi_cloud.button.er.async_get", return_value=registry
    ):
        state = MagicMock()
        state.state = "3000.0"
        hass.states.get.return_value = state

        result = button_mod._get_power_value(hass, "SN123", "charge")

        registry.async_get_entity_id.assert_called_once_with(
            "number", DOMAIN, "hyxi_SN123_charge_power"
        )
        hass.states.get.assert_called_once_with("number.hyxi_sn123_charge_power")
        assert result == 3000


def test_get_power_value_entity_not_found():
    """Test _get_power_value when number entity is missing from registry."""
    hass = MagicMock()
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None

    with patch(
        "custom_components.hyxi_cloud.button.er.async_get", return_value=registry
    ):
        result = button_mod._get_power_value(hass, "SN123", "charge")
        assert result == 100


def test_get_power_value_invalid_state():
    """Test _get_power_value when state is unknown/unavailable or non-numeric."""
    hass = MagicMock()
    registry = MagicMock()
    registry.async_get_entity_id.return_value = "number.hyxi_sn123_charge_power"

    with patch(
        "custom_components.hyxi_cloud.button.er.async_get", return_value=registry
    ):
        # Test 'unknown' state
        state_unknown = MagicMock()
        state_unknown.state = "unknown"
        hass.states.get.return_value = state_unknown
        assert button_mod._get_power_value(hass, "SN123", "charge") == 100

        # Test invalid float string
        state_invalid = MagicMock()
        state_invalid.state = "abc"
        hass.states.get.return_value = state_invalid
        assert button_mod._get_power_value(hass, "SN123", "charge") == 100

        # Test None state (entity missing from state machine)
        hass.states.get.return_value = None
        assert button_mod._get_power_value(hass, "SN123", "charge") == 100

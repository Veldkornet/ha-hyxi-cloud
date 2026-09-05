"""Tests for the button platform."""

# pylint: disable=missing-module-docstring, wrong-import-position, import-outside-toplevel
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest


# 1. BULLETPROOF MOCKS
class FakeBase:
    """Fake base class for testing."""


class FakeCoordinatorEntity(FakeBase):
    """Fake coordinator entity."""

    # Allow CoordinatorEntity[HyxiDataUpdateCoordinator] subscripting in class bases
    __class_getitem__ = classmethod(lambda cls, item: cls)

    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        """Mirror HA's real CoordinatorEntity.available: tracks last update success."""
        return getattr(self.coordinator, "last_update_success", True)


class FakeButtonEntity(FakeBase):
    """Fake button entity."""


mock_ha = sys.modules.get("homeassistant")
if mock_ha is None:
    mock_ha = MagicMock()
    mock_ha.__path__ = []
    mock_ha.callback = lambda func: func
    sys.modules["homeassistant"] = mock_ha

if "homeassistant.components" not in sys.modules:
    sys.modules["homeassistant.components"] = mock_ha
if "homeassistant.config_entries" not in sys.modules:
    sys.modules["homeassistant.config_entries"] = mock_ha
if "homeassistant.core" not in sys.modules:
    sys.modules["homeassistant.core"] = mock_ha
if "homeassistant.const" not in sys.modules:
    sys.modules["homeassistant.const"] = mock_ha

if "homeassistant.helpers.entity_registry" not in sys.modules:
    sys.modules["homeassistant.helpers.entity_registry"] = MagicMock()

if "homeassistant.helpers.entity_platform" not in sys.modules:
    sys.modules["homeassistant.helpers.entity_platform"] = MagicMock()

if "homeassistant.components.button" not in sys.modules:
    sys.modules["homeassistant.components.button"] = MagicMock()
button_mock: Any = sys.modules["homeassistant.components.button"]
button_mock.ButtonEntity = FakeButtonEntity


mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator

mock_bs = MagicMock()
mock_bs.BinarySensorEntity = FakeBase
sys.modules["homeassistant.components.binary_sensor"] = mock_bs

mock_api = sys.modules["hyxi_cloud_api"]


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
    coord.client.alter_alarm = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    coord.protection_controllers = {}
    return coord


@pytest.fixture
def mock_entry_fixture():
    """Fixture for config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    return entry


@pytest.mark.asyncio
async def test_async_setup_entry_no_coordinator_data(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test setup adds nothing (and doesn't crash) with no coordinator data yet."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {}

    async_add_entities = MagicMock()
    await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_adds_push_buttons(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Test the renew/purge subscription buttons are added when push is enabled."""
    hass = MagicMock()
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {"SN1": {"device_type_code": "UNKNOWN"}}
    mock_entry_fixture.options = {button_mod.CONF_ENABLE_PUSH: True}

    async_add_entities = MagicMock()
    await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert any(isinstance(e, button_mod.HyxiRenewSubscriptionButton) for e in entities)
    assert any(isinstance(e, button_mod.HyxiPurgeSubscriptionsButton) for e in entities)


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
    assert len(entities) == 2
    assert any(isinstance(e, button_mod.HyxiClearAlarmsButton) for e in entities)
    assert any(isinstance(e, button_mod.HyxiMicroRestartButton) for e in entities)
    assert (
        next(
            e for e in entities if isinstance(e, button_mod.HyxiMicroRestartButton)
        )._sn
        == "SN_MICRO"
    )


@pytest.mark.asyncio
async def test_async_setup_entry_skips_clear_alarms_button_for_modbus(
    mock_coordinator_fixture, mock_entry_fixture
):
    """HyxiClearAlarmsButton calls alter_alarm, which neither Modbus client
    implements, and reads dev_data["alarms"], which neither populates --
    a Modbus entry must not get this button at all."""
    hass = MagicMock()
    mock_entry_fixture.data = {"transport": "modbus"}
    mock_entry_fixture.options = {}
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN123": {"device_type_code": "1", "model": "H10K-HT", "alarms": []}
    }

    async_add_entities = MagicMock()

    await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    if async_add_entities.called:
        entities = async_add_entities.call_args[0][0]
        assert not any(
            isinstance(e, button_mod.HyxiClearAlarmsButton) for e in entities
        )


@pytest.mark.asyncio
async def test_async_setup_entry_adds_refresh_settings_button_for_modbus(
    mock_coordinator_fixture, mock_entry_fixture
):
    """The manual settings-refresh button is Modbus-only, and only when
    battery control is on -- with it off there are no setting number/switch
    entities for a forced re-read to change."""
    hass = MagicMock()
    mock_entry_fixture.data = {"transport": "modbus"}
    mock_entry_fixture.options = {"enable_battery_control": True}
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN123": {"device_type_code": "1", "model": "H10K-HT"}
    }

    async_add_entities = MagicMock()
    await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    button = next(
        e for e in entities if isinstance(e, button_mod.HyxiRefreshSettingsButton)
    )
    assert button._attr_unique_id == f"{mock_entry_fixture.entry_id}_refresh_settings"
    assert button._attr_device_info["name"] == "HYXI Modbus Service"


@pytest.mark.asyncio
async def test_async_setup_entry_skips_refresh_settings_button_for_cloud(
    mock_coordinator_fixture, mock_entry_fixture
):
    """A cloud entry never gets the Modbus-only refresh button."""
    hass = MagicMock()
    mock_entry_fixture.data = {"transport": "cloud"}
    mock_entry_fixture.options = {"enable_battery_control": True}
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN123": {"device_type_code": "1", "model": "H10K-HT", "alarms": []}
    }

    async_add_entities = MagicMock()
    await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    assert not any(
        isinstance(e, button_mod.HyxiRefreshSettingsButton) for e in entities
    )


@pytest.mark.asyncio
async def test_async_setup_entry_skips_refresh_settings_button_without_control(
    mock_coordinator_fixture, mock_entry_fixture
):
    """A Modbus entry with battery control off has no setting number/switch
    entities, so the refresh button (which only helps those) is skipped
    too."""
    hass = MagicMock()
    mock_entry_fixture.data = {"transport": "modbus"}
    mock_entry_fixture.options = {}
    hass.data = {DOMAIN: {mock_entry_fixture.entry_id: mock_coordinator_fixture}}
    mock_coordinator_fixture.data = {
        "SN123": {"device_type_code": "1", "model": "H10K-HT"}
    }

    async_add_entities = MagicMock()
    await button_mod.async_setup_entry(hass, mock_entry_fixture, async_add_entities)

    if async_add_entities.called:
        entities = async_add_entities.call_args[0][0]
        assert not any(
            isinstance(e, button_mod.HyxiRefreshSettingsButton) for e in entities
        )


@pytest.mark.asyncio
async def test_refresh_settings_button_press_forces_refresh_and_requests_a_poll(
    mock_coordinator_fixture, mock_entry_fixture
):
    """Pressing the button must clear the client's throttle before asking
    the coordinator to poll -- otherwise the immediate poll this triggers
    would just hit the still-open refresh window and skip the read."""
    button = button_mod.HyxiRefreshSettingsButton(
        mock_coordinator_fixture, mock_entry_fixture
    )
    mock_coordinator_fixture.client.force_settings_refresh = MagicMock()

    await button.async_press()

    mock_coordinator_fixture.client.force_settings_refresh.assert_called_once_with()
    mock_coordinator_fixture.async_request_refresh.assert_awaited_once()


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
    assert len(entities) == 5
    assert any(isinstance(e, button_mod.HyxiClearAlarmsButton) for e in entities)
    mode_entities = [e for e in entities if isinstance(e, button_mod.HyxiModeButton)]
    assert len(mode_entities) == 4
    modes = [e._mode for e in mode_entities]
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
    assert len(entities) == 6
    assert any(isinstance(e, button_mod.HyxiClearAlarmsButton) for e in entities)
    shaving_entities = [
        e for e in entities if isinstance(e, button_mod.HyxiPeakShavingButton)
    ]
    assert len(shaving_entities) == 5
    options = [e._option for e in shaving_entities]
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

    # For unknown phase, the mode buttons are skipped, but the clear alarms button is still added
    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 1
    assert isinstance(entities[0], button_mod.HyxiClearAlarmsButton)


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

    with pytest.raises(button_mod.HomeAssistantError, match="restart microinverter"):
        await btn.async_press()


@pytest.mark.asyncio
async def test_power_command_button_press(mock_coordinator_fixture):
    """Test pressing each of the three power command buttons."""
    for action in ("power_on", "power_off", "restart"):
        btn = button_mod.HyxiPowerCommandButton(
            mock_coordinator_fixture, "SN123", {}, action
        )
        await btn.async_press()
        getattr(mock_coordinator_fixture.client, action).assert_called_once_with(
            "SN123"
        )


@pytest.mark.asyncio
async def test_power_command_button_error(mock_coordinator_fixture):
    """Test error handling when a power command write fails."""
    mock_coordinator_fixture.client.restart.side_effect = (
        button_mod.HyxiApiClient.ControlError("bus fell over")
    )
    btn = button_mod.HyxiPowerCommandButton(
        mock_coordinator_fixture, "SN123", {}, "restart"
    )

    with pytest.raises(button_mod.HomeAssistantError, match="power command"):
        await btn.async_press()


@pytest.mark.asyncio
async def test_mode_button_press_idle_self_consume(mock_coordinator_fixture):
    """Test pressing idle and self_consume mode buttons."""
    btn_idle = button_mod.HyxiModeButton(mock_coordinator_fixture, "SN123", {}, "idle")
    btn_idle.hass = MagicMock()
    await btn_idle.async_press()
    mock_coordinator_fixture.client.set_mode_idle.assert_called_once_with("SN123")

    btn_sc = button_mod.HyxiModeButton(
        mock_coordinator_fixture, "SN123", {}, "self_consume"
    )
    btn_sc.hass = MagicMock()
    await btn_sc.async_press()
    mock_coordinator_fixture.client.set_mode_self_consume.assert_called_once_with(
        "SN123"
    )


@pytest.mark.asyncio
@patch("custom_components.hyxi_cloud.control._get_power_value", return_value=5000)
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
async def test_mode_button_delegates_and_does_not_swallow_errors(
    mock_coordinator_fixture,
):
    """The mode button is a thin wrapper over control.async_send_battery_mode:
    it forwards its args and lets the helper's HomeAssistantError propagate."""
    btn = button_mod.HyxiModeButton(mock_coordinator_fixture, "SN123", {}, "discharge")
    btn.hass = MagicMock()

    with patch(
        "custom_components.hyxi_cloud.button.async_send_battery_mode",
        new_callable=AsyncMock,
        side_effect=button_mod.HomeAssistantError("boom"),
    ) as send:
        with pytest.raises(button_mod.HomeAssistantError, match="boom"):
            await btn.async_press()

    send.assert_awaited_once_with(
        btn.hass, mock_coordinator_fixture, "SN123", "discharge"
    )


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
    error = button_mod.HyxiApiClient.ControlError("Fail")
    mock_coordinator_fixture.client.set_peak_shaving.side_effect = error
    btn = button_mod.HyxiPeakShavingButton(
        mock_coordinator_fixture, "SN123", {}, "hold"
    )

    with patch.object(button_mod, "_LOGGER") as mock_logger:
        with pytest.raises(button_mod.HomeAssistantError, match="peak shaving 'hold'"):
            await btn.async_press()
        mock_logger.exception.assert_called_once_with(
            "Failed to send peak shaving '%s' to %s: %s",
            "hold",
            button_mod.mask_sn("SN123"),
            error,
        )


@pytest.mark.asyncio
async def test_clear_alarms_button_press(mock_coordinator_fixture):
    """Test pressing the clear alarms button with active alarms."""
    # Mock some alarms in data
    mock_coordinator_fixture.data = {
        "SN123": {
            "alarms": [
                {"id": 44733168, "alarmState": 2, "alarmName": "Grid failure"},
                {"alarmId": 44733169, "alarmstate": 1, "alarmName": "Battery failure"},
                {
                    "id": 44733170,
                    "alarmState": 3,
                    "alarmName": "Recovered/Acknowledged",
                },  # not active
                {"id": 44733171, "alarmState": "0", "alarmName": "Active String"},
                {
                    "id": 44733172,
                    "alarmState": 2,
                    "alarmName": "Grid failure resolved",
                    "endTime": 1779374715000,
                },  # resolved (has endTime), should not be cleared
            ]
        }
    }
    btn = button_mod.HyxiClearAlarmsButton(mock_coordinator_fixture, "SN123", {})

    await btn.async_press()

    mock_coordinator_fixture.client.alter_alarm.assert_called_once_with(
        [44733168, 44733169, 44733171]
    )
    mock_coordinator_fixture.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_clear_alarms_button_press_no_alarms(mock_coordinator_fixture):
    """Test pressing clear alarms button with no active alarms."""
    mock_coordinator_fixture.data = {
        "SN123": {
            "alarms": [
                {
                    "id": 44733170,
                    "alarmState": 3,
                    "alarmName": "Recovered/Acknowledged",
                },
            ]
        }
    }
    btn = button_mod.HyxiClearAlarmsButton(mock_coordinator_fixture, "SN123", {})

    await btn.async_press()

    mock_coordinator_fixture.client.alter_alarm.assert_not_called()
    mock_coordinator_fixture.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_clear_alarms_button_error(mock_coordinator_fixture):
    """Test error handling in clear alarms button press."""
    mock_coordinator_fixture.data = {
        "SN123": {
            "alarms": [
                {"id": 44733168, "alarmState": 2, "alarmName": "Grid failure"},
            ]
        }
    }
    mock_coordinator_fixture.client.alter_alarm.side_effect = (
        button_mod.HyxiApiClient.ControlError("API Failure")
    )
    btn = button_mod.HyxiClearAlarmsButton(mock_coordinator_fixture, "SN123", {})

    with pytest.raises(button_mod.HomeAssistantError, match="clear active alarms"):
        await btn.async_press()


@pytest.mark.asyncio
async def test_clear_alarms_button_skips_non_integer_alarm_id(mock_coordinator_fixture):
    """Test an alarm with a non-integer id is logged and skipped, not fatal."""
    mock_coordinator_fixture.data = {
        "SN123": {
            "alarms": [
                {"id": 44733168, "alarmState": 2, "alarmName": "Grid failure"},
                {"id": "not-an-int", "alarmState": 1, "alarmName": "Bad id"},
            ]
        }
    }
    btn = button_mod.HyxiClearAlarmsButton(mock_coordinator_fixture, "SN123", {})

    await btn.async_press()

    mock_coordinator_fixture.client.alter_alarm.assert_called_once_with([44733168])


@pytest.mark.asyncio
async def test_clear_alarms_button_skips_alarm_with_no_id(mock_coordinator_fixture):
    """Test an active alarm with no id/alarmId/alarmid field is skipped, not fatal."""
    mock_coordinator_fixture.data = {
        "SN123": {
            "alarms": [
                {"id": 44733168, "alarmState": 2, "alarmName": "Grid failure"},
                {"alarmState": 1, "alarmName": "No id at all"},
            ]
        }
    }
    btn = button_mod.HyxiClearAlarmsButton(mock_coordinator_fixture, "SN123", {})

    await btn.async_press()

    mock_coordinator_fixture.client.alter_alarm.assert_called_once_with([44733168])


def test_mode_button_available_delegates_to_super(mock_coordinator_fixture):
    """Test HyxiModeButton.available is a pure pass-through to super().available.

    Patches button_mod.CoordinatorEntity directly (the actual base class bound
    into HyxiModeButton at import time) rather than relying on a particular
    fake's default behavior, since which fake wins is import-order dependent
    across this suite's module-level sys.modules mocking.
    """
    btn = button_mod.HyxiModeButton(mock_coordinator_fixture, "SN123", {}, "idle")

    with patch.object(
        button_mod.CoordinatorEntity, "available", new_callable=PropertyMock
    ) as mock_available:
        mock_available.return_value = True
        assert btn.available is True

        mock_available.return_value = False
        assert btn.available is False


def test_peak_shaving_button_available_delegates_to_super(mock_coordinator_fixture):
    """Test HyxiPeakShavingButton.available is a pure pass-through to super().available."""
    btn = button_mod.HyxiPeakShavingButton(
        mock_coordinator_fixture, "SN123", {}, "hold"
    )

    with patch.object(
        button_mod.CoordinatorEntity, "available", new_callable=PropertyMock
    ) as mock_available:
        mock_available.return_value = True
        assert btn.available is True

        mock_available.return_value = False
        assert btn.available is False

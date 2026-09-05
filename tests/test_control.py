"""Tests for the shared battery-control primitives (control.py)."""

# pylint: disable=missing-function-docstring, wrong-import-position
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests import conftest


class _MockHomeAssistantError(Exception):
    """Stand-in for homeassistant.exceptions.HomeAssistantError."""


conftest.ensure_mock(
    "homeassistant.exceptions", {"HomeAssistantError": _MockHomeAssistantError}
)
conftest.ensure_mock("homeassistant.helpers.entity_registry")

from custom_components.hyxi_cloud import control as control_mod
from custom_components.hyxi_cloud.const import DOMAIN

HomeAssistantError = control_mod.HomeAssistantError
# Whatever the shared hyxi_cloud_api mock resolved ControlError to for this
# run -- a real exception class in the full suite (set by a sibling test
# module), a bare MagicMock in an isolated single-file run. async_send_battery
# _mode's ``except HyxiApiClient.ControlError`` needs it to be a real class, so
# skip the file rather than emit spurious TypeErrors when run alone (see
# memory/test_suite_invocation.md -- always run via the CI commands).
ControlError = control_mod.HyxiApiClient.ControlError
pytestmark = pytest.mark.skipif(
    not isinstance(ControlError, type),
    reason="run the full tests/ suite, not this file alone",
)


# ── _get_power_value ────────────────────────────────────────────────────


def test_get_power_value_valid_state():
    hass = MagicMock()
    registry = MagicMock()
    registry.async_get_entity_id.return_value = "number.hyxi_sn123_charge_power"

    with patch(
        "custom_components.hyxi_cloud.control.er.async_get", return_value=registry
    ):
        state = MagicMock()
        state.state = "3000.0"
        hass.states.get.return_value = state

        result = control_mod._get_power_value(hass, "SN123", "charge")

        registry.async_get_entity_id.assert_called_once_with(
            "number", DOMAIN, "hyxi_SN123_charge_power"
        )
        hass.states.get.assert_called_once_with("number.hyxi_sn123_charge_power")
        assert result == 3000


def test_get_power_value_entity_not_found():
    hass = MagicMock()
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None

    with patch(
        "custom_components.hyxi_cloud.control.er.async_get", return_value=registry
    ):
        assert control_mod._get_power_value(hass, "SN123", "charge") == 100


def test_get_power_value_invalid_state():
    hass = MagicMock()
    registry = MagicMock()
    registry.async_get_entity_id.return_value = "number.hyxi_sn123_charge_power"

    with patch(
        "custom_components.hyxi_cloud.control.er.async_get", return_value=registry
    ):
        state_unknown = MagicMock()
        state_unknown.state = "unknown"
        hass.states.get.return_value = state_unknown
        assert control_mod._get_power_value(hass, "SN123", "charge") == 100

        state_invalid = MagicMock()
        state_invalid.state = "abc"
        hass.states.get.return_value = state_invalid
        assert control_mod._get_power_value(hass, "SN123", "charge") == 100

        state_inf = MagicMock()
        state_inf.state = "inf"  # float() parses it, int() overflows
        hass.states.get.return_value = state_inf
        assert control_mod._get_power_value(hass, "SN123", "charge") == 100

        hass.states.get.return_value = None
        assert control_mod._get_power_value(hass, "SN123", "charge") == 100


# ── SOC-protection guards ──────────────────────────────────────────────


def test_block_manual_discharge_if_needed_raises_when_blocked():
    coordinator = MagicMock()
    controller = MagicMock()
    controller.should_block_manual_discharge.return_value = True
    coordinator.protection_controllers = {"SN123": controller}

    with pytest.raises(HomeAssistantError, match="SOC Minimum"):
        control_mod._block_manual_discharge_if_needed(coordinator, "SN123")


def test_block_manual_discharge_if_needed_allows_when_not_blocked():
    coordinator = MagicMock()
    controller = MagicMock()
    controller.should_block_manual_discharge.return_value = False
    coordinator.protection_controllers = {"SN123": controller}

    control_mod._block_manual_discharge_if_needed(coordinator, "SN123")  # no raise


def test_block_manual_discharge_if_needed_no_controller():
    coordinator = MagicMock()
    coordinator.protection_controllers = {}

    control_mod._block_manual_discharge_if_needed(coordinator, "SN123")  # no raise


def test_block_manual_charge_if_needed_raises_when_blocked():
    coordinator = MagicMock()
    controller = MagicMock()
    controller.should_block_manual_charge.return_value = True
    coordinator.protection_controllers = {"SN123": controller}

    with pytest.raises(HomeAssistantError, match="SOC Maximum"):
        control_mod._block_manual_charge_if_needed(coordinator, "SN123")


def test_block_manual_charge_if_needed_allows_when_not_blocked():
    coordinator = MagicMock()
    controller = MagicMock()
    controller.should_block_manual_charge.return_value = False
    coordinator.protection_controllers = {"SN123": controller}

    control_mod._block_manual_charge_if_needed(coordinator, "SN123")  # no raise


def test_block_manual_peak_shaving_if_needed_no_controller():
    coordinator = MagicMock()
    coordinator.protection_controllers = {}

    control_mod._block_manual_peak_shaving_if_needed(coordinator, "SN123", "discharge")


def test_block_manual_peak_shaving_if_needed_raises_for_discharge():
    coordinator = MagicMock()
    controller = MagicMock()
    controller.should_block_manual_discharge.return_value = True
    coordinator.protection_controllers = {"SN123": controller}

    with pytest.raises(HomeAssistantError, match="SOC Minimum"):
        control_mod._block_manual_peak_shaving_if_needed(
            coordinator, "SN123", "discharge"
        )


def test_block_manual_peak_shaving_if_needed_raises_for_charge():
    coordinator = MagicMock()
    controller = MagicMock()
    controller.should_block_manual_charge.return_value = True
    coordinator.protection_controllers = {"SN123": controller}

    with pytest.raises(HomeAssistantError, match="SOC Maximum"):
        control_mod._block_manual_peak_shaving_if_needed(coordinator, "SN123", "charge")


def test_block_manual_peak_shaving_if_needed_allows_other_options():
    coordinator = MagicMock()
    controller = MagicMock()
    controller.should_block_manual_discharge.return_value = True
    controller.should_block_manual_charge.return_value = True
    coordinator.protection_controllers = {"SN123": controller}

    control_mod._block_manual_peak_shaving_if_needed(coordinator, "SN123", "hold")
    control_mod._block_manual_peak_shaving_if_needed(coordinator, "SN123", "stop")
    control_mod._block_manual_peak_shaving_if_needed(coordinator, "SN123", "close")


def test_note_manual_mode():
    coordinator = MagicMock()
    controller = MagicMock()
    coordinator.protection_controllers = {"SN123": controller}

    control_mod._note_manual_mode(coordinator, "SN123", "test_mode")

    controller.note_manual_mode.assert_called_once_with("test_mode")


def test_note_manual_mode_no_controller():
    coordinator = MagicMock()
    coordinator.protection_controllers = {}

    control_mod._note_manual_mode(coordinator, "SN123", "test_mode")  # no raise


@pytest.mark.parametrize("mode", ["idle", "self_consume"])
def test_preflight_battery_mode_never_blocks_idle_or_self_consume(mode):
    coordinator = MagicMock()
    controller = MagicMock()
    controller.should_block_manual_charge.return_value = True
    controller.should_block_manual_discharge.return_value = True
    coordinator.protection_controllers = {"SN123": controller}

    control_mod.preflight_battery_mode(coordinator, "SN123", mode)  # no raise


@pytest.mark.parametrize(
    ("mode", "match"),
    [("charge", "SOC Maximum"), ("discharge", "SOC Minimum")],
)
def test_preflight_battery_mode_raises_the_soc_guard(mode, match):
    coordinator = MagicMock()
    controller = MagicMock()
    controller.should_block_manual_charge.return_value = True
    controller.should_block_manual_discharge.return_value = True
    coordinator.protection_controllers = {"SN123": controller}

    with pytest.raises(HomeAssistantError, match=match):
        control_mod.preflight_battery_mode(coordinator, "SN123", mode)


# ── async_send_battery_mode ────────────────────────────────────────────


@pytest.fixture
def coord():
    coordinator = MagicMock()
    coordinator.protection_controllers = {}
    coordinator.client = MagicMock()
    coordinator.client.set_mode_idle = AsyncMock()
    coordinator.client.set_mode_charge = AsyncMock()
    coordinator.client.set_mode_discharge = AsyncMock()
    coordinator.client.set_mode_self_consume = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.mark.asyncio
async def test_send_battery_mode_idle_and_self_consume(coord):
    await control_mod.async_send_battery_mode(MagicMock(), coord, "SN123", "idle")
    coord.client.set_mode_idle.assert_awaited_once_with("SN123")

    await control_mod.async_send_battery_mode(
        MagicMock(), coord, "SN123", "self_consume"
    )
    coord.client.set_mode_self_consume.assert_awaited_once_with("SN123")
    assert coord.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_send_battery_mode_explicit_power_skips_the_number_lookup(coord):
    with patch("custom_components.hyxi_cloud.control._get_power_value") as fallback:
        await control_mod.async_send_battery_mode(
            MagicMock(), coord, "SN123", "charge", power=2500
        )
    coord.client.set_mode_charge.assert_awaited_once_with("SN123", 2500)
    fallback.assert_not_called()


@pytest.mark.asyncio
async def test_send_battery_mode_falls_back_to_the_power_number(coord):
    with patch(
        "custom_components.hyxi_cloud.control._get_power_value", return_value=1234
    ):
        await control_mod.async_send_battery_mode(
            MagicMock(), coord, "SN123", "discharge"
        )
    coord.client.set_mode_discharge.assert_awaited_once_with("SN123", 1234)


@pytest.mark.asyncio
async def test_send_battery_mode_blocks_charge_at_soc_max(coord):
    controller = MagicMock()
    controller.should_block_manual_charge.return_value = True
    coord.protection_controllers = {"SN123": controller}

    send = control_mod.async_send_battery_mode(
        MagicMock(), coord, "SN123", "charge", power=1000
    )
    with pytest.raises(HomeAssistantError, match="SOC Maximum"):
        await send
    coord.client.set_mode_charge.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_battery_mode_wraps_a_control_error(coord):
    coord.client.set_mode_idle.side_effect = ControlError("bus down")

    send = control_mod.async_send_battery_mode(MagicMock(), coord, "SN123", "idle")
    with pytest.raises(HomeAssistantError, match="Failed to set mode 'idle'"):
        await send

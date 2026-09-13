"""Tests for the shared battery-control primitives (control.py)."""

# pylint: disable=missing-function-docstring, wrong-import-position
from unittest.mock import ANY, AsyncMock, MagicMock, patch

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

    controller.note_manual_mode.assert_called_once_with("test_mode", None)


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

    hass = MagicMock()
    with pytest.raises(HomeAssistantError, match="SOC Maximum"):
        await control_mod.async_send_battery_mode(
            hass, coord, "SN123", "charge", power=1000
        )
    coord.client.set_mode_charge.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_battery_mode_wraps_a_control_error(coord):
    coord.client.set_mode_idle.side_effect = ControlError("bus down")

    hass = MagicMock()
    with pytest.raises(HomeAssistantError, match="Failed to set mode 'idle'"):
        await control_mod.async_send_battery_mode(hass, coord, "SN123", "idle")


# ── Control-result verification ─────────────────────────────────────────
#
# async_send_battery_mode's Cloud write is fire-and-forget: HYXI's
# response only confirms the cloud accepted it, not that the device
# applied it. The actual polling is shared (control_verify, tested in
# test_control_verify.py); these tests cover control.py's own wiring:
# scheduling, and what a confirmed rejection should correct.

_CONTROL_RESPONSE = {
    "success": True,
    "data": [{"traceId": "123456789", "deviceSn": "SN123"}],
}


@pytest.mark.asyncio
async def test_send_battery_mode_schedules_verification_on_success(coord):
    coord.client.set_mode_idle.return_value = _CONTROL_RESPONSE
    hass = MagicMock()
    # async_create_background_task's target is a real coroutine here
    # (unlike the rest of this file's bare MagicMock hass/entry) -- close
    # it so it doesn't leave a "coroutine was never awaited" warning
    # behind, since nothing actually runs it in this test.
    coord.entry.async_create_background_task = MagicMock(
        side_effect=lambda hass, coro, name: coro.close()
    )

    await control_mod.async_send_battery_mode(hass, coord, "SN123", "idle")

    coord.entry.async_create_background_task.assert_called_once()


@pytest.mark.asyncio
async def test_send_battery_mode_skips_verification_for_modbus(coord):
    coord.entry = MagicMock(data={"transport": "modbus"})
    coord.client.set_mode_idle.return_value = _CONTROL_RESPONSE
    hass = MagicMock()

    await control_mod.async_send_battery_mode(hass, coord, "SN123", "idle")

    coord.entry.async_create_background_task.assert_not_called()


@pytest.mark.asyncio
async def test_send_battery_mode_skips_verification_for_a_skipped_traceid(coord):
    """Regression test: observed live against a device under active
    third-party (energy-provider) VPP dispatch, HYXI returned traceId:
    "SKIPPED" rather than a real, pollable one -- must not schedule a
    verification task for it (control_verify.extract_trace_id already
    rejects it; this proves the rejection reaches all the way through
    the real async_send_battery_mode call path)."""
    coord.client.set_mode_idle.return_value = {
        "success": True,
        "data": [{"traceId": "SKIPPED", "deviceSn": "SN123"}],
    }
    hass = MagicMock()

    await control_mod.async_send_battery_mode(hass, coord, "SN123", "idle")

    coord.entry.async_create_background_task.assert_not_called()


def test_maybe_verify_control_result_skips_when_no_trace_id():
    coordinator = MagicMock()
    coordinator.entry = MagicMock(data={"transport": "cloud"})
    hass = MagicMock()

    control_mod._maybe_verify_control_result(hass, coordinator, "SN123", "idle", None)

    coordinator.entry.async_create_background_task.assert_not_called()


def test_maybe_verify_control_result_ties_the_task_to_the_config_entry(coord):
    """Regression test: a manual mode command has no persistent controller
    of its own to track/cancel its verify task on unload (unlike
    protection.py/engine.py) -- it must tie into ConfigEntry.
    async_create_background_task instead, which HA already cancels
    automatically on unload/reload, rather than firing a bare
    hass.async_create_task that would keep running (and could invoke
    _on_verify_result against a torn-down coordinator) past unload."""
    hass = MagicMock()
    coord.entry.async_create_background_task = MagicMock(
        side_effect=lambda hass, coro, name: coro.close()
    )

    control_mod._maybe_verify_control_result(hass, coord, "SN123", "idle", "123456789")

    coord.entry.async_create_background_task.assert_called_once_with(
        hass, ANY, "hyxi_control_verify"
    )


def test_on_verify_result_failure_delegates_without_its_own_duplicate_log(caplog):
    """When a protection controller exists, it owns logging the rejection
    (via note_manual_mode_rejected -> _handle_device_rejected) -- this
    must not also log its own warning for the same event."""
    coordinator = MagicMock()
    controller = MagicMock()
    coordinator.protection_controllers = {"SN123": controller}

    with caplog.at_level("WARNING", logger="custom_components.hyxi_cloud.control"):
        control_mod._on_verify_result(
            coordinator,
            "SN123",
            "idle",
            "TRACE123",
            control_mod.control_verify.RESULT_FAILURE,
        )

    controller.note_manual_mode_rejected.assert_called_once_with("idle", "TRACE123")
    assert caplog.text == ""


def test_on_verify_result_success_and_none_are_no_ops():
    coordinator = MagicMock()
    controller = MagicMock()
    coordinator.protection_controllers = {"SN123": controller}

    control_mod._on_verify_result(
        coordinator,
        "SN123",
        "idle",
        "TRACE123",
        control_mod.control_verify.RESULT_SUCCESS,
    )
    control_mod._on_verify_result(coordinator, "SN123", "idle", "TRACE123", None)

    controller.note_manual_mode_rejected.assert_not_called()


def test_on_verify_result_failure_with_no_controller_logs_its_own_warning(caplog):
    """With no protection controller to own the log, this is the only
    place the rejection would ever be recorded, so it logs it directly."""
    coordinator = MagicMock()
    coordinator.protection_controllers = {}

    with caplog.at_level("WARNING", logger="custom_components.hyxi_cloud.control"):
        control_mod._on_verify_result(
            coordinator,
            "SN123",
            "idle",
            "TRACE123",
            control_mod.control_verify.RESULT_FAILURE,
        )

    assert "rejected by the device" in caplog.text

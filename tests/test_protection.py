"""Tests for minimal battery protection logic."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hyxi_cloud import control_verify
from custom_components.hyxi_cloud.protection import HyxiBatteryProtectionController


class FakeCoordinator:
    """Minimal coordinator stub for protection tests."""

    def __init__(
        self, soc: float, model: str = "H5K-HT", transport: str = "cloud"
    ) -> None:
        self.data = {
            "SN123": {
                "model": model,
                "metrics": {"batSoc": soc},
            }
        }
        self.client = SimpleNamespace(
            set_mode_idle=AsyncMock(),
            set_mode_charge=AsyncMock(),
            set_mode_discharge=AsyncMock(),
            set_mode_self_consume=AsyncMock(),
            set_peak_shaving=AsyncMock(),
            query_control_result=AsyncMock(),
        )
        self.async_request_refresh = AsyncMock()
        # A real coordinator always has .entry; default matches every
        # existing test in this file, which was written against the cloud
        # phase-based routing these fakes predate.
        self.entry = SimpleNamespace(data={"transport": transport})

    def async_add_listener(self, listener):
        """Return a no-op unsubscribe callback."""
        return lambda: None


def _fake_async_create_task(coro):
    """Stand in for HomeAssistant.async_create_task.

    Existing tests assert on synchronous side effects and don't need the
    coroutine to actually run, so this doesn't schedule it -- but it does
    close it, so a background task the code under test fires (e.g.
    protection's control-result verification) doesn't leave a "coroutine
    was never awaited" RuntimeWarning behind.
    """
    coro.close()
    return MagicMock()


def _build_controller(
    soc: float, model: str = "H5K-HT", transport: str = "cloud"
) -> HyxiBatteryProtectionController:
    """Create a controller with parameter lookups stubbed."""
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=_fake_async_create_task)
    controller = HyxiBatteryProtectionController(
        hass, FakeCoordinator(soc, model, transport), "SN123"
    )

    def get_param(key, default):
        return {
            "soc_min": 20,
            "soc_max": 90,
            "soc_min_hysteresis_pct": 2,
            "soc_max_hysteresis_pct": 2,
        }.get(key, default)

    controller._get_param = get_param  # type: ignore[method-assign]
    controller._ensure_mode = AsyncMock()  # type: ignore[method-assign]
    return controller


# --- Low SOC Protection ---


@pytest.mark.asyncio
async def test_low_soc_triggers_idle_hold():
    """SOC at or below the minimum should force idle."""
    controller = _build_controller(20)

    await controller.async_evaluate()

    assert controller._low_soc_hold is True
    controller._ensure_mode.assert_awaited_once_with("idle")


@pytest.mark.asyncio
async def test_low_soc_allows_manual_charge_recovery():
    """Low-SOC protection should not block a user charging the battery back up."""
    controller = _build_controller(20)
    controller.note_manual_mode("charge")

    await controller.async_evaluate()

    assert controller._low_soc_hold is True
    controller._ensure_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_low_soc_hysteresis_keeps_idle_until_resume():
    """Low-SOC hold should remain active until the resume threshold is crossed."""
    controller = _build_controller(21)
    controller._low_soc_hold = True

    await controller.async_evaluate()

    assert controller._low_soc_hold is True
    controller._ensure_mode.assert_awaited_once_with("idle")


@pytest.mark.asyncio
async def test_low_soc_hysteresis_clears_above_resume():
    """Crossing soc_min + hysteresis should release the low-SOC hold."""
    controller = _build_controller(23)
    controller._low_soc_hold = True

    await controller.async_evaluate()

    assert controller._low_soc_hold is False
    controller._ensure_mode.assert_not_awaited()


# --- High SOC Protection ---


@pytest.mark.asyncio
async def test_soc_max_stops_tracked_charge_mode():
    """A tracked charge mode should be forced to idle when SOC reaches the max."""
    controller = _build_controller(90)
    controller.note_manual_mode("charge")

    await controller.async_evaluate()

    assert controller._high_soc_hold is True
    controller._ensure_mode.assert_awaited_once_with("idle")


@pytest.mark.asyncio
async def test_soc_max_ignores_unknown_mode():
    """No extra command is needed when the inverter is already idle at soc_max."""
    controller = _build_controller(90)
    controller.note_manual_mode("idle")

    await controller.async_evaluate()

    controller._ensure_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_high_soc_hysteresis_keeps_charge_paths_blocked():
    """Three-phase upper hold should stay active without blocking self-consume."""
    controller = _build_controller(89)
    controller._high_soc_hold = True
    controller.note_manual_mode("self_consume")

    await controller.async_evaluate()

    assert controller._high_soc_hold is True
    controller._ensure_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_high_soc_hysteresis_clears_below_resume():
    """Upper hold should clear once SOC drops to the release threshold."""
    controller = _build_controller(88)
    controller._high_soc_hold = True
    controller.note_manual_mode("idle")

    await controller.async_evaluate()

    assert controller._high_soc_hold is False
    controller._ensure_mode.assert_not_awaited()


# --- Mode Restore ---


def test_restore_last_idle_mode():
    """Restoring an idle state should set last_sent_mode without sending command."""
    controller = _build_controller(50)

    controller.restore_last_sent_mode("idle")

    controller._coordinator.client.set_mode_idle.assert_not_called()
    assert controller.last_sent_mode == "idle"


def test_restore_last_charge_mode():
    """Restoring charge should set last_sent_mode without sending command."""
    controller = _build_controller(50)

    controller.restore_last_sent_mode("charge")

    controller._coordinator.client.set_mode_charge.assert_not_called()
    assert controller.last_sent_mode == "charge"


# --- Manual Blocking ---


def test_manual_discharge_blocked_at_soc_min():
    """Manual discharge should be blocked at or below the configured floor."""
    controller = _build_controller(20)

    assert controller.should_block_manual_discharge() is True


def test_manual_discharge_allowed_above_soc_min():
    """Manual discharge should remain allowed above the configured floor."""
    controller = _build_controller(21)

    assert controller.should_block_manual_discharge() is False


def test_manual_charge_blocked_at_soc_max():
    """Manual charge should be blocked once SOC reaches the upper limit."""
    controller = _build_controller(90)

    assert controller.should_block_manual_charge() is True


def test_manual_charge_allowed_below_upper_release_threshold():
    """Manual charge should be allowed again after the upper hysteresis releases."""
    controller = _build_controller(88)
    controller._high_soc_hold = True

    assert controller.should_block_manual_charge() is False


def test_manual_charge_blocked_above_release_threshold():
    """Manual charge should still be blocked between soc_max and upper hysteresis."""
    controller = _build_controller(89)
    controller._high_soc_hold = True

    assert controller.should_block_manual_charge() is True


# --- Single-Phase Behavior ---


@pytest.mark.asyncio
async def test_single_phase_low_soc_forces_hold():
    """Single-phase low-SOC protection should force hold rather than idle."""
    controller = _build_controller(20, "H5K-HS")

    await controller.async_evaluate()

    assert controller._low_soc_hold is True
    controller._ensure_mode.assert_awaited_once_with("hold")


@pytest.mark.asyncio
async def test_single_phase_high_soc_keeps_hold_allowed():
    """Single-phase upper hold should allow hold without forcing another action."""
    controller = _build_controller(89, "H5K-HS")
    controller._high_soc_hold = True
    controller.note_manual_mode("hold")

    await controller.async_evaluate()

    assert controller._high_soc_hold is True
    controller._ensure_mode.assert_not_awaited()


def test_single_phase_restore_hold_action():
    """Single-phase restore should set last_sent_mode without sending command."""
    controller = _build_controller(50, "H5K-HS")

    controller.restore_last_sent_mode("hold")

    controller._coordinator.client.set_peak_shaving.assert_not_called()
    assert controller.last_sent_mode == "hold"


@pytest.mark.asyncio
async def test_proactive_state_restore_on_start():
    """Test that async_start proactively restores last_sent_mode from HASS state registry."""
    controller = _build_controller(50)

    mock_state = SimpleNamespace(state="charge")
    controller._hass.states = SimpleNamespace(get=MagicMock(return_value=mock_state))

    await controller.async_start()

    controller._hass.states.get.assert_called_once_with(
        "sensor.hyxi_SN123_last_sent_mode"
    )
    assert controller.last_sent_mode == "charge"


# --- Coverage Extensions ---


@pytest.mark.asyncio
async def test_async_start_already_running():
    """Verify async_start returns early if unsub_listener is already set."""
    controller = _build_controller(50)
    controller._unsub_listener = MagicMock()

    # If it didn't return early, it would try to query hass states and register listener,
    # raising errors since states/coordinator aren't fully configured.
    await controller.async_start()
    assert controller._unsub_listener is not None


@pytest.mark.asyncio
async def test_async_stop_cancels_evaluation_task():
    """Verify stop() unsubscribes and cancels the running evaluation task,
    and cancels any pending control-result verification tasks too."""
    controller = _build_controller(50)
    mock_unsub = MagicMock()
    controller._unsub_listener = mock_unsub

    mock_task = MagicMock()
    mock_task.done.return_value = False
    controller._eval_task = mock_task

    pending_verify = MagicMock()
    pending_verify.done.return_value = False
    finished_verify = MagicMock()
    finished_verify.done.return_value = True
    controller._verify_tasks = {pending_verify, finished_verify}

    controller.stop()

    mock_unsub.assert_called_once()
    assert controller._unsub_listener is None
    mock_task.cancel.assert_called_once()
    assert controller._eval_task is None
    pending_verify.cancel.assert_called_once()
    finished_verify.cancel.assert_not_called()
    assert controller._verify_tasks == set()


def test_note_manual_mode():
    """Verify note_manual_mode correctly updates the last sent mode attribute."""
    controller = _build_controller(50)
    assert controller.last_sent_mode is None

    controller.note_manual_mode("charge")
    assert controller.last_sent_mode == "charge"

    controller.note_manual_mode("idle")
    assert controller.last_sent_mode == "idle"


def test_restore_last_sent_mode_invalid():
    """Verify restore_last_sent_mode ignores invalid/unsupported modes."""
    controller = _build_controller(50)
    controller._last_sent_mode = "charge"

    controller.restore_last_sent_mode("invalid_mode_name")
    assert controller.last_sent_mode == "charge"


def test_should_block_discharge_charge_when_soc_is_none():
    """Verify should_block_manual_discharge/charge return False if SOC is None."""
    controller = _build_controller(50)
    controller._coordinator.data = {}  # Empty to force SOC = None

    assert controller.should_block_manual_discharge() is False
    assert controller.should_block_manual_charge() is False


@pytest.mark.asyncio
async def test_handle_coordinator_update_cancels_running_task():
    """Verify _handle_coordinator_update cancels previous tasks before launching new ones."""
    controller = _build_controller(50)
    mock_task = MagicMock()
    mock_task.done.return_value = False
    controller._eval_task = mock_task

    mock_hass = MagicMock()
    controller._hass = mock_hass

    controller._handle_coordinator_update()

    mock_task.cancel.assert_called_once()
    mock_hass.async_create_task.assert_called_once()


@pytest.mark.asyncio
async def test_async_evaluate_missing_dev_data_or_metrics_or_soc():
    """Verify async_evaluate handles missing data gracefully."""
    controller = _build_controller(50)

    # 1. No dev_data
    controller._coordinator.data = {}
    await controller.async_evaluate()
    assert controller._low_soc_hold is False

    # 2. No metrics
    controller._coordinator.data = {"SN123": {}}
    await controller.async_evaluate()
    assert controller._low_soc_hold is False

    # 3. SOC is None
    controller._coordinator.data = {"SN123": {"metrics": {"batSoc": None}}}
    await controller.async_evaluate()
    assert controller._low_soc_hold is False


@pytest.mark.asyncio
async def test_single_phase_high_soc_evaluation():
    """Verify single-phase high SOC protection transitions to hold."""
    controller = _build_controller(95, "H5K-HS")
    controller._ensure_mode = AsyncMock()  # Override the mock from _build_controller

    # High SOC (>= 90) on single phase should force hold if not already discharge/hold
    controller.note_manual_mode("charge")
    await controller.async_evaluate()
    assert controller._high_soc_hold is True
    controller._ensure_mode.assert_awaited_once_with("hold")


@pytest.mark.asyncio
async def test_single_phase_high_soc_hold_maintains_hold():
    """Verify single-phase high SOC hold maintains hold until release threshold."""
    controller = _build_controller(89, "H5K-HS")
    controller._high_soc_hold = True
    controller._ensure_mode = AsyncMock()

    # SOC is 89 (above resume threshold 90 - 2 = 88)
    controller.note_manual_mode("charge")
    await controller.async_evaluate()
    assert controller._high_soc_hold is True
    controller._ensure_mode.assert_awaited_once_with("hold")


@pytest.mark.asyncio
async def test_three_phase_high_soc_hold_maintains_idle():
    """Verify three-phase high SOC hold maintains idle until release threshold."""
    controller = _build_controller(89, "H5K-HT")
    controller._high_soc_hold = True
    controller._ensure_mode = AsyncMock()

    # SOC is 89 (above resume threshold 90 - 2 = 88)
    controller.note_manual_mode("charge")
    await controller.async_evaluate()
    assert controller._high_soc_hold is True
    controller._ensure_mode.assert_awaited_once_with("idle")


@pytest.mark.asyncio
async def test_ensure_mode_cooldown():
    """Verify _ensure_mode respects cooldown and does not send repeated commands."""
    controller = _build_controller(50)
    controller._ensure_mode = HyxiBatteryProtectionController._ensure_mode.__get__(
        controller, HyxiBatteryProtectionController
    )
    controller._send_control = AsyncMock()
    controller._last_sent_mode = "idle"

    # Mode is same: early return
    await controller._ensure_mode("idle")
    controller._send_control.assert_not_called()

    # Cooldown active: early return
    import time

    controller._last_mode_switch = time.monotonic()
    await controller._ensure_mode("charge")
    controller._send_control.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_mode_three_phase_actions():
    """Verify _ensure_mode sends correct three-phase command."""
    controller = _build_controller(50, "H5K-HT")
    controller._ensure_mode = HyxiBatteryProtectionController._ensure_mode.__get__(
        controller, HyxiBatteryProtectionController
    )

    # 1. idle
    await controller._ensure_mode("idle")
    controller._coordinator.client.set_mode_idle.assert_awaited_once_with("SN123")

    # 2. charge
    controller._last_mode_switch = -999999.0  # bypass cooldown
    await controller._ensure_mode("charge")
    controller._coordinator.client.set_mode_charge.assert_awaited_once()

    # 3. discharge
    controller._last_mode_switch = -999999.0
    await controller._ensure_mode("discharge")
    controller._coordinator.client.set_mode_discharge.assert_awaited_once()

    # 4. self_consume
    controller._last_mode_switch = -999999.0
    await controller._ensure_mode("self_consume")
    controller._coordinator.client.set_mode_self_consume.assert_awaited_once_with(
        "SN123"
    )


@pytest.mark.asyncio
async def test_ensure_mode_single_phase_actions():
    """Verify _ensure_mode sends correct single-phase command."""
    controller = _build_controller(50, "H5K-HS")
    controller._ensure_mode = HyxiBatteryProtectionController._ensure_mode.__get__(
        controller, HyxiBatteryProtectionController
    )

    await controller._ensure_mode("hold")
    controller._coordinator.client.set_peak_shaving.assert_awaited_once_with(
        "SN123", "hold"
    )


@pytest.mark.asyncio
async def test_ensure_mode_modbus_actions_override_single_phase_model():
    """A Modbus entry must use the mode surface even on a model name that
    would route to peak-shaving over the cloud -- proving _uses_mode_control
    checks transport before phase, not the other way around."""
    controller = _build_controller(50, "H5K-HS", transport="modbus")
    controller._ensure_mode = HyxiBatteryProtectionController._ensure_mode.__get__(
        controller, HyxiBatteryProtectionController
    )

    await controller._ensure_mode("idle")
    controller._coordinator.client.set_mode_idle.assert_awaited_once_with("SN123")
    controller._coordinator.client.set_peak_shaving.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_mode_swallows_a_rejected_control_write(caplog):
    """A ControlError from the client (the inverter rejected the write --
    commonly because it's under external VPP control) is logged, not raised
    as an unhandled task exception, and does not count as a mode change.
    The first failure logs at WARNING, repeats drop to DEBUG.
    """
    import logging

    from custom_components.hyxi_cloud import protection as protection_mod

    class _ControlError(Exception):
        pass

    controller = _build_controller(50, "H5K-HT")
    controller._ensure_mode = HyxiBatteryProtectionController._ensure_mode.__get__(
        controller, HyxiBatteryProtectionController
    )
    controller._send_control = AsyncMock(side_effect=_ControlError("write rejected"))

    with patch.object(protection_mod.HyxiApiClient, "ControlError", _ControlError):
        caplog.set_level(
            logging.DEBUG, logger="custom_components.hyxi_cloud.protection"
        )

        await controller._ensure_mode("idle")  # must not raise

        assert "could not set mode 'idle'" in caplog.text
        assert controller._last_sent_mode is None  # not treated as sent
        assert [r.levelno for r in caplog.records if "could not set" in r.message] == [
            logging.WARNING
        ]

        caplog.clear()
        controller._last_mode_switch = -999999.0  # bypass the retry cooldown
        await controller._ensure_mode("idle")  # still fails, quietly now

        assert [r.levelno for r in caplog.records if "could not set" in r.message] == [
            logging.DEBUG
        ]


@pytest.mark.asyncio
async def test_ensure_mode_permission_denied_gets_distinct_guidance(caplog):
    """A ControlError carrying HYXI's B003026 permission code (the API
    itself rejecting the write as unauthorized) is logged with guidance
    naming that distinction, not the generic 'inverter may be under
    external control' text -- and switching between the two kinds of
    rejection logs at WARNING again each time, rather than being silently
    downgraded to DEBUG because *some* rejection was already logged once.
    """
    import logging

    from custom_components.hyxi_cloud import protection as protection_mod

    class _ControlError(Exception):
        pass

    controller = _build_controller(50, "H5K-HT")
    controller._ensure_mode = HyxiBatteryProtectionController._ensure_mode.__get__(
        controller, HyxiBatteryProtectionController
    )
    permission_denied = _ControlError(
        "request failed (code=B003026): The current application does "
        "not have permission to call this API."
    )
    external_control = _ControlError("request failed (code=B003099): busy")

    with patch.object(protection_mod.HyxiApiClient, "ControlError", _ControlError):
        caplog.set_level(
            logging.DEBUG, logger="custom_components.hyxi_cloud.protection"
        )

        controller._send_control = AsyncMock(side_effect=permission_denied)
        await controller._ensure_mode("idle")  # must not raise

        assert "rejected the write as unauthorized" in caplog.text
        assert "under external control" not in caplog.text
        assert [r.levelno for r in caplog.records if "could not set" in r.message] == [
            logging.WARNING
        ]

        # Same kind again (cooldown bypassed): drops to DEBUG.
        caplog.clear()
        controller._last_mode_switch = -999999.0
        await controller._ensure_mode("idle")
        assert [r.levelno for r in caplog.records if "could not set" in r.message] == [
            logging.DEBUG
        ]

        # A *different* kind of rejection is new, actionable information,
        # so it's WARNING again rather than staying suppressed at DEBUG.
        caplog.clear()
        controller._send_control = AsyncMock(side_effect=external_control)
        controller._last_mode_switch = -999999.0
        await controller._ensure_mode("idle")

        assert "under external control" in caplog.text
        assert "rejected the write as unauthorized" not in caplog.text
        assert [r.levelno for r in caplog.records if "could not set" in r.message] == [
            logging.WARNING
        ]


@pytest.mark.asyncio
async def test_ensure_mode_permission_denied_requires_exact_code_boundary(caplog):
    """A code that merely starts with the same digits as B003026 (e.g. a
    hypothetical B0030261), or any other text that quotes the digits
    without HYXI's own "(code=B003026)" formatting, must not be mistaken
    for the permission-denied response code -- it should fall back to the
    generic external-control guidance instead.
    """
    import logging

    from custom_components.hyxi_cloud import protection as protection_mod

    class _ControlError(Exception):
        pass

    controller = _build_controller(50, "H5K-HT")
    controller._ensure_mode = HyxiBatteryProtectionController._ensure_mode.__get__(
        controller, HyxiBatteryProtectionController
    )
    lookalike_code = _ControlError(
        "request failed (code=B0030261): unrelated business rule"
    )

    with patch.object(protection_mod.HyxiApiClient, "ControlError", _ControlError):
        caplog.set_level(
            logging.DEBUG, logger="custom_components.hyxi_cloud.protection"
        )

        controller._send_control = AsyncMock(side_effect=lookalike_code)
        await controller._ensure_mode("idle")  # must not raise

        assert "under external control" in caplog.text
        assert "rejected the write as unauthorized" not in caplog.text


@pytest.mark.asyncio
async def test_ensure_mode_control_error_on_modbus_never_gets_cloud_guidance(caplog):
    """The B003026 guidance is a HYXI Cloud API response code and has no
    meaning for a local Modbus entry, so even an error text that happens
    to contain that substring must still fall back to the generic
    external-control guidance on a Modbus transport.
    """
    import logging

    from custom_components.hyxi_cloud import protection as protection_mod

    class _ControlError(Exception):
        pass

    controller = _build_controller(50, "H5K-HT", transport="modbus")
    controller._ensure_mode = HyxiBatteryProtectionController._ensure_mode.__get__(
        controller, HyxiBatteryProtectionController
    )
    controller._send_control = AsyncMock(
        side_effect=_ControlError("Modbus write failed: code=B003026 coincidence")
    )

    with patch.object(protection_mod.HyxiApiClient, "ControlError", _ControlError):
        caplog.set_level(
            logging.DEBUG, logger="custom_components.hyxi_cloud.protection"
        )

        await controller._ensure_mode("idle")  # must not raise

        assert "under external control" in caplog.text
        assert "rejected the write as unauthorized" not in caplog.text


@pytest.mark.asyncio
async def test_send_control_three_phase_exceptions():
    """Verify three-phase send_control raises ValueError for unsupported modes."""
    controller = _build_controller(50, "H5K-HT")

    with pytest.raises(ValueError) as exc:
        await controller._send_control("unsupported_mode")
    assert "Unsupported three-phase protection mode" in str(exc.value)


@pytest.mark.asyncio
async def test_send_control_single_phase_exceptions():
    """Verify single-phase send_control raises ValueError for unsupported modes."""
    controller = _build_controller(50, "H5K-HS")

    with pytest.raises(ValueError) as exc:
        await controller._send_control("unsupported_mode")
    assert "Unsupported single-phase protection mode" in str(exc.value)


@pytest.mark.asyncio
async def test_send_control_invalid_phase_type():
    """Verify _send_control raises ValueError for unsupported phase types."""
    controller = _build_controller(50)
    with patch.object(controller, "_phase_type", return_value="invalid_phase"):
        with pytest.raises(ValueError) as exc:
            await controller._send_control("idle")
        assert "Unsupported phase type for protection" in str(exc.value)


def test_get_param_registry_and_state_fallbacks():
    """Verify _get_param falls back to default on missing registry, state, or invalid format."""
    # Build a real controller (not stubbed get_param)
    hass = MagicMock()
    coordinator = FakeCoordinator(50)
    controller = HyxiBatteryProtectionController(hass, coordinator, "SN123")

    mock_registry = MagicMock()
    mock_registry.async_get_entity_id.return_value = None

    with patch(
        "custom_components.hyxi_cloud.protection.er.async_get",
        return_value=mock_registry,
    ):
        # 1. Registry returns None
        assert controller._get_param("soc_min", 20) == 20

        # 2. State is None
        mock_registry.async_get_entity_id.return_value = "number.hyxi_SN123_soc_min"
        hass.states.get.return_value = None
        assert controller._get_param("soc_min", 20) == 20

        # 3. State is unavailable
        mock_state = MagicMock()
        mock_state.state = "unavailable"
        hass.states.get.return_value = mock_state
        assert controller._get_param("soc_min", 20) == 20

        # 4. State is ValueError (not floatable)
        mock_state.state = "invalid_float"
        assert controller._get_param("soc_min", 20) == 20


def test_get_power_value_fallbacks():
    """Verify _get_power_value fallbacks on missing entities or unparsable values."""
    hass = MagicMock()
    coordinator = FakeCoordinator(50)
    controller = HyxiBatteryProtectionController(hass, coordinator, "SN123")

    mock_registry = MagicMock()
    mock_registry.async_get_entity_id.return_value = None

    with patch(
        "custom_components.hyxi_cloud.protection.er.async_get",
        return_value=mock_registry,
    ):
        # 1. Registry returns None
        assert controller._get_power_value("charge") == 100

        # 2. State is None
        mock_registry.async_get_entity_id.return_value = (
            "number.hyxi_SN123_charge_power"
        )
        hass.states.get.return_value = None
        assert controller._get_power_value("charge") == 100

        # 3. State is unavailable
        mock_state = MagicMock()
        mock_state.state = "unavailable"
        hass.states.get.return_value = mock_state
        assert controller._get_power_value("charge") == 100

        # 4. State is ValueError (not floatable)
        mock_state.state = "invalid_float"
        assert controller._get_power_value("charge") == 100

        # 5. Watts value is parsed but capped at minimum of 1
        mock_state.state = "-50.0"
        assert controller._get_power_value("charge") == 1


def test_metric_float_exceptions():
    """Verify _metric_float handles invalid types or None correctly."""
    assert HyxiBatteryProtectionController._metric_float(None) is None
    assert HyxiBatteryProtectionController._metric_float("invalid") is None
    assert HyxiBatteryProtectionController._metric_float([]) is None


# --- Control-result verification ---
#
# A Cloud set_device_control write is fire-and-forget: HYXI's response
# only confirms the cloud accepted it, not that the device applied it.
# The actual polling (control_verify.verify_control_result) and traceId
# extraction (control_verify.extract_trace_id) are shared with
# control.py/engine.py and tested directly in test_control_verify.py.
# These tests cover protection's own wiring on top of that: scheduling,
# interpreting the outcome (_on_verify_result), and correcting state on a
# confirmed rejection (_handle_device_rejected).

_CONTROL_RESPONSE = {
    "success": True,
    "data": [{"traceId": "TRACE123", "deviceSn": "SN123"}],
}


@pytest.mark.asyncio
async def test_maybe_verify_control_result_skips_modbus():
    """Verify Modbus writes never schedule a verification task -- there's
    no traceId/async-confirmation concept for a local register write."""
    controller = _build_controller(50, transport="modbus")

    with patch.object(controller._hass, "async_create_task") as mock_create:
        controller._maybe_verify_control_result("idle", "TRACE123")
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_verify_control_result_skips_when_no_trace_id():
    """Verify a None trace_id (no traceId extracted, or a Modbus write)
    schedules nothing."""
    controller = _build_controller(50)

    with patch.object(controller._hass, "async_create_task") as mock_create:
        controller._maybe_verify_control_result("idle", None)
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_verify_control_result_schedules_and_tracks_task():
    """Verify a real traceId schedules _verify_control_result as a tracked
    background task that discards itself once done."""
    controller = _build_controller(50)

    real_task = asyncio.ensure_future(asyncio.sleep(0))

    def _create_task(coro):
        # The real verify_control_result(...) coroutine built by
        # _maybe_verify_control_result is discarded in favor of
        # real_task below -- close it so it doesn't leave a "coroutine
        # was never awaited" warning behind.
        coro.close()
        return real_task

    with patch.object(
        controller._hass, "async_create_task", side_effect=_create_task
    ) as mock_create:
        controller._maybe_verify_control_result("idle", "TRACE123")

    mock_create.assert_called_once()
    assert real_task in controller._verify_tasks
    await asyncio.gather(real_task)  # let the done callback fire
    assert real_task not in controller._verify_tasks


@pytest.mark.asyncio
async def test_on_verify_result_success_clears_rejection_flag():
    """Verify a confirmed "3" (success) outcome for the currently-tracked
    trace_id clears any prior device-rejection flag, so a later,
    different failure warns again."""
    controller = _build_controller(50)
    controller._last_device_rejected_logged = True
    controller._last_sent_trace_id = "TRACE123"

    controller._on_verify_result("idle", "TRACE123", control_verify.RESULT_SUCCESS)

    assert controller._last_device_rejected_logged is False


async def test_on_verify_result_stale_success_does_not_clear_rejection_flag():
    """Verify a confirmed success for a trace_id that's no longer current
    (an older, superseded send resolving late) doesn't clear the flag --
    a newer send may still be genuinely rejected, and this must not reset
    that warning back to needing to fire again at WARNING."""
    controller = _build_controller(50)
    controller._last_device_rejected_logged = True
    controller._last_sent_trace_id = "NEWER_TRACE"  # a newer send is current

    controller._on_verify_result("idle", "OLD_TRACE", control_verify.RESULT_SUCCESS)

    assert controller._last_device_rejected_logged is True


@pytest.mark.asyncio
async def test_on_verify_result_failure_delegates_to_handle_device_rejected():
    """Verify a confirmed "6" (failure) outcome is handed to
    _handle_device_rejected with the same mode/trace_id."""
    controller = _build_controller(50)
    controller._handle_device_rejected = MagicMock()

    controller._on_verify_result("idle", "TRACE123", control_verify.RESULT_FAILURE)

    controller._handle_device_rejected.assert_called_once_with("idle", "TRACE123")


@pytest.mark.asyncio
async def test_on_verify_result_inconclusive_is_a_no_op():
    """Verify a None outcome (lookup failure, or still-issuing past the
    attempt budget -- already logged by verify_control_result itself)
    doesn't touch any state."""
    controller = _build_controller(50)
    controller._handle_device_rejected = MagicMock()
    controller._last_device_rejected_logged = True

    controller._on_verify_result("idle", "TRACE123", None)

    controller._handle_device_rejected.assert_not_called()
    assert controller._last_device_rejected_logged is True


def test_handle_device_rejected_first_time_warns_and_clears_mode(caplog):
    """Verify the first device-level rejection for the currently-tracked
    trace_id logs at WARNING, clears _last_sent_mode/_last_sent_trace_id
    so the next evaluation retries, and requests a coordinator refresh so
    a stale HyxiLastSentModeSensor and a delayed retry (otherwise waiting
    on the next unrelated Cloud poll, up to 5 minutes away) don't have to
    wait on an unrelated poll to happen to occur."""
    import logging

    controller = _build_controller(50)
    controller._last_sent_mode = "idle"
    controller._last_sent_trace_id = "TRACE123"

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.hyxi_cloud.protection"
    ):
        controller._handle_device_rejected("idle", "TRACE123")

    assert controller._last_device_rejected_logged is True
    assert controller._last_sent_mode is None
    assert controller._last_sent_trace_id is None
    assert [
        r.levelno for r in caplog.records if "rejected by the device" in r.message
    ] == [logging.WARNING]
    controller._coordinator.async_request_refresh.assert_called_once()


def test_handle_device_rejected_repeated_logs_debug():
    """Verify a repeated device-level rejection (already logged once)
    for the same current trace_id drops to DEBUG."""
    import logging

    controller = _build_controller(50)
    controller._last_device_rejected_logged = True
    controller._last_sent_mode = "idle"
    controller._last_sent_trace_id = "TRACE123"

    with patch.object(protection_module_logger(), "log") as mock_log:
        controller._handle_device_rejected("idle", "TRACE123")

    mock_log.assert_called_once()
    assert mock_log.call_args[0][0] == logging.DEBUG


def test_handle_device_rejected_leaves_superseded_send_alone(caplog):
    """Verify a rejection for a trace_id that's no longer the currently
    tracked one (superseded by a newer send, automatic or manual) does
    not clobber that newer state, and logs quietly rather than warning
    about a mode nothing relies on anymore."""
    import logging

    controller = _build_controller(50)
    controller._last_sent_mode = "charge"
    controller._last_sent_trace_id = "NEWER_TRACE"  # a newer command already sent

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.hyxi_cloud.protection"
    ):
        controller._handle_device_rejected("idle", "OLD_TRACE")  # stale rejection

    assert controller._last_sent_mode == "charge"
    assert controller._last_sent_trace_id == "NEWER_TRACE"
    assert controller._last_device_rejected_logged is False
    assert "no action needed" in caplog.text
    assert [r.levelno for r in caplog.records if "no action needed" in r.message] == [
        logging.DEBUG
    ]
    controller._coordinator.async_request_refresh.assert_not_called()


def test_note_manual_mode_invalidates_pending_verification():
    """Verify a manual mode command for a genuinely *different* mode
    clears any trace_id an in-flight automatic verification was tracking
    for the old mode, so a later rejection of that earlier automatic send
    can't clobber the manual command's mode."""
    controller = _build_controller(50)
    controller._last_sent_mode = "idle"
    controller._last_sent_trace_id = "AUTO_TRACE"

    controller.note_manual_mode("charge")

    assert controller._last_sent_mode == "charge"
    assert controller._last_sent_trace_id is None


def test_note_manual_mode_same_mode_reaffirmation_preserves_pending_verification():
    """Regression test: EM's routine _notify_protection(mode) call (which
    never carries a trace_id) must not cancel protection's own pending
    verification just because it happens to reaffirm the same mode
    protection already sent -- EM and protection can share a controller
    for the same device, and EM's decision loop calls this on every
    successful send, not just ones that actually change the mode.
    """
    controller = _build_controller(50)
    controller._last_sent_mode = "idle"
    controller._last_sent_trace_id = "AUTO_TRACE"

    controller.note_manual_mode("idle")  # e.g. EM's _notify_protection

    assert controller._last_sent_mode == "idle"
    assert controller._last_sent_trace_id == "AUTO_TRACE"  # untouched


def test_note_manual_mode_same_mode_with_new_trace_updates_tracking():
    """Verify a same-mode notification that *does* carry a real trace_id
    (e.g. a double-clicked manual button, both sends for 'idle') updates
    tracking to the newest send rather than leaving the first one's trace
    in place -- so a later rejection of the superseded first send is
    correctly recognized as stale instead of clobbering the second."""
    controller = _build_controller(50)
    controller.note_manual_mode("idle", "TRACE_OLD")

    controller.note_manual_mode("idle", "TRACE_NEW")

    assert controller._last_sent_mode == "idle"
    assert controller._last_sent_trace_id == "TRACE_NEW"


def test_note_manual_mode_rejected_delegates_to_handle_device_rejected():
    """Verify note_manual_mode_rejected delegates to the same trace_id-based
    staleness check protection's own automatic sends use, rather than a
    separate, weaker mode-string-only comparison."""
    controller = _build_controller(50)
    controller._handle_device_rejected = MagicMock()

    controller.note_manual_mode_rejected("idle", "TRACE123")

    controller._handle_device_rejected.assert_called_once_with("idle", "TRACE123")


def test_note_manual_mode_rejected_clears_matching_send():
    """Verify a rejected manual command (with its own recorded trace_id
    via note_manual_mode) clears _last_sent_mode when it's still current."""
    controller = _build_controller(50)
    controller.note_manual_mode("idle", "TRACE123")

    controller.note_manual_mode_rejected("idle", "TRACE123")

    assert controller._last_sent_mode is None
    assert controller._last_sent_trace_id is None


def test_note_manual_mode_rejected_leaves_superseded_send_alone():
    """Verify a rejection for a trace_id that's no longer current (a
    newer manual or automatic send has replaced it) doesn't clobber that
    newer state -- fixing the double-click race a mode-string-only check
    couldn't tell apart."""
    controller = _build_controller(50)
    controller.note_manual_mode("idle", "TRACE_OLD")
    controller.note_manual_mode("idle", "TRACE_NEW")  # e.g. a double-click retry

    controller.note_manual_mode_rejected("idle", "TRACE_OLD")

    assert controller._last_sent_mode == "idle"
    assert controller._last_sent_trace_id == "TRACE_NEW"


def protection_module_logger():
    """Return protection.py's module logger, for asserting on _LOGGER.log calls."""
    from custom_components.hyxi_cloud import protection as protection_mod

    return protection_mod._LOGGER


@pytest.mark.asyncio
async def test_ensure_mode_schedules_verification_after_successful_cloud_send():
    """Verify _ensure_mode extracts the traceId from the client's response,
    records it as the currently-tracked send, and passes it through to
    _maybe_verify_control_result after a successful send."""
    controller = _build_controller(50, "H5K-HT")
    controller._ensure_mode = HyxiBatteryProtectionController._ensure_mode.__get__(
        controller, HyxiBatteryProtectionController
    )
    controller._send_control = AsyncMock(return_value=_CONTROL_RESPONSE)
    controller._maybe_verify_control_result = MagicMock()

    await controller._ensure_mode("idle")

    controller._maybe_verify_control_result.assert_called_once_with("idle", "TRACE123")
    assert controller._last_sent_trace_id == "TRACE123"

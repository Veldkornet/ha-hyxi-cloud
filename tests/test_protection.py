"""Tests for minimal battery protection logic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hyxi_cloud.protection import HyxiBatteryProtectionController


class FakeCoordinator:
    """Minimal coordinator stub for protection tests."""

    def __init__(self, soc: float, model: str = "H5K-HT") -> None:
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
        )
        self.async_request_refresh = AsyncMock()

    def async_add_listener(self, listener):
        """Return a no-op unsubscribe callback."""
        return lambda: None


def _build_controller(
    soc: float, model: str = "H5K-HT"
) -> HyxiBatteryProtectionController:
    """Create a controller with parameter lookups stubbed."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    controller = HyxiBatteryProtectionController(
        hass, FakeCoordinator(soc, model), "SN123"
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
    """Verify async_stop unsubscribes and cancels the running evaluation task."""
    controller = _build_controller(50)
    mock_unsub = MagicMock()
    controller._unsub_listener = mock_unsub

    mock_task = MagicMock()
    mock_task.done.return_value = False
    controller._eval_task = mock_task

    await controller.async_stop()

    mock_unsub.assert_called_once()
    assert controller._unsub_listener is None
    mock_task.cancel.assert_called_once()
    assert controller._eval_task is None


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
    controller._last_mode_switch = 0.0  # bypass cooldown
    await controller._ensure_mode("charge")
    controller._coordinator.client.set_mode_charge.assert_awaited_once()

    # 3. discharge
    controller._last_mode_switch = 0.0
    await controller._ensure_mode("discharge")
    controller._coordinator.client.set_mode_discharge.assert_awaited_once()

    # 4. self_consume
    controller._last_mode_switch = 0.0
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

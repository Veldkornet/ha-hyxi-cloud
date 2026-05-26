"""Tests for minimal battery protection logic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
    hass = SimpleNamespace(async_create_task=lambda coro: None)
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

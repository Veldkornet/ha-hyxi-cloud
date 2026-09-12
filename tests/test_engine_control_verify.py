"""Tests for the Energy Manager's Cloud control-write verification wiring.

set_mode_*/set_peak_shaving over Cloud are fire-and-forget: a success
response only confirms HYXI's cloud accepted the write, not that the
device applied it. These tests cover EM's own wiring on top of the
shared control_verify polling (tested directly in test_control_verify.py):
scheduling, interpreting the outcome, and correcting _current_mode on a
confirmed rejection. Hand-mocked (no real hass fixture) since none of
this touches HA entity state/attributes the real framework validates --
see tests/integration/test_real_engine.py for _set_mode/_adjust_power's
own dispatch behavior against the real class.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hyxi_cloud import control_verify
from custom_components.hyxi_cloud.engine import EMEntityConfig, EnergyManagerEngine

_CONTROL_RESPONSE = {
    "success": True,
    "data": [{"traceId": "TRACE123", "deviceSn": "SN123"}],
}


def _fake_async_create_task(coro):
    """Stand in for HomeAssistant.async_create_task: don't run the
    coroutine, but close it so an unawaited background task doesn't leave
    a "coroutine was never awaited" RuntimeWarning behind."""
    coro.close()
    return MagicMock()


def _build_engine(transport: str = "cloud") -> EnergyManagerEngine:
    """Build a real EnergyManagerEngine with a minimal mocked hass/coordinator."""
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=_fake_async_create_task)

    coordinator = MagicMock()
    coordinator.entry = SimpleNamespace(data={"transport": transport}, options={})
    coordinator.client = SimpleNamespace(
        set_mode_idle=AsyncMock(return_value=_CONTROL_RESPONSE),
        set_mode_charge=AsyncMock(return_value=_CONTROL_RESPONSE),
        set_mode_discharge=AsyncMock(return_value=_CONTROL_RESPONSE),
        set_mode_self_consume=AsyncMock(return_value=_CONTROL_RESPONSE),
        set_peak_shaving=AsyncMock(return_value=_CONTROL_RESPONSE),
        query_control_result=AsyncMock(),
    )
    coordinator.protection_controllers = {}

    config = EMEntityConfig(sn="SN123", p1_entity="sensor.p1_meter")
    return EnergyManagerEngine(hass, coordinator, config)


@pytest.mark.asyncio
async def test_maybe_verify_control_result_skips_modbus():
    """Verify Modbus writes never schedule a verification task."""
    engine = _build_engine(transport="modbus")

    with patch.object(engine._hass, "async_create_task") as mock_create:
        engine._maybe_verify_control_result("idle", "TRACE123")
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_verify_control_result_skips_when_no_trace_id():
    """Verify a None trace_id schedules nothing."""
    engine = _build_engine()

    with patch.object(engine._hass, "async_create_task") as mock_create:
        engine._maybe_verify_control_result("idle", None)
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_verify_control_result_schedules_and_tracks_task():
    """Verify a real traceId schedules verify_control_result as a tracked
    background task that discards itself once done."""
    import asyncio

    engine = _build_engine()

    real_task = asyncio.ensure_future(asyncio.sleep(0))

    def _create_task(coro):
        # The real verify_control_result(...) coroutine built by
        # _maybe_verify_control_result is discarded in favor of
        # real_task below -- close it so it doesn't leave a "coroutine
        # was never awaited" warning behind.
        coro.close()
        return real_task

    with patch.object(
        engine._hass, "async_create_task", side_effect=_create_task
    ) as mock_create:
        engine._maybe_verify_control_result("idle", "TRACE123")

    mock_create.assert_called_once()
    assert real_task in engine._verify_tasks
    await asyncio.gather(real_task)
    assert real_task not in engine._verify_tasks


def test_on_verify_result_failure_delegates_to_handle_device_rejected():
    """Verify a confirmed "6" (failure) outcome is handed to
    _handle_device_rejected."""
    engine = _build_engine()
    engine._handle_device_rejected = MagicMock()

    engine._on_verify_result("idle", "TRACE123", control_verify.RESULT_FAILURE)

    engine._handle_device_rejected.assert_called_once_with("idle", "TRACE123")


def test_on_verify_result_success_and_none_are_no_ops():
    """Verify a confirmed success, or an inconclusive/errored outcome
    (already logged by verify_control_result itself), don't touch state."""
    engine = _build_engine()
    engine._handle_device_rejected = MagicMock()

    engine._on_verify_result("idle", "TRACE123", control_verify.RESULT_SUCCESS)
    engine._on_verify_result("idle", "TRACE123", None)

    engine._handle_device_rejected.assert_not_called()


def test_handle_device_rejected_clears_current_mode_when_still_current():
    """Verify a rejection for the currently-tracked trace_id logs a
    warning and clears _current_mode/_current_mode_trace_id."""
    engine = _build_engine()
    engine._current_mode = "idle"
    engine._current_mode_trace_id = "TRACE123"

    engine._handle_device_rejected("idle", "TRACE123")

    assert engine._current_mode is None
    assert engine._current_mode_trace_id is None


def test_handle_device_rejected_leaves_superseded_send_alone():
    """Verify a rejection for a trace_id that's no longer current
    (superseded by a newer send) doesn't clobber that newer state."""
    engine = _build_engine()
    engine._current_mode = "charge"
    engine._current_mode_trace_id = "NEWER_TRACE"

    engine._handle_device_rejected("idle", "OLD_TRACE")

    assert engine._current_mode == "charge"
    assert engine._current_mode_trace_id == "NEWER_TRACE"


@pytest.mark.asyncio
async def test_set_mode_schedules_verification_after_successful_cloud_send():
    """Verify _set_mode extracts the traceId from the client's response,
    records it, and passes it through to _maybe_verify_control_result."""
    engine = _build_engine()
    engine._last_mode_switch = -999999.0
    engine._maybe_verify_control_result = MagicMock()

    assert await engine._set_mode("idle") is True

    engine._maybe_verify_control_result.assert_called_once_with("idle", "TRACE123")
    assert engine._current_mode_trace_id == "TRACE123"


@pytest.mark.asyncio
async def test_adjust_power_schedules_verification_after_successful_cloud_send():
    """Verify _adjust_power extracts the traceId from the client's
    response, records it, and passes it through to
    _maybe_verify_control_result."""
    engine = _build_engine()
    engine._last_power_adjust = -999999.0
    engine._maybe_verify_control_result = MagicMock()

    assert await engine._adjust_power("charge", 700) is True

    engine._maybe_verify_control_result.assert_called_once_with("charge", "TRACE123")
    assert engine._current_mode_trace_id == "TRACE123"


@pytest.mark.asyncio
async def test_set_peak_shaving_schedules_verification_after_successful_cloud_send():
    """Verify _set_peak_shaving extracts the traceId from the client's
    response, records it, and passes it through to
    _maybe_verify_peak_shaving_result."""
    engine = _build_engine()
    engine._last_pv_curtail_toggle = -999999.0
    engine._maybe_verify_peak_shaving_result = MagicMock()

    assert await engine._set_peak_shaving("stop") is True

    engine._maybe_verify_peak_shaving_result.assert_called_once_with("stop", "TRACE123")
    assert engine._pv_curtail_trace_id == "TRACE123"


@pytest.mark.asyncio
async def test_maybe_verify_peak_shaving_result_skips_modbus():
    """Verify Modbus writes never schedule a peak-shaving verification task."""
    engine = _build_engine(transport="modbus")

    with patch.object(engine._hass, "async_create_task") as mock_create:
        engine._maybe_verify_peak_shaving_result("stop", "TRACE123")
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_verify_peak_shaving_result_skips_when_no_trace_id():
    """Verify a None trace_id schedules nothing."""
    engine = _build_engine()

    with patch.object(engine._hass, "async_create_task") as mock_create:
        engine._maybe_verify_peak_shaving_result("stop", None)
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_verify_peak_shaving_result_schedules_and_tracks_task():
    """Verify a real traceId schedules verify_control_result as a tracked
    background task that discards itself once done."""
    import asyncio

    engine = _build_engine()

    real_task = asyncio.ensure_future(asyncio.sleep(0))

    def _create_task(coro):
        coro.close()
        return real_task

    with patch.object(
        engine._hass, "async_create_task", side_effect=_create_task
    ) as mock_create:
        engine._maybe_verify_peak_shaving_result("stop", "TRACE123")

    mock_create.assert_called_once()
    assert real_task in engine._verify_tasks
    await asyncio.gather(real_task)
    assert real_task not in engine._verify_tasks


def test_on_peak_shaving_verify_result_failure_delegates():
    """Verify a confirmed "6" (failure) outcome is handed to
    _handle_peak_shaving_rejected."""
    engine = _build_engine()
    engine._handle_peak_shaving_rejected = MagicMock()

    engine._on_peak_shaving_verify_result(
        "stop", "TRACE123", control_verify.RESULT_FAILURE
    )

    engine._handle_peak_shaving_rejected.assert_called_once_with("stop", "TRACE123")


def test_on_peak_shaving_verify_result_success_clears_rejection_flag():
    """Verify a confirmed success for the currently-tracked trace_id
    clears the peak-shaving rejection-log throttle flag."""
    engine = _build_engine()
    engine._peak_shaving_rejected_logged = True
    engine._pv_curtail_trace_id = "TRACE123"

    engine._on_peak_shaving_verify_result(
        "stop", "TRACE123", control_verify.RESULT_SUCCESS
    )

    assert engine._peak_shaving_rejected_logged is False


def test_on_peak_shaving_verify_result_stale_success_does_not_clear_flag():
    """Verify a confirmed success for a superseded trace_id doesn't clear
    the throttle flag for a still-current rejection."""
    engine = _build_engine()
    engine._peak_shaving_rejected_logged = True
    engine._pv_curtail_trace_id = "NEWER_TRACE"

    engine._on_peak_shaving_verify_result(
        "stop", "OLD_TRACE", control_verify.RESULT_SUCCESS
    )

    assert engine._peak_shaving_rejected_logged is True


def test_on_verify_result_success_clears_mode_rejection_flag():
    """Verify a confirmed success for the currently-tracked trace_id
    clears the mode rejection-log throttle flag, independently of the
    peak-shaving stream's own flag."""
    engine = _build_engine()
    engine._mode_rejected_logged = True
    engine._peak_shaving_rejected_logged = True
    engine._current_mode_trace_id = "TRACE123"

    engine._on_verify_result("idle", "TRACE123", control_verify.RESULT_SUCCESS)

    assert engine._mode_rejected_logged is False
    assert engine._peak_shaving_rejected_logged is True  # untouched


def test_on_verify_result_stale_success_does_not_clear_mode_flag():
    """Verify a confirmed success for a superseded trace_id doesn't clear
    the mode-rejection throttle flag for a still-current rejection."""
    engine = _build_engine()
    engine._mode_rejected_logged = True
    engine._current_mode_trace_id = "NEWER_TRACE"

    engine._on_verify_result("idle", "OLD_TRACE", control_verify.RESULT_SUCCESS)

    assert engine._mode_rejected_logged is True


def test_handle_peak_shaving_rejected_reverts_when_still_current():
    """Verify a confirmed rejection for the currently-tracked trace_id
    undoes the optimistic _pv_curtailed flip."""
    engine = _build_engine()
    engine._pv_curtailed = True  # optimistically set by option == "stop"
    engine._pv_curtail_trace_id = "TRACE123"

    engine._handle_peak_shaving_rejected("stop", "TRACE123")

    assert engine._pv_curtailed is False
    assert engine._pv_curtail_trace_id is None


def test_handle_peak_shaving_rejected_leaves_superseded_send_alone():
    """Verify a rejection for a trace_id that's no longer current doesn't
    clobber newer curtailment state."""
    engine = _build_engine()
    engine._pv_curtailed = False
    engine._pv_curtail_trace_id = "NEWER_TRACE"

    engine._handle_peak_shaving_rejected("stop", "OLD_TRACE")

    assert engine._pv_curtailed is False
    assert engine._pv_curtail_trace_id == "NEWER_TRACE"


@pytest.mark.asyncio
async def test_release_pv_curtailment_does_not_force_state_when_hold_is_blocked():
    """Regression test: _release_pv_curtailment must not declare
    curtailment released when its own "hold" write never actually sent
    anything (blocked by _set_peak_shaving's own cooldown, or a raised
    ControlError) -- doing so previously left _pv_curtailed=False while
    _pv_curtail_trace_id still pointed at the earlier "stop" send, so
    that stop's later-confirmed rejection would flip _pv_curtailed back
    on top of the wrong baseline."""
    engine = _build_engine()
    engine._pv_curtailed = True
    engine._pv_curtail_trace_id = "STOP_TRACE"
    engine._set_peak_shaving = AsyncMock(return_value=False)  # blocked/failed

    await engine._release_pv_curtailment()

    engine._set_peak_shaving.assert_awaited_once_with("hold")
    assert engine._pv_curtailed is True  # unchanged: nothing was actually sent
    assert engine._pv_curtail_trace_id == "STOP_TRACE"  # unchanged


@pytest.mark.asyncio
async def test_release_pv_curtailment_clears_state_via_successful_hold():
    """Verify a successful "hold" send clears _pv_curtailed the normal
    way -- through _set_peak_shaving's own state update, not a redundant
    unconditional set in the caller."""
    engine = _build_engine()
    engine._pv_curtailed = True
    engine._last_pv_curtail_toggle = -999999.0

    await engine._release_pv_curtailment()

    assert engine._pv_curtailed is False
    assert engine._pv_curtail_trace_id == "TRACE123"


def test_stop_cancels_pending_verify_tasks():
    """Verify stop() cancels any pending control-result verification tasks."""
    engine = _build_engine()
    engine._enabled = True

    pending = MagicMock()
    pending.done.return_value = False
    engine._verify_tasks = {pending}

    engine.stop()

    pending.cancel.assert_called_once()
    assert engine._verify_tasks == set()

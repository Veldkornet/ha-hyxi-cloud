"""Tests for the shared Cloud control-write verification helpers.

set_device_control is fire-and-forget: HYXI's response only confirms the
cloud accepted a write, not that the device applied it. extract_trace_id
and verify_control_result close that gap by polling query_control_result
for the traceId the write returned; they're shared by protection.py,
control.py, and engine.py, each of which tests only its own wiring on top
(scheduling, and what a confirmed outcome should correct in its own
state).
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.hyxi_cloud import control_verify

_CONTROL_RESPONSE = {
    "success": True,
    "data": [{"traceId": "TRACE123", "deviceSn": "SN123"}],
}


def test_extract_trace_id_valid():
    """Verify extract_trace_id pulls traceId out of a well-formed
    set_device_control response."""
    assert control_verify.extract_trace_id(_CONTROL_RESPONSE) == "TRACE123"


def test_extract_trace_id_malformed_shapes():
    """Verify extract_trace_id returns None for every malformed shape,
    including a Modbus response (which carries no `data` at all)."""
    extract = control_verify.extract_trace_id

    assert extract({"code": "0", "msg": "ok"}) is None  # Modbus-shaped
    assert extract({"data": None}) is None
    assert extract({"data": "not-a-list"}) is None
    assert extract({"data": []}) is None
    assert extract({"data": ["not-a-dict"]}) is None
    assert extract({"data": [{"deviceSn": "SN123"}]}) is None  # no traceId
    assert extract({"data": [{"traceId": 12345}]}) is None  # not a string
    assert extract({"data": [{"traceId": ""}]}) is None  # empty string
    assert extract({"data": [{"traceId": "   "}]}) is None  # whitespace-only


@pytest.mark.asyncio
async def test_verify_control_result_success_first_attempt(caplog):
    """Verify a "3" (success) result on the first poll stops immediately,
    logs confirmation, and reports it via on_result."""
    import logging

    client = AsyncMock()
    client.query_control_result = AsyncMock(
        return_value={"success": True, "data": {"result": "3"}}
    )
    results: list[str | None] = []

    with patch.object(control_verify.asyncio, "sleep", AsyncMock()):
        caplog.set_level(logging.DEBUG, logger="custom_components.hyxi_cloud")
        await control_verify.verify_control_result(
            client, "SN123", "Test", "idle", "TRACE123", results.append
        )

    client.query_control_result.assert_awaited_once_with("TRACE123")
    assert results == [control_verify.RESULT_SUCCESS]
    assert "confirmed applied by the device" in caplog.text


@pytest.mark.asyncio
async def test_verify_control_result_issuing_then_success():
    """Verify "2" (issuing) keeps polling until a later attempt succeeds."""
    client = AsyncMock()
    client.query_control_result = AsyncMock(
        side_effect=[
            {"success": True, "data": {"result": "2"}},
            {"success": True, "data": {"result": "3"}},
        ]
    )
    results: list[str | None] = []

    with patch.object(control_verify.asyncio, "sleep", AsyncMock()):
        await control_verify.verify_control_result(
            client, "SN123", "Test", "charge", "TRACE123", results.append
        )

    assert client.query_control_result.await_count == 2
    assert results == [control_verify.RESULT_SUCCESS]


@pytest.mark.asyncio
async def test_verify_control_result_failure_reported_immediately():
    """Verify a "6" (failure) result reports it via on_result and stops
    polling immediately."""
    client = AsyncMock()
    client.query_control_result = AsyncMock(
        return_value={"success": True, "data": {"result": "6"}}
    )
    results: list[str | None] = []

    with patch.object(control_verify.asyncio, "sleep", AsyncMock()):
        await control_verify.verify_control_result(
            client, "SN123", "Test", "discharge", "TRACE123", results.append
        )

    client.query_control_result.assert_awaited_once()
    assert results == [control_verify.RESULT_FAILURE]


@pytest.mark.asyncio
async def test_verify_control_result_exhausts_attempts_while_issuing(caplog):
    """Verify polling gives up after the attempt budget if the result
    never resolves past "issuing", reporting None."""
    import logging

    client = AsyncMock()
    client.query_control_result = AsyncMock(
        return_value={"success": True, "data": {"result": "2"}}
    )
    results: list[str | None] = []

    with patch.object(control_verify.asyncio, "sleep", AsyncMock()):
        caplog.set_level(logging.DEBUG, logger="custom_components.hyxi_cloud")
        await control_verify.verify_control_result(
            client, "SN123", "Test", "idle", "TRACE123", results.append
        )

    assert (
        client.query_control_result.await_count == control_verify._VERIFY_MAX_ATTEMPTS
    )
    assert results == [None]
    assert "still unconfirmed" in caplog.text


@pytest.mark.asyncio
async def test_verify_control_result_malformed_data_treated_as_inconclusive():
    """Verify a response with missing/malformed `data` doesn't crash --
    it's treated the same as "issuing" and polling continues."""
    client = AsyncMock()
    client.query_control_result = AsyncMock(
        return_value={"success": True, "data": None}
    )
    results: list[str | None] = []

    with patch.object(control_verify.asyncio, "sleep", AsyncMock()):
        await control_verify.verify_control_result(
            client, "SN123", "Test", "idle", "TRACE123", results.append
        )

    assert (
        client.query_control_result.await_count == control_verify._VERIFY_MAX_ATTEMPTS
    )
    assert results == [None]


@pytest.mark.asyncio
async def test_verify_control_result_non_dict_response_treated_as_inconclusive():
    """Verify a response that isn't even a dict (a malformed API reply,
    or a test double that doesn't return one) doesn't crash -- it's
    treated the same as "issuing" and polling continues, rather than
    raising AttributeError from response.get(...) outside the try block."""
    client = AsyncMock()
    client.query_control_result = AsyncMock(return_value="not-a-dict")
    results: list[str | None] = []

    with patch.object(control_verify.asyncio, "sleep", AsyncMock()):
        await control_verify.verify_control_result(
            client, "SN123", "Test", "idle", "TRACE123", results.append
        )

    assert (
        client.query_control_result.await_count == control_verify._VERIFY_MAX_ATTEMPTS
    )
    assert results == [None]


@pytest.mark.asyncio
async def test_verify_control_result_retries_after_exception(caplog):
    """Verify any exception from query_control_result itself (a transient
    auth/transport failure, or a raw error the client didn't wrap) is
    logged and swallowed rather than escaping the background task, and
    that the loop retries on the remaining attempt budget instead of
    giving up on the first hiccup -- this is a secondary confirmation of
    a write HYXI's cloud already reported accepting, not the write
    itself, so one bad lookup shouldn't cost the whole confirmation."""
    import logging

    client = AsyncMock()
    client.query_control_result = AsyncMock(side_effect=TimeoutError("timed out"))
    results: list[str | None] = []

    with patch.object(control_verify.asyncio, "sleep", AsyncMock()):
        caplog.set_level(logging.DEBUG, logger="custom_components.hyxi_cloud")
        await control_verify.verify_control_result(
            client, "SN123", "Test", "idle", "TRACE123", results.append
        )  # must not raise

    assert (
        client.query_control_result.await_count == control_verify._VERIFY_MAX_ATTEMPTS
    )
    assert results == [None]
    assert "could not confirm mode" in caplog.text
    assert "still unconfirmed" in caplog.text


@pytest.mark.asyncio
async def test_verify_control_result_recovers_after_a_transient_exception():
    """Verify a transient exception on an early attempt doesn't prevent a
    later, successful attempt from being observed."""
    client = AsyncMock()
    client.query_control_result = AsyncMock(
        side_effect=[
            TimeoutError("timed out"),
            {"success": True, "data": {"result": "3"}},
        ]
    )
    results: list[str | None] = []

    with patch.object(control_verify.asyncio, "sleep", AsyncMock()):
        await control_verify.verify_control_result(
            client, "SN123", "Test", "idle", "TRACE123", results.append
        )

    assert client.query_control_result.await_count == 2
    assert results == [control_verify.RESULT_SUCCESS]


def test_cancel_pending_cancels_only_undone_tasks():
    """Verify cancel_pending cancels every not-yet-done task in the set
    and clears it, leaving already-finished tasks alone."""
    from unittest.mock import MagicMock

    pending = MagicMock()
    pending.done.return_value = False
    finished = MagicMock()
    finished.done.return_value = True

    tasks = {pending, finished}
    control_verify.cancel_pending(tasks)

    pending.cancel.assert_called_once()
    finished.cancel.assert_not_called()
    assert tasks == set()


@pytest.mark.asyncio
async def test_cancel_pending_with_real_tasks():
    """Verify cancel_pending against real asyncio tasks (not just mocks)."""
    pending = asyncio.ensure_future(asyncio.sleep(10))
    finished = asyncio.ensure_future(asyncio.sleep(0))
    await asyncio.gather(finished)

    tasks = {pending, finished}
    control_verify.cancel_pending(tasks)

    assert pending.cancelled() or pending.cancelling()
    assert tasks == set()

    # Let the cancellation actually propagate so pytest-asyncio doesn't
    # warn about a task destroyed while pending at test teardown.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(pending)

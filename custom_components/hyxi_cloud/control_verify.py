"""Shared Cloud control-write verification.

set_device_control (behind every set_mode_*/set_peak_shaving Cloud call)
is fire-and-forget: a success response only means HYXI's cloud accepted
the write, not that the device applied it. HYXI's query_control_result
lets a caller poll for the real, device-level outcome using the traceId
the write returned. protection.py, control.py, and engine.py each issue
Cloud control writes and each need this same confirm-after-the-fact step,
so the traceId-extraction and polling logic live here once rather than
being copied into all three.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .const import mask_sn

_LOGGER = logging.getLogger(__name__)

# HYXI's query_control_result "result" codes (hyxi_cloud_api's
# query_control_result docstring): "2" issuing, "3" success, "6" failure.
RESULT_SUCCESS = "3"
RESULT_FAILURE = "6"

# These govern how long verify_control_result polls query_control_result
# after a write before giving up -- unconfirmed against live timing,
# chosen as a reasonable window for a cloud round-trip to a device rather
# than a measured value.
_VERIFY_INITIAL_DELAY = 5.0
_VERIFY_RETRY_DELAY = 5.0
_VERIFY_MAX_ATTEMPTS = 3


def extract_trace_id(response: dict) -> str | None:
    """Pull the traceId out of a Cloud set_device_control response.

    HYXI's v2 control endpoint returns `data` as a list with one
    `{"traceId": ..., "deviceSn": ...}` entry per device in the request --
    always exactly one here, since set_device_control is always called
    with a single device_sn. A Modbus response carries no `data`/traceId
    at all, so this returns None for those too.
    """
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if not isinstance(data, list) or not data:
        return None
    entry = data[0]
    if not isinstance(entry, dict):
        return None
    trace_id = entry.get("traceId")
    # query_control_result itself rejects a blank/whitespace-only
    # trace_id (ValueError); matching that here means a malformed
    # traceId is treated as "none extracted" in one place, not two.
    return trace_id if isinstance(trace_id, str) and trace_id.strip() else None


async def verify_control_result(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    client,
    sn: str,
    log_tag: str,
    mode: str,
    trace_id: str,
    on_result: Callable[[str | None], None],
) -> None:
    """Poll query_control_result until the device confirms `mode` applied,
    confirms it was rejected, or the attempt budget runs out.

    Intended to run as a background task (see callers) so it never blocks
    the write it's confirming. Any failure to reach HYXI here is logged
    and swallowed, never raised -- this is a secondary confirmation of a
    write HYXI's cloud already reported accepting, not the write itself,
    so no exception from it should ever surface as an unhandled
    background-task error.

    Calls `on_result` exactly once with the final result code (RESULT_
    SUCCESS/RESULT_FAILURE) or None (a lookup failure, or "issuing"/an
    unrecognized code past the attempt budget) -- the caller decides what,
    if anything, needs correcting in its own state; this function only
    knows how to poll and log, not what `mode` means to its caller.
    """
    for attempt in range(_VERIFY_MAX_ATTEMPTS):
        await asyncio.sleep(
            _VERIFY_INITIAL_DELAY if attempt == 0 else _VERIFY_RETRY_DELAY
        )
        try:
            response = await client.query_control_result(trace_id)
        except Exception as err:  # pylint: disable=broad-exception-caught
            # A transient lookup failure (rate limit, network blip) on one
            # attempt shouldn't give up on the whole poll budget -- log it
            # and let the loop retry, same as an "issuing" result would.
            _LOGGER.debug(
                "%s %s: could not confirm mode '%s' result (attempt %d/%d): %s",
                log_tag,
                mask_sn(sn),
                mode,
                attempt + 1,
                _VERIFY_MAX_ATTEMPTS,
                err,
            )
            continue

        data = response.get("data") if isinstance(response, dict) else None
        result = data.get("result") if isinstance(data, dict) else None
        if result == RESULT_SUCCESS:
            _LOGGER.debug(
                "%s %s: mode '%s' confirmed applied by the device",
                log_tag,
                mask_sn(sn),
                mode,
            )
            on_result(result)
            return
        if result == RESULT_FAILURE:
            on_result(result)
            return
        # "2" (issuing) or an unrecognized code: keep polling.

    _LOGGER.debug(
        "%s %s: mode '%s' still unconfirmed after %d attempts",
        log_tag,
        mask_sn(sn),
        mode,
        _VERIFY_MAX_ATTEMPTS,
    )
    on_result(None)


def cancel_pending(tasks: set[asyncio.Task]) -> None:
    """Cancel every not-yet-done task in `tasks`, then empty it.

    Shared by protection.py/engine.py's stop() methods, both of which
    track their own set of in-flight verify_control_result tasks so a
    config-entry unload doesn't leave them running indefinitely.
    """
    for task in tasks:
        if not task.done():
            task.cancel()
    tasks.clear()

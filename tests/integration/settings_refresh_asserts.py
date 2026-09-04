"""Shared bodies for the settings-block refresh-cadence tests.

test_modbus.py (HALO) and test_modbus_hybrid.py (hybrid) each need the same
three behaviors checked against their own client fixture and their own
family-specific register/metric: the refresh window is respected, it's
re-read once elapsed, and a failed read past the window still retries and
recovers. Sharing the assertions here, rather than each file spelling out
the same sequence of calls, is what keeps that from being copy-pasted code
SonarCloud (and any future reader diffing the two files) would have to
verify stays in sync by hand.
"""

from unittest.mock import patch

from modbus_connection import IllegalDataAddressError


async def settings_are_not_reread_within_the_refresh_window(client) -> None:
    """Settings change rarely, and every set_* method already keeps HA's
    own copy in sync with what it wrote -- a poll inside the refresh
    window must not pay for another block read."""
    await client.async_read_all()
    with patch.object(client.settings, "async_update") as second:
        await client.async_read_all()

    second.assert_not_called()


async def settings_are_reread_once_the_refresh_window_elapses(
    client, refresh_seconds: int
) -> None:
    """Past the refresh window, the next poll re-reads the block -- this is
    what catches a change made by something other than this session (the
    app, another Modbus master)."""
    await client.async_read_all()
    client._settings_read_at -= refresh_seconds + 1  # pylint: disable=protected-access

    with patch.object(
        client.settings, "async_update", wraps=client.settings.async_update
    ) as second:
        await client.async_read_all()

    second.assert_called_once()


async def a_failed_settings_read_retries_after_the_refresh_window(
    client,
    refresh_seconds: int,
    fail_register: int,
    metric_key: str,
    expected_value: object,
) -> None:
    """A transient failure must not lock the block out forever. Past the
    refresh window the next poll tries again, and recovers the real value
    once the device answers."""
    client._unit.fail_read(  # pylint: disable=protected-access
        fail_register, IllegalDataAddressError(2, "nope"), register_type="holding"
    )
    devices = await client.async_read_all()
    sn = next(iter(devices))
    assert metric_key not in devices[sn]["metrics"]

    client._settings_read_at -= refresh_seconds + 1  # pylint: disable=protected-access
    client._unit.fail_read(  # pylint: disable=protected-access
        fail_register, None, register_type="holding"
    )  # device recovers

    devices = await client.async_read_all()
    assert devices[sn]["metrics"][metric_key] == expected_value


def settings_refresh_can_be_forced_past_the_window(client) -> None:
    """force_settings_refresh backs the manual "Refresh Settings" button --
    it must actually clear the retry throttle, not just exist."""
    client._settings_read_at = 123.0  # pylint: disable=protected-access
    client.force_settings_refresh()
    assert client._settings_read_at is None  # pylint: disable=protected-access

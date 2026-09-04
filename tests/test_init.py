"""Tests for the initial setup of the HYXI Cloud integration."""

import sys
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# Define AUTHORITATIVE local exceptions first to avoid MagicMock TypeErrors in pytest.raises
class ConfigEntryAuthFailed(Exception):
    """Authoritative local class for auth failure."""


class ConfigEntryNotReady(Exception):
    """Authoritative local class for entry not ready."""


class UpdateFailed(Exception):
    """Authoritative local class for update failed."""


# We MUST define the initial mocks for sys.modules if they aren't there because the test
# might be run individually, meaning other tests haven't put them there yet.

if "homeassistant.exceptions" not in sys.modules or not hasattr(
    sys.modules["homeassistant.exceptions"], "ConfigEntryAuthFailed"
):
    print("DEBUG: test_init.py exception mock block is RUNNING!")
    # Harden the mock to behave like a module
    mock_ha = MagicMock()
    mock_ha.__path__ = []
    mock_ha.__spec__ = MagicMock()
    if "homeassistant" not in sys.modules:
        sys.modules["homeassistant"] = mock_ha
    if "homeassistant.components" not in sys.modules:
        sys.modules["homeassistant.components"] = MagicMock()
    if "homeassistant.const" not in sys.modules:
        sys.modules["homeassistant.const"] = mock_ha
    if "homeassistant.core" not in sys.modules:
        sys.modules["homeassistant.core"] = mock_ha
    if "homeassistant.exceptions" not in sys.modules:
        sys.modules["homeassistant.exceptions"] = mock_ha
    if "homeassistant.helpers" not in sys.modules:
        sys.modules["homeassistant.helpers"] = mock_ha
    if "homeassistant.util" not in sys.modules:
        sys.modules["homeassistant.util"] = mock_ha

    sys.modules[
        "homeassistant.exceptions"
    ].ConfigEntryAuthFailed = ConfigEntryAuthFailed  # type: ignore[attr-defined]
    sys.modules["homeassistant.exceptions"].ConfigEntryNotReady = ConfigEntryNotReady  # type: ignore[attr-defined]
    sys.modules["homeassistant.exceptions"].UpdateFailed = UpdateFailed  # type: ignore[attr-defined]

    # Also inject into the specific locations expected by the component
    if "homeassistant.config_entries" not in sys.modules:
        sys.modules["homeassistant.config_entries"] = MagicMock()
    sys.modules[
        "homeassistant.config_entries"
    ].ConfigEntryAuthFailed = ConfigEntryAuthFailed  # type: ignore[attr-defined]
    sys.modules[
        "homeassistant.config_entries"
    ].ConfigEntryNotReady = ConfigEntryNotReady  # type: ignore[attr-defined]

    if "homeassistant.helpers.update_coordinator" not in sys.modules:
        sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
    sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed = UpdateFailed  # type: ignore[attr-defined]


if "homeassistant.helpers.aiohttp_client" not in sys.modules:
    sys.modules["homeassistant.helpers.aiohttp_client"] = MagicMock()

if "aiohttp" not in sys.modules:
    sys.modules["aiohttp"] = MagicMock()
    sys.modules["aiohttp"].ClientError = type("ClientError", (Exception,), {})  # type: ignore[attr-defined]

mock_api = sys.modules["hyxi_cloud_api"]


import custom_components.hyxi_cloud.__init__ as hc_init  # pylint: disable=wrong-import-position

# DIRECT NAMESPACE INJECTION: Force the component to use our authoritative classes
# This is the only way to guarantee class identity consistency in a mocked environment.
hc_init.ConfigEntryAuthFailed = ConfigEntryAuthFailed
hc_init.ConfigEntryNotReady = ConfigEntryNotReady
hc_init.UpdateFailed = UpdateFailed


# Redefine for local use is now redundant but kept for legacy nomenclature compatibility
LocalEntryAuthFailed = ConfigEntryAuthFailed
LocalEntryNotReady = ConfigEntryNotReady
LocalUpdateFailed = UpdateFailed


async_setup_entry = hc_init.async_setup_entry
async_unload_entry = hc_init.async_unload_entry
async_reload_entry = hc_init.async_reload_entry

# Inject back into the module if they were mocked by mistake during the import process

from custom_components.hyxi_cloud.const import (  # pylint: disable=wrong-import-position # pylint: disable=wrong-import-position
    DOMAIN,
    PLATFORMS,
)


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.data = {}
    config_entries = MagicMock()
    config_entries.async_forward_entry_setups = AsyncMock()
    config_entries.async_unload_platforms = AsyncMock(return_value=True)
    config_entries.async_reload = AsyncMock()
    config_entries.async_update_entry = MagicMock()
    hass.config_entries = config_entries

    def _create_task(coro, *args, **kwargs):
        import asyncio

        return asyncio.create_task(coro)

    hass.async_create_task = _create_task
    return hass


@pytest.fixture
def mock_entry():
    from custom_components.hyxi_cloud.const import CONF_ACCESS_KEY, CONF_SECRET_KEY

    entry = MagicMock()
    entry.data = {
        CONF_ACCESS_KEY: "test_access",
        CONF_SECRET_KEY: "test_secret",
    }
    entry.options = {}  # Empty options — no EM enabled
    entry.entry_id = "test_id"
    entry.add_update_listener = MagicMock()
    entry.async_on_unload = MagicMock()
    return entry


@pytest.mark.asyncio
async def test_webhook_handle_auth_fails_non_ascii():
    """Verify webhook handles non-ASCII access key in auth without crashing (DoS protection)."""
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"

    request = MagicMock()
    request.headers = {"accessKey": "malicious_ñ_key"}

    res = await _async_handle_webhook("webhook_id", request, coordinator)
    assert res.status == 401


@pytest.mark.asyncio
async def test_async_setup_entry_success(mock_hass, mock_entry):
    """Test successful setup of entry."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
        patch("custom_components.hyxi_cloud.__init__.dr.async_get") as mock_dr_get,
        patch("custom_components.hyxi_cloud.__init__.er.async_get"),
        patch("custom_components.hyxi_cloud.__init__.async_reload_entry"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_preload_cache = AsyncMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.engine = None  # No EM engine
        mock_coordinator.data = {
            "TEST_SN_1": {
                "device_name": "Test Device 1",
                "model": "Model 1",
                "sw_version": "v1",
                "hw_version": "hw1",
                "metrics": {"batSn": "TEST_BAT_1"},
            },
            "TEST_SN_2": {"metrics": {}},
        }

        mock_registry = MagicMock()
        mock_dr_get.return_value = mock_registry

        result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True

        # Check coordinator is in hass.data
        assert DOMAIN in mock_hass.data
        assert mock_entry.entry_id in mock_hass.data[DOMAIN]
        assert mock_hass.data[DOMAIN][mock_entry.entry_id] is mock_coordinator

        # Check parent devices and child device registration
        # Pass 1: SN_1, SN_2
        # Pass 2: BAT_1 (linked to SN_1)
        assert mock_registry.async_get_or_create.call_count == 3

        # We can optionally inspect the calls made to async_get_or_create:
        calls = mock_registry.async_get_or_create.call_args_list
        # Call 1: Base TEST_SN_1
        assert calls[0].kwargs["identifiers"] == {(DOMAIN, "TEST_SN_1")}
        assert calls[0].kwargs["name"] == "Test Device 1"
        assert calls[0].kwargs["serial_number"] == "TEST_SN_1"

        # Call 2: Base TEST_SN_2
        assert calls[1].kwargs["identifiers"] == {(DOMAIN, "TEST_SN_2")}
        assert calls[1].kwargs["name"] == "Device TEST_SN_2"

        # Call 3: Battery TEST_BAT_1 (Pass 2)
        assert calls[2].kwargs["identifiers"] == {(DOMAIN, "TEST_BAT_1")}
        assert calls[2].kwargs["via_device"] == (DOMAIN, "TEST_SN_1")
        assert calls[2].kwargs["serial_number"] == "TEST_BAT_1"

        # Check platforms setup forwarded
        mock_hass.config_entries.async_forward_entry_setups.assert_called_once_with(
            mock_entry, PLATFORMS
        )

        # Check listener added
        mock_entry.add_update_listener.assert_called_once()
        mock_entry.async_on_unload.assert_called_once_with(
            mock_entry.add_update_listener.return_value
        )


@pytest.mark.asyncio
async def test_async_setup_entry_uses_shared_session_not_owned(mock_hass, mock_entry):
    """The integration must never own its aiohttp session.

    It borrows HA's shared per-hass session via async_get_clientsession(),
    which HA's own aiohttp_client helper creates once and closes once on HA
    shutdown. A private ClientSession paired with a manual close() on
    unload/HA-stop is the leak-prone pattern this integration deliberately
    avoids -- so it must also never register a HA-stop listener to do that
    closing, since it has nothing of its own to close.
    """
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch(
            "custom_components.hyxi_cloud.__init__.async_get_clientsession"
        ) as mock_get_session,
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient") as mock_client_cls,
        patch("custom_components.hyxi_cloud.__init__.dr.async_get") as mock_dr_get,
        patch("custom_components.hyxi_cloud.__init__.er.async_get"),
        patch("custom_components.hyxi_cloud.__init__.async_reload_entry"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_preload_cache = AsyncMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.engine = None
        mock_coordinator.data = {}
        mock_dr_get.return_value = MagicMock()

        await async_setup_entry(mock_hass, mock_entry)

        # Client is built from HA's shared session, not a private one --
        # whether passed positionally or by keyword.
        mock_get_session.assert_called_once_with(mock_hass)
        call = mock_client_cls.call_args
        assert mock_get_session.return_value in (*call.args, *call.kwargs.values())

        # No listener registered for HA-stop -- there's no privately-owned
        # session/client to close on shutdown. Scoped to the stop event by
        # name (not "no async_listen call at all") so this doesn't break if
        # the integration adds some other, unrelated bus listener later.
        stop_event_calls = [
            c
            for c in mock_hass.bus.async_listen.call_args_list
            if "homeassistant_stop" in c.args
        ]
        assert not stop_event_calls


@pytest.mark.asyncio
async def test_async_setup_entry_parent_link(mock_hass, mock_entry):
    """Test successful setup of entry with parentSn relationship."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
        patch("custom_components.hyxi_cloud.__init__.dr.async_get") as mock_dr_get,
        patch("custom_components.hyxi_cloud.__init__.er.async_get"),
        patch("custom_components.hyxi_cloud.__init__.async_reload_entry"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_preload_cache = AsyncMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.engine = None  # No EM engine
        mock_coordinator.data = {
            "CHILD_SN_1": {
                "device_name": "Child Device",
                "metrics": {"parentSn": "PARENT_SN_1"},
            },
            "PARENT_SN_1": {"device_name": "Parent Device", "metrics": {}},
        }

        mock_registry = MagicMock()
        mock_dr_get.return_value = mock_registry

        result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True

        # Call count: 2 (Pass 1) + 1 (Pass 2 for ParentSn) = 3
        assert mock_registry.async_get_or_create.call_count == 3
        calls = mock_registry.async_get_or_create.call_args_list

        # Verify child links via_device to parent in Pass 2
        # Call 3 is the update call for CHILD_SN_1 in Pass 2
        assert calls[2].kwargs["identifiers"] == {(DOMAIN, "CHILD_SN_1")}
        assert calls[2].kwargs["via_device"] == (DOMAIN, "PARENT_SN_1")

        # Check platforms setup forwarded
        mock_hass.config_entries.async_forward_entry_setups.assert_called_once_with(
            mock_entry, PLATFORMS
        )

        # Check listener added
        mock_entry.add_update_listener.assert_called_once()
        mock_entry.async_on_unload.assert_called_once_with(
            mock_entry.add_update_listener.return_value
        )


@pytest.mark.asyncio
async def test_async_setup_entry_auth_failed(mock_hass, mock_entry):
    """Test setup failing due to authentication error."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_preload_cache = AsyncMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=ConfigEntryAuthFailed
        )

        with patch(
            "custom_components.hyxi_cloud.__init__._LOGGER.error"
        ) as mock_logger:
            with pytest.raises(ConfigEntryAuthFailed):
                await async_setup_entry(mock_hass, mock_entry)

            mock_logger.assert_called_with("Authentication failed during setup")


@pytest.mark.asyncio
async def test_async_setup_entry_not_ready(mock_hass, mock_entry):
    """Test setup failing due to general exception."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_preload_cache = AsyncMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=UpdateFailed("Timeout")
        )

        with patch(
            "custom_components.hyxi_cloud.__init__._LOGGER.warning"
        ) as mock_logger:
            with pytest.raises(ConfigEntryNotReady) as exc:
                await async_setup_entry(mock_hass, mock_entry)

            assert "Connection error: Timeout" in str(exc.value)
            mock_logger.assert_called_with(
                "HYXI not ready: %s",
                mock_coordinator.async_config_entry_first_refresh.side_effect,
            )


@pytest.mark.asyncio
async def test_async_setup_entry_client_error(mock_hass, mock_entry):
    """Test setup failing due to ClientError."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        # Use our local sys.modules injected class
        client_err = sys.modules["aiohttp"].ClientError("API connection error")
        mock_coordinator.async_preload_cache = AsyncMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=client_err
        )

        with patch(
            "custom_components.hyxi_cloud.__init__._LOGGER.warning"
        ) as mock_logger:
            with pytest.raises(ConfigEntryNotReady) as exc:
                await async_setup_entry(mock_hass, mock_entry)

            assert "Connection error: API connection error" in str(exc.value)
            mock_logger.assert_called_with(
                "HYXI not ready: %s",
                mock_coordinator.async_config_entry_first_refresh.side_effect,
            )


@pytest.mark.asyncio
async def test_async_setup_entry_timeout_error(mock_hass, mock_entry):
    """Test setup failing due to TimeoutError."""
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_preload_cache = AsyncMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=TimeoutError("Connection timed out")
        )

        with patch(
            "custom_components.hyxi_cloud.__init__._LOGGER.warning"
        ) as mock_logger:
            with pytest.raises(ConfigEntryNotReady) as exc:
                await async_setup_entry(mock_hass, mock_entry)

            assert "Connection error: Connection timed out" in str(exc.value)
            mock_logger.assert_called_with(
                "HYXI not ready: %s",
                mock_coordinator.async_config_entry_first_refresh.side_effect,
            )


@pytest.mark.asyncio
async def test_async_setup_entry_missing_keys(mock_hass):
    """Test setup failing due to missing keys."""
    entry = MagicMock()
    entry.data = {}

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.error") as mock_logger:
        result = await async_setup_entry(mock_hass, entry)
        assert result is False
        mock_logger.assert_called_with(
            "HYXI Integration could not find Access/Secret keys."
        )


@pytest.mark.asyncio
async def test_async_unload_entry_success(mock_hass, mock_entry):
    """Test successful unload of a config entry."""
    mock_coordinator = MagicMock()
    mock_coordinator.protection_controllers = {}
    mock_coordinator.engine = None
    mock_hass.data[DOMAIN] = {mock_entry.entry_id: mock_coordinator}
    mock_hass.config_entries.async_unload_platforms.return_value = True

    assert await async_unload_entry(mock_hass, mock_entry) is True

    mock_hass.config_entries.async_unload_platforms.assert_called_once_with(
        mock_entry, PLATFORMS
    )
    assert mock_entry.entry_id not in mock_hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_unload_entry_keeps_subscriptions_alive(mock_hass, mock_entry):
    """Regular unload must NOT cancel subscriptions remotely -- they are kept
    alive (code + fingerprint persisted) for reuse on the next load."""
    mock_coordinator = MagicMock()
    mock_coordinator.protection_controllers = {}
    mock_coordinator.engine = None
    mock_coordinator.subscribe_code = "sub_code_123"
    mock_coordinator.alarm_subscribe_code = "alarm_code_123"
    mock_coordinator.client.cancel_subscription = AsyncMock()
    mock_hass.data[DOMAIN] = {mock_entry.entry_id: mock_coordinator}
    mock_hass.config_entries.async_unload_platforms.return_value = True

    assert await async_unload_entry(mock_hass, mock_entry) is True

    mock_coordinator.client.cancel_subscription.assert_not_called()
    mock_hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_async_unload_entry_does_not_close_shared_session(mock_hass, mock_entry):
    """Regression guard: unload must never call .close()/.session.close().

    The client's session is HA's shared aiohttp session -- owned and closed
    by HA core itself, not by this integration. Closing it here would break
    every other integration still using the same shared session.
    """
    mock_coordinator = MagicMock()
    mock_coordinator.protection_controllers = {}
    mock_coordinator.engine = None
    mock_hass.data[DOMAIN] = {mock_entry.entry_id: mock_coordinator}
    mock_hass.config_entries.async_unload_platforms.return_value = True

    assert await async_unload_entry(mock_hass, mock_entry) is True

    mock_coordinator.client.close.assert_not_called()
    mock_coordinator.client.session.close.assert_not_called()


@pytest.mark.asyncio
async def test_async_remove_entry_cancels_subscriptions(mock_hass, mock_entry):
    """Permanent entry removal cancels both subscriptions remotely."""
    from custom_components.hyxi_cloud.__init__ import async_remove_entry

    mock_entry.data = {
        "access_key": "ak",
        "secret_key": "sk",
        "push_subscribe_code": "sub_code_123",
        "alarm_subscribe_code": "alarm_code_123",
    }

    with (
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient") as mock_client_cls,
        patch(
            "custom_components.hyxi_cloud.__init__.async_cancel_and_unregister_subscription",
            new_callable=AsyncMock,
        ) as mock_cancel,
    ):
        await async_remove_entry(mock_hass, mock_entry)

        assert mock_cancel.call_count == 2
        cancelled = {call.args[2] for call in mock_cancel.call_args_list}
        assert cancelled == {"sub_code_123", "alarm_code_123"}
        mock_client_cls.assert_called_once()


@pytest.mark.asyncio
async def test_async_remove_entry_survives_cancel_failure(mock_hass, mock_entry):
    """Removal must not raise even if the remote cancel fails."""
    from custom_components.hyxi_cloud.__init__ import async_remove_entry

    mock_entry.data = {
        "access_key": "ak",
        "secret_key": "sk",
        "push_subscribe_code": "sub_code_123",
    }

    with (
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
        patch(
            "custom_components.hyxi_cloud.__init__.async_cancel_and_unregister_subscription",
            new=AsyncMock(side_effect=RuntimeError("network error")),
        ),
    ):
        await async_remove_entry(mock_hass, mock_entry)


@pytest.mark.asyncio
async def test_async_remove_entry_no_codes_is_noop(mock_hass, mock_entry):
    """No stored subscription codes at all -- nothing to cancel, no client built."""
    from custom_components.hyxi_cloud.__init__ import async_remove_entry

    mock_entry.data = {"access_key": "ak", "secret_key": "sk"}

    with patch(
        "custom_components.hyxi_cloud.__init__.HyxiApiClient"
    ) as mock_client_cls:
        await async_remove_entry(mock_hass, mock_entry)

        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_async_remove_entry_no_credentials_is_noop(mock_hass, mock_entry):
    """Codes are stored but credentials are missing -- can't build a client
    to cancel them remotely, so bail out rather than raise."""
    from custom_components.hyxi_cloud.__init__ import async_remove_entry

    mock_entry.data = {"push_subscribe_code": "sub_code_123"}

    with patch(
        "custom_components.hyxi_cloud.__init__.HyxiApiClient"
    ) as mock_client_cls:
        await async_remove_entry(mock_hass, mock_entry)

        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_battery_protection_logs_and_continues_on_start_failure(
    mock_hass, mock_entry, caplog
):
    """If a protection controller's initial start fails (bus contention, a
    provider-controlled battery that will never accept a local write, a
    transient timeout), that must not take the whole config entry down --
    the controller stays registered and a warning is logged instead, so it
    can retry naturally on the next coordinator refresh."""
    import logging

    from custom_components.hyxi_cloud import _async_setup_battery_protection
    from custom_components.hyxi_cloud.protection import HyxiBatteryProtectionController

    mock_entry.options = {"enable_battery_control": True}
    coordinator = MagicMock()
    coordinator.entry = mock_entry
    coordinator.protection_controllers = {}
    coordinator.data = {
        "SN123": {"device_type_code": "1", "model": "H10K-HT"},  # three-phase hybrid
    }

    with patch.object(
        HyxiBatteryProtectionController,
        "async_start",
        AsyncMock(side_effect=RuntimeError("listener setup failed")),
    ):
        caplog.set_level(logging.WARNING)
        # Must not raise -- a single device's failed start no longer takes
        # the whole config entry down.
        await _async_setup_battery_protection(mock_hass, coordinator)

    # The controller stays registered (not cleaned up) so the coordinator's
    # own listener keeps it retrying on future refreshes.
    assert "SN123" in coordinator.protection_controllers
    assert any(
        "failed to start" in rec.message and "listener setup failed" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_async_setup_battery_protection_disabled_is_noop(mock_hass, mock_entry):
    """Battery control disabled by user settings -- no controllers created."""
    from custom_components.hyxi_cloud import _async_setup_battery_protection

    mock_entry.options = {"enable_battery_control": False}
    coordinator = MagicMock()
    coordinator.entry = mock_entry
    coordinator.protection_controllers = {}
    coordinator.data = {"SN123": {"device_type_code": "1", "model": "H10K-HT"}}

    await _async_setup_battery_protection(mock_hass, coordinator)

    assert not coordinator.protection_controllers


@pytest.mark.asyncio
async def test_async_setup_battery_protection_isolates_sibling_failures(
    mock_hass, mock_entry
):
    """One device's controller failing to start must not affect a sibling
    device's controller in the same setup batch -- each stays registered
    independently of whether its own start succeeded."""
    from custom_components.hyxi_cloud import _async_setup_battery_protection
    from custom_components.hyxi_cloud.protection import HyxiBatteryProtectionController

    mock_entry.options = {"enable_battery_control": True}
    coordinator = MagicMock()
    coordinator.entry = mock_entry
    coordinator.protection_controllers = {}
    coordinator.data = {
        "SN_FAIL": {"device_type_code": "1", "model": "H10K-HT"},  # three-phase
        "SN_OK": {"device_type_code": "1", "model": "H5K-HS"},  # single-phase
    }

    async def fake_start(self):
        if self._sn == "SN_FAIL":  # pylint: disable=protected-access
            raise RuntimeError("boom")

    with patch.object(HyxiBatteryProtectionController, "async_start", fake_start):
        # Must not raise -- SN_FAIL's failure is isolated to itself.
        await _async_setup_battery_protection(mock_hass, coordinator)

    assert "SN_FAIL" in coordinator.protection_controllers
    assert "SN_OK" in coordinator.protection_controllers


@pytest.mark.asyncio
async def test_async_unload_entry_failure(mock_hass, mock_entry):
    """Test failed unload of a config entry."""
    mock_coordinator = MagicMock()
    mock_coordinator.protection_controllers = {}
    mock_coordinator.engine = None
    mock_hass.data[DOMAIN] = {mock_entry.entry_id: mock_coordinator}
    mock_hass.config_entries.async_unload_platforms.return_value = False

    assert await async_unload_entry(mock_hass, mock_entry) is False

    mock_hass.config_entries.async_unload_platforms.assert_called_once_with(
        mock_entry, PLATFORMS
    )
    assert mock_entry.entry_id in mock_hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_reload_entry(mock_hass, mock_entry):
    """Test reload config entry."""

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.debug") as mock_logger:
        await async_reload_entry(mock_hass, mock_entry)

        mock_logger.assert_called_with(
            "HYXI: Options updated, reloading integration to apply new settings"
        )
        mock_hass.config_entries.async_reload.assert_called_once_with(
            mock_entry.entry_id
        )


@pytest.mark.asyncio
async def test_async_setup_entry_battery_first_class_device(mock_hass, mock_entry):
    """Test that bat_sn already in coordinator.data is linked, not re-stubbed.

    When a battery is discovered as a standalone device (it appears in
    coordinator.data with full metadata), Pass 2 must only set the via_device
    link rather than creating a sparse 'Battery {sn}' stub that would
    overwrite the richer entry registered in Pass 1.
    """
    with (
        patch(
            "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator"
        ) as mock_coordinator_class,
        patch("custom_components.hyxi_cloud.__init__.async_get_clientsession"),
        patch("custom_components.hyxi_cloud.__init__.HyxiApiClient"),
        patch("custom_components.hyxi_cloud.__init__.dr.async_get") as mock_dr_get,
        patch("custom_components.hyxi_cloud.__init__.er.async_get"),
        patch("custom_components.hyxi_cloud.__init__.async_reload_entry"),
    ):
        mock_coordinator = mock_coordinator_class.return_value
        mock_coordinator.async_preload_cache = AsyncMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.engine = None  # No EM engine
        mock_coordinator.data = {
            # Inverter knows about its battery via metrics
            "INVERTER_SN": {
                "device_name": "Hybrid Inverter",
                "model": "HYB-5K",
                "sw_version": "v2",
                "hw_version": "hw2",
                "metrics": {"batSn": "BATTERY_SN"},
            },
            # Battery is also a first-class device in its own right
            "BATTERY_SN": {
                "device_name": "Battery Pack",
                "model": "ESS-10",
                "sw_version": "v1",
                "hw_version": "hw1",
                "metrics": {},
            },
        }

        mock_registry = MagicMock()
        mock_dr_get.return_value = mock_registry

        result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True

        calls = mock_registry.async_get_or_create.call_args_list

        # Pass 1: 2 calls (INVERTER_SN, BATTERY_SN)
        # Pass 2: 1 call — link BATTERY_SN via_device to INVERTER_SN (guard path)
        assert mock_registry.async_get_or_create.call_count == 3

        # Pass 1 — INVERTER_SN registered with full metadata
        assert calls[0].kwargs["identifiers"] == {(DOMAIN, "INVERTER_SN")}
        assert calls[0].kwargs["name"] == "Hybrid Inverter"

        # Pass 1 — BATTERY_SN registered with full metadata (not a stub)
        assert calls[1].kwargs["identifiers"] == {(DOMAIN, "BATTERY_SN")}
        assert calls[1].kwargs["name"] == "Battery Pack"
        assert calls[1].kwargs["model"] == "ESS-10"

        # Pass 2 — guard path: link only, no name/model/serial overwrite
        assert calls[2].kwargs["identifiers"] == {(DOMAIN, "BATTERY_SN")}
        assert calls[2].kwargs["via_device"] == (DOMAIN, "INVERTER_SN")
        assert "name" not in calls[2].kwargs
        assert "model" not in calls[2].kwargs
        assert "serial_number" not in calls[2].kwargs


@pytest.mark.asyncio
async def test_remove_legacy_select_entities(mock_hass):
    """Test removal of legacy select entities."""
    from custom_components.hyxi_cloud.__init__ import _remove_legacy_select_entities

    with patch("custom_components.hyxi_cloud.__init__.er.async_get") as mock_er_get:
        mock_registry = MagicMock()
        mock_er_get.return_value = mock_registry

        # Setup side effect for async_get_entity_id
        # We want it to return an entity ID for 'hyxi_123_operating_mode' and 'hyxi_456_peak_shaving'
        # and None for others.
        def mock_get_entity_id(domain, component, unique_id):
            if unique_id == "hyxi_123_operating_mode":
                return "select.hyxi_123_operating_mode"
            if unique_id == "hyxi_456_peak_shaving":
                return "select.hyxi_456_peak_shaving"
            return None

        mock_registry.async_get_entity_id.side_effect = mock_get_entity_id

        # Test with two devices, one with both entities matched, one with neither
        devices: dict[str, dict] = {"123": {}, "456": {}}

        with patch(
            "custom_components.hyxi_cloud.__init__._LOGGER.debug"
        ) as mock_logger:
            _remove_legacy_select_entities(mock_hass, devices)

            # Check that the registry was fetched
            mock_er_get.assert_called_once_with(mock_hass)

            # Check that remove was called for the found entities
            assert mock_registry.async_remove.call_count == 2
            mock_registry.async_remove.assert_any_call("select.hyxi_123_operating_mode")
            mock_registry.async_remove.assert_any_call("select.hyxi_456_peak_shaving")

            # Check that it wasn't called for the not found entities (implied by call_count)

            # Check logging
            assert mock_logger.call_count == 2
            mock_logger.assert_any_call(
                "Removing legacy HYXI select entity %s",
                "select.hyxi_123_operating_mode",
            )
            mock_logger.assert_any_call(
                "Removing legacy HYXI select entity %s", "select.hyxi_456_peak_shaving"
            )


@pytest.mark.asyncio
async def test_migrate_vpp_dispatch_to_work_mode(mock_hass, mock_entry):
    """Test the vpp_dispatch -> work_mode unique_id migration."""
    from custom_components.hyxi_cloud.__init__ import (
        _migrate_vpp_dispatch_to_work_mode,
    )

    mock_entry.entry_id = "test_id"

    with patch("custom_components.hyxi_cloud.__init__.er.async_get") as mock_er_get:
        mock_registry = MagicMock()
        mock_er_get.return_value = mock_registry

        def mock_get_entity_id(domain, component, unique_id):
            if unique_id == "test_id_123_vpp_dispatch":
                return "binary_sensor.hyx_123_vpp_dispatch"
            return None

        mock_registry.async_get_entity_id.side_effect = mock_get_entity_id

        # Two devices: one with a pre-existing old-named entity to migrate,
        # one already on the new name (or never had the entity at all).
        devices: dict[str, dict] = {"123": {}, "456": {}}

        with patch(
            "custom_components.hyxi_cloud.__init__._LOGGER.debug"
        ) as mock_logger:
            _migrate_vpp_dispatch_to_work_mode(mock_hass, mock_entry, devices)

            mock_er_get.assert_called_once_with(mock_hass)
            mock_registry.async_update_entity.assert_called_once_with(
                "binary_sensor.hyx_123_vpp_dispatch",
                new_unique_id="test_id_123_work_mode",
            )
            mock_logger.assert_called_once_with(
                "Migrating %s from vpp_dispatch to work_mode unique_id",
                "binary_sensor.hyx_123_vpp_dispatch",
            )


@pytest.mark.asyncio
async def test_migrate_vpp_dispatch_to_work_mode_unique_id_collision(
    mock_hass, mock_entry
):
    """A previous migration attempt left both the legacy vpp_dispatch and
    the renamed work_mode entity registered for the same device.

    async_update_entity would raise ValueError on the unique_id collision
    and abort the whole config entry setup -- the migration must instead
    keep the work_mode entity (and its history) and drop the now-redundant
    legacy duplicate, without ever calling async_update_entity.
    """
    from custom_components.hyxi_cloud.__init__ import (
        _migrate_vpp_dispatch_to_work_mode,
    )

    mock_entry.entry_id = "test_id"

    with patch("custom_components.hyxi_cloud.__init__.er.async_get") as mock_er_get:
        mock_registry = MagicMock()
        mock_er_get.return_value = mock_registry

        def mock_get_entity_id(domain, component, unique_id):
            if unique_id == "test_id_123_vpp_dispatch":
                return "binary_sensor.hyx_123_vpp_dispatch"
            if unique_id == "test_id_123_work_mode":
                return "binary_sensor.hyx_123_work_mode"
            return None

        mock_registry.async_get_entity_id.side_effect = mock_get_entity_id

        devices: dict[str, dict] = {"123": {}}

        _migrate_vpp_dispatch_to_work_mode(mock_hass, mock_entry, devices)

        mock_registry.async_update_entity.assert_not_called()
        mock_registry.async_remove.assert_called_once_with(
            "binary_sensor.hyx_123_vpp_dispatch"
        )


def test_rekey_registry_entity_noop_when_ids_equal():
    """No lookups at all when there is nothing to move."""
    from custom_components.hyxi_cloud.__init__ import _rekey_registry_entity

    registry = MagicMock()
    _rekey_registry_entity(registry, "sensor", "hyxi_A_batSoc", "hyxi_A_batSoc")
    registry.async_get_entity_id.assert_not_called()


def test_rekey_registry_entity_noop_when_old_missing():
    """A no-op when the old unique_id isn't registered."""
    from custom_components.hyxi_cloud.__init__ import _rekey_registry_entity

    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    _rekey_registry_entity(registry, "sensor", "hyxi_A_batSoc", "hyxi_B_batSoc")
    registry.async_update_entity.assert_not_called()
    registry.async_remove.assert_not_called()


def test_rekey_registry_entity_drops_clashing_new_entry():
    """When new_unique_id is already taken, the newer entry is removed so the
    older, history-carrying one can take it."""
    from custom_components.hyxi_cloud.__init__ import _rekey_registry_entity

    registry = MagicMock()
    ids = {"hyxi_A_batSoc": "sensor.old", "hyxi_B_batSoc": "sensor.fresh"}
    registry.async_get_entity_id.side_effect = lambda d, c, uid: ids.get(uid)
    _rekey_registry_entity(registry, "sensor", "hyxi_A_batSoc", "hyxi_B_batSoc")
    registry.async_remove.assert_called_once_with("sensor.fresh")
    registry.async_update_entity.assert_called_once_with(
        "sensor.old", new_unique_id="hyxi_B_batSoc"
    )


def test_inverter_sn_via_device_resolution_paths():
    """Every fallback path of resolving an inverter serial from a battery
    device's via_device link."""
    from types import SimpleNamespace

    from custom_components.hyxi_cloud.__init__ import _inverter_sn_via_device

    reg = MagicMock()
    assert _inverter_sn_via_device(reg, None, {}) is None

    reg.async_get.return_value = None
    assert _inverter_sn_via_device(reg, "d1", {"INV": {}}) is None

    reg.async_get.return_value = SimpleNamespace(via_device_id=None)
    assert _inverter_sn_via_device(reg, "d1", {"INV": {}}) is None

    battery = SimpleNamespace(via_device_id="p1")
    reg.async_get.side_effect = lambda i: battery if i == "d1" else None
    assert _inverter_sn_via_device(reg, "d1", {"INV": {}}) is None

    parent_other = SimpleNamespace(identifiers={(DOMAIN, "OTHER")})
    reg.async_get.side_effect = lambda i: battery if i == "d1" else parent_other
    assert _inverter_sn_via_device(reg, "d1", {"INV": {}}) is None

    parent_inv = SimpleNamespace(identifiers={(DOMAIN, "INV")})
    reg.async_get.side_effect = lambda i: battery if i == "d1" else parent_inv
    assert _inverter_sn_via_device(reg, "d1", {"INV": {}}) == "INV"


@pytest.mark.asyncio
async def test_migrate_battery_sensor_unique_ids_filters_registry_entries(
    mock_hass, mock_entry
):
    """Only battery-key sensor entries that aren't already inverter-keyed and
    can be mapped to an inverter are re-keyed."""
    from types import SimpleNamespace

    from custom_components.hyxi_cloud.__init__ import (
        _migrate_battery_sensor_unique_ids,
    )

    mock_entry.entry_id = "eid"
    entries = [
        SimpleNamespace(  # wrong domain
            domain="number", unique_id="hyxi_BAT_batSoc", device_id=None
        ),
        SimpleNamespace(  # not a battery key
            domain="sensor", unique_id="hyxi_INV_totalE", device_id=None
        ),
        SimpleNamespace(  # already inverter-keyed
            domain="sensor", unique_id="hyxi_INV_batSoc", device_id=None
        ),
        SimpleNamespace(  # battery-keyed but unmappable (no batSn, no device)
            domain="sensor", unique_id="hyxi_GHOST_batSoh", device_id=None
        ),
        SimpleNamespace(  # battery-keyed, mappable via current batSn
            domain="sensor", unique_id="hyxi_BAT_batP", device_id=None
        ),
    ]

    with (
        patch("custom_components.hyxi_cloud.__init__.er.async_get"),
        patch("custom_components.hyxi_cloud.__init__.dr.async_get"),
        patch(
            "custom_components.hyxi_cloud.__init__.er.async_entries_for_config_entry",
            return_value=entries,
        ),
        patch(
            "custom_components.hyxi_cloud.__init__._rekey_registry_entity"
        ) as mock_rekey,
    ):
        _migrate_battery_sensor_unique_ids(
            mock_hass, mock_entry, {"INV": {"metrics": {"batSn": "BAT"}}}
        )

    mock_rekey.assert_called_once()
    assert mock_rekey.call_args[0][2:] == ("hyxi_BAT_batP", "hyxi_INV_batP")


def test_battery_serial_to_inverter_map_excludes_ambiguous_and_junk():
    """Blank/non-string serials, self/first-class-device serials, and serials
    reported by more than one inverter are all left out of the map."""
    from custom_components.hyxi_cloud.__init__ import _battery_serial_to_inverter

    devices = {
        "INV_A": {"metrics": {"batSn": "SHARED"}},
        "INV_B": {"metrics": {"batSn": "SHARED"}},  # same serial -> ambiguous
        "INV_C": {"metrics": {"batSn": "   "}},  # blank
        "INV_D": {"metrics": {"batSn": 0}},  # non-string (Modbus junk)
        "INV_E": {"metrics": {"batSn": "INV_E"}},  # serial == own sn
        "INV_F": {"metrics": {"batSn": "BAT_F"}},  # the one good mapping
        "BAT_G": {"metrics": {}},  # first-class battery device
        "INV_H": {"metrics": {"batSn": "BAT_G"}},  # points at a real device
    }

    assert _battery_serial_to_inverter(devices) == {"BAT_F": "INV_F"}


@pytest.mark.asyncio
async def test_remove_work_mode_sensor_for_modbus(mock_hass, mock_entry):
    """A Modbus entry's pre-existing work_mode entity (from before it was
    gated out, or from switching a device from cloud to Modbus) is removed
    from the registry rather than left dangling."""
    from custom_components.hyxi_cloud.__init__ import (
        _remove_work_mode_sensor_for_modbus,
    )

    mock_entry.entry_id = "test_id"
    mock_entry.data = {"transport": "modbus"}

    with patch("custom_components.hyxi_cloud.__init__.er.async_get") as mock_er_get:
        mock_registry = MagicMock()
        mock_er_get.return_value = mock_registry

        def mock_get_entity_id(domain, component, unique_id):
            if unique_id == "test_id_123_work_mode":
                return "binary_sensor.hyx_123_work_mode"
            return None

        mock_registry.async_get_entity_id.side_effect = mock_get_entity_id

        devices: dict[str, dict] = {"123": {}, "456": {}}

        _remove_work_mode_sensor_for_modbus(mock_hass, mock_entry, devices)

        mock_registry.async_remove.assert_called_once_with(
            "binary_sensor.hyx_123_work_mode"
        )


@pytest.mark.asyncio
async def test_remove_work_mode_sensor_for_modbus_is_noop_for_cloud(
    mock_hass, mock_entry
):
    """A cloud entry still gets HyxiWorkModeSensor -- this must never touch
    the registry for one, not even to check whether the entity exists."""
    from custom_components.hyxi_cloud.__init__ import (
        _remove_work_mode_sensor_for_modbus,
    )

    with patch("custom_components.hyxi_cloud.__init__.er.async_get") as mock_er_get:
        _remove_work_mode_sensor_for_modbus(mock_hass, mock_entry, {"123": {}})

        mock_er_get.assert_not_called()


@pytest.mark.asyncio
async def test_remove_alarm_entities_for_modbus(mock_hass, mock_entry):
    """A Modbus entry's pre-existing device_alarm/clear_alarms entities
    (from before they were gated out, or from switching a device from
    cloud to Modbus) are removed from the registry rather than left
    dangling."""
    from custom_components.hyxi_cloud.__init__ import (
        _remove_alarm_entities_for_modbus,
    )

    mock_entry.entry_id = "test_id"
    mock_entry.data = {"transport": "modbus"}

    with patch("custom_components.hyxi_cloud.__init__.er.async_get") as mock_er_get:
        mock_registry = MagicMock()
        mock_er_get.return_value = mock_registry

        def mock_get_entity_id(domain, component, unique_id):
            if (domain, unique_id) == ("binary_sensor", "test_id_123_device_alarm"):
                return "binary_sensor.hyx_123_device_alarm"
            if (domain, unique_id) == ("button", "hyxi_123_clear_alarms"):
                return "button.hyxi_123_clear_alarms"
            return None

        mock_registry.async_get_entity_id.side_effect = mock_get_entity_id

        devices: dict[str, dict] = {"123": {}, "456": {}}

        _remove_alarm_entities_for_modbus(mock_hass, mock_entry, devices)

        assert mock_registry.async_remove.call_args_list == [
            call("binary_sensor.hyx_123_device_alarm"),
            call("button.hyxi_123_clear_alarms"),
        ]


@pytest.mark.asyncio
async def test_remove_alarm_entities_for_modbus_is_noop_for_cloud(
    mock_hass, mock_entry
):
    """A cloud entry still gets both alarm entities -- this must never
    touch the registry for either, not even to check whether they exist."""
    from custom_components.hyxi_cloud.__init__ import (
        _remove_alarm_entities_for_modbus,
    )

    with patch("custom_components.hyxi_cloud.__init__.er.async_get") as mock_er_get:
        _remove_alarm_entities_for_modbus(mock_hass, mock_entry, {"123": {}})

        mock_er_get.assert_not_called()


# --- __init__.py Platform Tests ---

from custom_components.hyxi_cloud.__init__ import (
    _async_handle_alarm_webhook,
    _async_handle_webhook,
    _async_resolve_webhook_url,
    _async_setup_alarm_subscription,
    _async_setup_push_subscription,
    _async_teardown_alarm_subscription,
    _async_teardown_push_subscription,
)


@pytest.mark.asyncio
async def test_async_reload_entry_options_not_changed():
    """Verify async_reload_entry returns early when options haven't changed."""
    mock_hass = MagicMock()
    mock_hass.data = {DOMAIN: {"entry_id": MagicMock()}}
    mock_entry = MagicMock()
    mock_entry.entry_id = "entry_id"
    mock_entry.options = {"opt": "val"}

    # We populate the coordinator options to match entry options
    coordinator = mock_hass.data[DOMAIN]["entry_id"]
    coordinator.options = {"opt": "val"}

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.debug") as mock_log:
        await async_reload_entry(mock_hass, mock_entry)
        mock_log.assert_any_call(
            "HYXI: Config entry data updated, skipping reload as options did not change"
        )
        mock_hass.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_async_resolve_webhook_url(mock_hass):
    """Verify webhook URL resolution paths including cloud hooks and fallbacks."""
    # Ensure hass.config.external_url doesn't raise error on yarl.URL parsing
    mock_hass.config = MagicMock()
    mock_hass.config.external_url = "https://default.url"

    # 1. Custom URL is configured
    res1 = await _async_resolve_webhook_url(
        mock_hass, "web_id", "https://my.custom.url/"
    )
    assert res1 == "https://my.custom.url/api/webhook/web_id"

    # 1b. Custom URL is unencrypted HTTP (should be rejected)
    res1b = await _async_resolve_webhook_url(
        mock_hass, "web_id", "http://my.custom.url/"
    )
    assert res1b is None

    # 2. Cloud hooks resolution (Nabu Casa subscription active)
    with patch(
        "homeassistant.components.cloud.async_active_subscription", return_value=True
    ):
        # 2a. Cloud hook successfully created
        with patch(
            "homeassistant.components.cloud.async_get_or_create_cloudhook",
            new=AsyncMock(return_value="https://cloud.hook/web_id"),
        ):
            res2 = await _async_resolve_webhook_url(mock_hass, "web_id", None)
            assert res2 == "https://cloud.hook/web_id"

        # 2b. Cloud hook raises error
        with patch(
            "homeassistant.components.cloud.async_get_or_create_cloudhook",
            new=AsyncMock(side_effect=Exception("cloud_err")),
        ):
            # It falls back to standard external settings because Exception isn't CloudNotAvailable
            with patch(
                "homeassistant.helpers.network.get_url",
                return_value="https://local.url",
            ):
                res3 = await _async_resolve_webhook_url(mock_hass, "web_id", None)
                assert res3 == "https://local.url/api/webhook/web_id"

    # 3. No Nabu Casa, network.get_url raises NoURLAvailableError
    from homeassistant.helpers.network import NoURLAvailableError

    with patch(
        "homeassistant.components.cloud.async_active_subscription", return_value=False
    ):
        with patch(
            "homeassistant.helpers.network.get_url", side_effect=NoURLAvailableError
        ):
            res4 = await _async_resolve_webhook_url(mock_hass, "web_id", None)
            assert res4 is None


@pytest.mark.asyncio
async def test_async_setup_push_subscription_no_url_or_devices():
    """Verify push subscription setup failure when URL or devices are missing."""
    hass = MagicMock()
    entry = MagicMock()
    entry.options = {"enable_realtime_push": True}
    coordinator = MagicMock()
    coordinator.data = {}  # No devices

    # 1. Webhook URL cannot be resolved
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value=None,
    ):
        await _async_setup_push_subscription(hass, entry, coordinator)
        assert coordinator.push_status == "error"
        assert "Could not resolve external URL" in coordinator.push_error

    # 2. Webhook URL resolved but no devices available
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_push_subscription(hass, entry, coordinator)
        assert coordinator.push_status == "inactive"


@pytest.mark.asyncio
async def test_async_setup_push_subscription_client_failure_or_error():
    """Verify push subscription client failure paths."""
    hass = MagicMock()
    entry = MagicMock()
    entry.options = {"enable_realtime_push": True}
    coordinator = MagicMock()
    coordinator.data = {"SN123": {}}

    # 1. SDK returns success=False
    coordinator.client.subscribe_real_time_data = AsyncMock(
        return_value={"success": False, "msg": "API Limit exceeded"}
    )
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_push_subscription(hass, entry, coordinator)
        assert coordinator.push_status == "error"
        assert coordinator.push_error == "API Limit exceeded"

    # 1b. SDK returns success=False with repeatedly error (B004002)
    coordinator.client.subscribe_real_time_data = AsyncMock(
        return_value={"success": False, "msg": "subscribed repeatedly (B004002)"}
    )
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_push_subscription(hass, entry, coordinator)
        assert coordinator.push_status == "error"
        assert coordinator.push_error == "subscribed repeatedly (B004002)"

    # 2. SDK raises exception
    coordinator.client.subscribe_real_time_data = AsyncMock(
        side_effect=Exception("conn_error")
    )
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_push_subscription(hass, entry, coordinator)
        assert coordinator.push_status == "error"
        assert coordinator.push_error == "conn_error"

    # 2b. SDK raises exception containing B004002
    coordinator.client.subscribe_real_time_data = AsyncMock(
        side_effect=Exception("Error B004002: subscribed repeatedly")
    )
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_push_subscription(hass, entry, coordinator)
        assert coordinator.push_status == "error"
        assert coordinator.push_error == "Error B004002: subscribed repeatedly"


@pytest.mark.asyncio
async def test_webhook_handle_auth_fails():
    """Verify webhook handles unauthorized requests securely."""
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"

    request = MagicMock()
    request.headers = {"accessKey": "wrong_ak"}

    res = await _async_handle_webhook("webhook_id", request, coordinator)
    assert res.status == 401


@pytest.mark.asyncio
async def test_webhook_handle_auth_fails_missing_access_key_header():
    """Verify webhook rejects a request with no accessKey header at all."""
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"

    request = MagicMock()
    request.headers = {}

    res = await _async_handle_webhook("webhook_id", request, coordinator)
    assert res.status == 401


@pytest.mark.asyncio
async def test_webhook_handle_invalid_json():
    """Verify webhook handles invalid JSON payloads gracefully."""
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"

    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.text = AsyncMock(return_value="{bad json}")

    res = await _async_handle_webhook("webhook_id", request, coordinator)
    assert res.status == 400


@pytest.mark.asyncio
async def test_webhook_handle_url_encoded_payload_fallback():
    """A body that isn't raw JSON but is URL-encoded form data with a
    'payload' field (as some platforms send) is still parsed successfully."""
    import json
    from urllib.parse import urlencode

    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"
    coordinator.data = {"SN123": {}}
    coordinator.client.process_push_data = MagicMock(
        return_value={"SN123": {"sn": "SN123", "metrics": {"batSoc": 50}}}
    )

    inner_payload = json.dumps({"dataList": [{"deviceSn": "SN123", "batSoc": 50}]})
    body = urlencode({"payload": inner_payload})

    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.text = AsyncMock(return_value=body)

    res = await _async_handle_webhook("webhook_id", request, coordinator)
    assert res.status == 200


@pytest.mark.asyncio
async def test_webhook_handle_process_exceptions():
    """Verify webhook handles process payload exceptions gracefully."""
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"
    coordinator.data = {}

    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.text = AsyncMock(return_value='{"data": "raw"}')
    coordinator.client.process_push_data = MagicMock(side_effect=Exception("sdk_error"))

    res = await _async_handle_webhook("webhook_id", request, coordinator)
    assert res.status == 500


@pytest.mark.asyncio
async def test_webhook_handle_untracked_device():
    """Verify webhook handles push data for untracked devices."""
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"
    coordinator.data = {"SN123": {}}

    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.text = AsyncMock(return_value="{}")

    # process_push_data returns updates for untracked device SN999
    coordinator.client.process_push_data = MagicMock(
        return_value={"SN999": {"metrics": {"batSoc": 80}}}
    )

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.debug") as mock_debug:
        res = await _async_handle_webhook("webhook_id", request, coordinator)
        assert res.status == 200
        assert (
            mock_debug.call_args[0][0]
            == "Received push data for untracked device SN: %s"
        )


@pytest.mark.asyncio
async def test_alarm_subscription_failures_and_webhooks():
    """Verify alarm subscription setup failures and webhook handling."""
    hass = MagicMock()
    entry = MagicMock()
    entry.options = {"enable_realtime_push": True}
    coordinator = MagicMock()
    coordinator.data = {"SN123": {}}
    coordinator.client.access_key = "correct_ak"

    # 1. Webhook URL unresolved
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value=None,
    ):
        await _async_setup_alarm_subscription(hass, entry, coordinator)
        assert coordinator.alarm_push_status == "error"

    # 2. No devices available
    coordinator.data = {}
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_alarm_subscription(hass, entry, coordinator)
        assert coordinator.alarm_push_status == "inactive"

    # 3. Client returns failure
    coordinator.data = {"SN123": {}}
    coordinator.client.subscribe_alarm = AsyncMock(
        return_value={"success": False, "msg": "failed"}
    )
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_alarm_subscription(hass, entry, coordinator)
        assert coordinator.alarm_push_status == "error"

    # 3b. Client returns failure with B004002
    coordinator.client.subscribe_alarm = AsyncMock(
        return_value={"success": False, "msg": "subscribed repeatedly (B004002)"}
    )
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_alarm_subscription(hass, entry, coordinator)
        assert coordinator.alarm_push_status == "error"

    # 4. Client raises exception
    coordinator.client.subscribe_alarm = AsyncMock(side_effect=Exception("err"))
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_alarm_subscription(hass, entry, coordinator)
        assert coordinator.alarm_push_status == "error"

    # 4b. Client raises exception with B004002
    coordinator.client.subscribe_alarm = AsyncMock(
        side_effect=Exception("err repeatedly B004002")
    )
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://url",
    ):
        await _async_setup_alarm_subscription(hass, entry, coordinator)
        assert coordinator.alarm_push_status == "error"

    # 5. Alarm Webhook: auth fails
    request = MagicMock()
    request.headers = {"accessKey": "wrong_ak"}
    res_auth = await _async_handle_alarm_webhook(
        "alarm_webhook_id", request, coordinator
    )
    assert res_auth.status == 401

    # 6. Alarm Webhook: invalid JSON
    request.headers = {"accessKey": "correct_ak"}
    request.text = AsyncMock(return_value="{bad json}")
    res_json = await _async_handle_alarm_webhook(
        "alarm_webhook_id", request, coordinator
    )
    assert res_json.status == 400

    # 7. Alarm Webhook: process raises exception
    request.text = AsyncMock(return_value="{}")
    coordinator.client.process_alarm_push_data = MagicMock(
        side_effect=Exception("sdk_err")
    )
    res_err = await _async_handle_alarm_webhook(
        "alarm_webhook_id", request, coordinator
    )
    assert res_err.status == 500

    # 8. Alarm Webhook: untracked device SN
    coordinator.client.process_alarm_push_data = MagicMock(
        return_value={"SN999": [{"alarmCode": "100"}]}
    )
    coordinator.data = {"SN123": {}}
    with patch("custom_components.hyxi_cloud.__init__._LOGGER.warning") as mock_warn:
        res_ok = await _async_handle_alarm_webhook(
            "alarm_webhook_id", request, coordinator
        )
        assert res_ok.status == 200
        assert (
            mock_warn.call_args[0][0]
            == "HYXI Alarm Push: received alarm for untracked device SN: %s"
        )


@pytest.mark.asyncio
async def test_additional_init_coverage(mock_hass, mock_entry):
    """Test additional branches and fallback paths in __init__.py for 100% coverage."""

    # 1. Test ValueError raised by Nabu Casa resolved URL (line 345)
    class CustomCloudNotAvailable(BaseException):
        pass

    import homeassistant.components.cloud as cloud

    cloud.CloudNotAvailable = CustomCloudNotAvailable  # type: ignore[assignment,misc]

    with patch(
        "homeassistant.components.cloud.async_active_subscription", return_value=True
    ):
        with patch(
            "homeassistant.components.cloud.async_get_or_create_cloudhook",
            new=AsyncMock(side_effect=ValueError("real_val_err")),
        ):
            with pytest.raises(ValueError, match="real_val_err"):
                await _async_resolve_webhook_url(mock_hass, "web_id", None)

    # 2. Test successful real-time push subscription (lines 449-456)
    from custom_components.hyxi_cloud.const import CONF_ACCESS_KEY, CONF_SECRET_KEY

    mock_entry.data = {
        CONF_ACCESS_KEY: "test_access",
        CONF_SECRET_KEY: "test_secret",
    }
    mock_entry.options = {
        "enable_realtime_push": True,
        "enable_push": True,
    }

    coordinator = MagicMock()
    coordinator.data = {
        "SN123": {
            "device_name": "Test Inverter",
            "model": "hybrid",
            "device_type_code": "1",
        }
    }
    coordinator.protection_controllers = {}
    coordinator.engine = None
    coordinator.webhook_id = None
    coordinator.subscribe_code = None
    coordinator.client.access_key = "correct_ak"
    coordinator.client.cancel_subscription = AsyncMock()

    # Success response from real time subscription
    coordinator.client.subscribe_real_time_data = AsyncMock(
        return_value={"success": True, "data": {"subscribeCode": "sub_code_123"}}
    )

    # Success response from alarm subscription
    coordinator.client.subscribe_alarm = AsyncMock(
        return_value={"success": True, "data": {"subscribeCode": "alarm_code_123"}}
    )

    # Mock resolves webhook URL successfully
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        return_value="https://webhook.url",
    ):
        await _async_setup_push_subscription(mock_hass, mock_entry, coordinator)
        assert coordinator.push_status == "active"
        assert coordinator.subscribe_code == "sub_code_123"

        await _async_setup_alarm_subscription(mock_hass, mock_entry, coordinator)
        assert coordinator.alarm_push_status == "active"
        assert coordinator.alarm_subscribe_code == "alarm_code_123"

    # 3. Webhook registration already registered (ValueError) (lines 407-409, 628-629)
    with patch(
        "homeassistant.components.webhook.async_register",
        side_effect=ValueError("Already registered"),
    ):
        with patch(
            "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
            return_value="https://webhook.url",
        ):
            # These should not crash (they catch ValueError)
            await _async_setup_push_subscription(mock_hass, mock_entry, coordinator)
            await _async_setup_alarm_subscription(mock_hass, mock_entry, coordinator)

    # 4. Webhook unregister raises KeyError (lines 479-481, 693-695)
    with patch(
        "homeassistant.components.webhook.async_unregister",
        side_effect=KeyError("Not found"),
    ):
        coordinator.webhook_id = "test_webhook"
        coordinator.alarm_webhook_id = "test_alarm_webhook"
        await _async_teardown_push_subscription(mock_hass, coordinator, mock_entry)
        await _async_teardown_alarm_subscription(mock_hass, coordinator, mock_entry)
        assert coordinator.webhook_id is None
        assert coordinator.alarm_webhook_id is None

    # 5. Push data webhook process with empty results (line 549)
    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.text = AsyncMock(return_value="{}")
    coordinator.client.process_push_data = MagicMock(return_value={})
    res = await _async_handle_webhook("web_id", request, coordinator)
    assert res.status == 200

    # 6. Push data webhook with coordinator.data is None (line 554)
    coordinator.data = None
    coordinator.client.process_push_data = MagicMock(
        return_value={"SN123": {"metrics": {"batSoc": 85}}}
    )
    from custom_components.hyxi_cloud.const import mask_sn

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.debug") as mock_debug:
        res = await _async_handle_webhook("web_id", request, coordinator)
        assert res.status == 200
        assert coordinator.data == {}
        # SN123 is untracked now
        mock_debug.assert_any_call(
            "Received push data for untracked device SN: %s", mask_sn("SN123")
        )

    # 7. Push data webhook updates successfully (line 577-580)
    coordinator.data = {"SN123": {"metrics": {}}}
    coordinator.async_update_listeners = MagicMock()
    res = await _async_handle_webhook("web_id", request, coordinator)
    assert res.status == 200
    assert coordinator.data["SN123"]["metrics"] == {"batSoc": 85}
    coordinator.async_update_listeners.assert_called_once()

    # 8. Alarm push webhook empty results (line 755)
    coordinator.client.process_alarm_push_data = MagicMock(return_value={})
    res = await _async_handle_alarm_webhook("alarm_web_id", request, coordinator)
    assert res.status == 200

    # 9. Alarm push webhook with coordinator.data is None (line 758)
    coordinator.data = None
    coordinator.client.process_alarm_push_data = MagicMock(
        return_value={"SN123": [{"alarmCode": "99"}]}
    )
    with patch("custom_components.hyxi_cloud.__init__._LOGGER.warning") as mock_warn:
        res = await _async_handle_alarm_webhook("alarm_web_id", request, coordinator)
        assert res.status == 200
        assert coordinator.data == {}
        mock_warn.assert_any_call(
            "HYXI Alarm Push: received alarm for untracked device SN: %s",
            mask_sn("SN123"),
        )

    # 10. Alarm push webhook merges alarm records successfully (lines 770-783, 790)
    coordinator.data = {"SN123": {"alarms": [{"alarmCode": "99", "msg": "old"}]}}
    coordinator.async_update_listeners = MagicMock()
    coordinator.client.process_alarm_push_data = MagicMock(
        return_value={
            "SN123": [
                {"alarmCode": "99", "msg": "new"},
                {"alarmCode": "100", "msg": "another"},
            ]
        }
    )
    res = await _async_handle_alarm_webhook("alarm_web_id", request, coordinator)
    assert res.status == 200
    assert len(coordinator.data["SN123"]["alarms"]) == 2
    # Ensure alarm with code "99" was updated
    alarms_by_code = {a["alarmCode"]: a for a in coordinator.data["SN123"]["alarms"]}
    assert alarms_by_code["99"]["msg"] == "new"
    coordinator.async_update_listeners.assert_called_once()

    # 11. Battery protection setup with invalid phase type (line 303)
    from custom_components.hyxi_cloud import _async_setup_battery_protection

    coordinator.entry = mock_entry
    # Battery control enabled
    mock_entry.options = {"enable_battery_control": True, "charge_power": 1000}
    coordinator.data = {
        "SN123": {"device_type_code": "1", "phase_type": "invalid_phase"}
    }
    # Should complete without error and not create a protection controller
    await _async_setup_battery_protection(mock_hass, coordinator)
    assert not coordinator.protection_controllers

    # 12. Cleanup control entities (lines 242, 277-283)
    # 12a. When battery control is enabled, cleanup returns early (line 242)
    from custom_components.hyxi_cloud import _cleanup_control_entities

    with patch("homeassistant.helpers.entity_registry.async_get") as mock_er:
        _cleanup_control_entities(mock_hass, mock_entry, coordinator)
        mock_er.assert_not_called()

    # 12b. When battery control is disabled, remove specific control entities (lines 277-283)
    mock_entry.options = {"enable_battery_control": False}
    coordinator.data = {"SN123": {}}
    mock_registry = MagicMock()
    # Mock entries in registry belonging to this config entry
    mock_reg_entry = MagicMock()
    mock_reg_entry.unique_id = "hyxi_SN123_mode_idle"
    mock_reg_entry.entity_id = "button.hyxi_SN123_mode_idle"
    mock_reg_entry.domain = "button"

    with patch(
        "homeassistant.helpers.entity_registry.async_get", return_value=mock_registry
    ):
        with patch(
            "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
            return_value=[mock_reg_entry],
        ):
            _cleanup_control_entities(mock_hass, mock_entry, coordinator)
            mock_registry.async_remove.assert_called_once_with(
                "button.hyxi_SN123_mode_idle"
            )

    # 12c. Modbus-only device-scoped control entities (setting numbers, the
    # anti-starvation switch, micro_ess_power, hybrid power commands) and
    # the entry-scoped refresh_settings button must also be cleaned up --
    # these were missing from keys_to_remove and would otherwise linger as
    # "unavailable" entities forever once battery control was turned off.
    mock_entry.options = {"enable_battery_control": False}
    coordinator.data = {"SN123": {}}
    mock_registry = MagicMock()
    modbus_reg_entries = [
        MagicMock(
            unique_id="hyxi_SN123_vpp_min_soc",
            entity_id="number.hyxi_SN123_vpp_min_soc",
            domain="number",
        ),
        MagicMock(
            unique_id="hyxi_SN123_anti_starvation",
            entity_id="switch.hyxi_SN123_anti_starvation",
            domain="switch",
        ),
        MagicMock(
            unique_id="hyxi_SN123_micro_ess_power",
            entity_id="switch.hyxi_SN123_micro_ess_power",
            domain="switch",
        ),
        MagicMock(
            unique_id="hyxi_SN123_power_on",
            entity_id="button.hyxi_SN123_power_on",
            domain="button",
        ),
        MagicMock(
            unique_id=f"{mock_entry.entry_id}_refresh_settings",
            entity_id="button.hyxi_modbus_service_refresh_settings",
            domain="button",
        ),
        MagicMock(
            unique_id="hyxi_SN123_unrelated_sensor",
            entity_id="sensor.hyxi_SN123_unrelated",
            domain="sensor",
        ),
    ]

    with patch(
        "homeassistant.helpers.entity_registry.async_get", return_value=mock_registry
    ):
        with patch(
            "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
            return_value=modbus_reg_entries,
        ):
            _cleanup_control_entities(mock_hass, mock_entry, coordinator)

    removed = {call.args[0] for call in mock_registry.async_remove.call_args_list}
    assert removed == {
        "number.hyxi_SN123_vpp_min_soc",
        "switch.hyxi_SN123_anti_starvation",
        "switch.hyxi_SN123_micro_ess_power",
        "button.hyxi_SN123_power_on",
        "button.hyxi_modbus_service_refresh_settings",
    }

    # 13. Setup and Unload with Energy Manager and Protection Controllers enabled
    from custom_components.hyxi_cloud.const import (
        CONF_EM_ENABLED,
        CONF_EM_INVERTER_SN,
        CONF_EM_P1_ENTITY,
        DOMAIN,
    )

    # Reset mock_entry
    mock_entry.options = {
        CONF_EM_ENABLED: True,
        CONF_EM_INVERTER_SN: "SN123",
        CONF_EM_P1_ENTITY: "sensor.p1",
        "enable_battery_control": True,
    }

    # Re-init coordinator
    coordinator.data = {
        "SN123": {
            "device_name": "Test Inverter",
            "model": "hybrid-HT",
            "device_type_code": "1",
            "phase_type": "three_phase",
        }
    }
    coordinator.protection_controllers = {}
    coordinator.engine = None
    coordinator.entry = mock_entry
    coordinator.async_preload_cache = AsyncMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()

    # Mock engine instance
    mock_engine = MagicMock()
    mock_engine.start = MagicMock()
    mock_engine.stop = MagicMock()

    # Mock protection controller
    mock_controller = MagicMock()
    mock_controller.async_start = AsyncMock()
    mock_controller.stop = MagicMock()

    with patch(
        "custom_components.hyxi_cloud.engine.EnergyManagerEngine",
        return_value=mock_engine,
    ):
        with patch(
            "custom_components.hyxi_cloud.__init__.HyxiBatteryProtectionController",
            return_value=mock_controller,
        ):
            with patch(
                "custom_components.hyxi_cloud.__init__.HyxiDataUpdateCoordinator",
                return_value=coordinator,
            ):
                with patch(
                    "custom_components.hyxi_cloud.__init__._remove_legacy_select_entities"
                ):
                    with (
                        patch(
                            "custom_components.hyxi_cloud.__init__._migrate_vpp_dispatch_to_work_mode"
                        ),
                        patch(
                            "custom_components.hyxi_cloud.__init__._migrate_battery_sensor_unique_ids"
                        ),
                        patch(
                            "custom_components.hyxi_cloud.__init__._merge_duplicate_battery_energy_sensors"
                        ),
                        patch(
                            "custom_components.hyxi_cloud.__init__._migrate_microinverter_sum_identifiers"
                        ),
                    ):
                        with patch(
                            "custom_components.hyxi_cloud.__init__._cleanup_control_entities"
                        ):
                            with patch(
                                "custom_components.hyxi_cloud.__init__.dr.async_get"
                            ):
                                with patch(
                                    "custom_components.hyxi_cloud.__init__.async_get_clientsession"
                                ):
                                    with patch(
                                        "custom_components.hyxi_cloud.__init__.HyxiApiClient"
                                    ):
                                        # Run setup
                                        res_setup = await async_setup_entry(
                                            mock_hass, mock_entry
                                        )
                                        assert res_setup is True
                                        assert coordinator.engine is mock_engine
                                        mock_engine.start.assert_called_once()
                                        mock_controller.async_start.assert_called_once()

                                        # Set up data in mock_hass.data for unload
                                        mock_hass.data[DOMAIN] = {
                                            mock_entry.entry_id: coordinator
                                        }

                                        # Run unload
                                        res_unload = await async_unload_entry(
                                            mock_hass, mock_entry
                                        )
                                        assert res_unload is True
                                        mock_engine.stop.assert_called_once()
                                        mock_controller.stop.assert_called_once()


@pytest.mark.asyncio
async def test_alarm_webhook_url_encoded_payload_fallback():
    """A body that isn't raw JSON but is URL-encoded form data with a
    'payload' field is still parsed successfully (mirrors the same fallback
    in the data-push webhook handler)."""
    import json
    from urllib.parse import urlencode

    from custom_components.hyxi_cloud.__init__ import _async_handle_alarm_webhook

    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"
    coordinator.data = {"SN123": {}}
    coordinator.client.process_alarm_push_data = MagicMock(return_value={})

    inner_payload = json.dumps({"alarmList": []})
    body = urlencode({"payload": inner_payload})

    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.text = AsyncMock(return_value=body)

    res = await _async_handle_alarm_webhook("alarm_web_id", request, coordinator)
    assert res.status == 200


@pytest.mark.asyncio
async def test_alarm_webhook_logs_masked_alarm_details_at_debug(caplog):
    """When debug logging is enabled, merged alarm records are logged with
    sensitive fields masked."""
    import logging

    from custom_components.hyxi_cloud.__init__ import _async_handle_alarm_webhook

    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"
    coordinator.data = {"SN123": {"alarms": []}}
    coordinator.client.process_alarm_push_data = MagicMock(
        return_value={"SN123": [{"alarmCode": "1", "sn": "SN123_full_serial"}]}
    )

    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.text = AsyncMock(return_value='{"alarmList": []}')

    caplog.set_level(logging.DEBUG)
    res = await _async_handle_alarm_webhook("alarm_web_id", request, coordinator)

    assert res.status == 200
    assert any(
        "HYXI Alarm Push Telemetry Update" in rec.message for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_async_setup_push_deactivation_cleanup(mock_hass, mock_entry):
    """Verify push deactivation cleans up active/stored subscription codes."""
    from custom_components.hyxi_cloud.const import CONF_ENABLE_PUSH

    mock_entry.options = {CONF_ENABLE_PUSH: False}
    mock_entry.data = {
        "push_subscribe_code": "sub_code_123",
        "alarm_subscribe_code": "alarm_code_123",
    }

    coordinator = MagicMock()
    coordinator.client = MagicMock()
    coordinator.client.cancel_subscription = AsyncMock(return_value={"success": True})

    with patch(
        "custom_components.hyxi_cloud.__init__.async_cancel_and_unregister_subscription",
        new=AsyncMock(),
    ) as mock_cancel:
        await _async_setup_push_subscription(mock_hass, mock_entry, coordinator)
        await _async_setup_alarm_subscription(mock_hass, mock_entry, coordinator)

        # Verify cancel and unregister was called for both
        assert mock_cancel.call_count == 2
        mock_cancel.assert_any_call(mock_hass, coordinator.client, "sub_code_123")
        mock_cancel.assert_any_call(mock_hass, coordinator.client, "alarm_code_123")

        # Verify config entry data was updated to clear the codes
        mock_hass.config_entries.async_update_entry.assert_any_call(
            mock_entry,
            data={
                **mock_entry.data,
                "push_subscribe_code": None,
                "push_subscribe_fingerprint": None,
            },
        )
        mock_hass.config_entries.async_update_entry.assert_any_call(
            mock_entry,
            data={
                **mock_entry.data,
                "alarm_subscribe_code": None,
                "alarm_subscribe_fingerprint": None,
            },
        )


@pytest.mark.asyncio
async def test_async_setup_push_deactivation_preserves_code_on_cancel_failure(
    mock_hass, mock_entry
):
    """A failed cancel (e.g. transient/network error) must NOT wipe the
    persisted subscription code -- it's the only way to recover the
    account's one push subscription slot without contacting the supplier."""
    from custom_components.hyxi_cloud.const import CONF_ENABLE_PUSH

    mock_entry.options = {CONF_ENABLE_PUSH: False}
    mock_entry.data = {
        "push_subscribe_code": "sub_code_123",
        "alarm_subscribe_code": "alarm_code_123",
    }

    coordinator = MagicMock()
    coordinator.client = MagicMock()

    with patch(
        "custom_components.hyxi_cloud.__init__.async_cancel_and_unregister_subscription",
        new=AsyncMock(side_effect=RuntimeError("temporary network error")),
    ):
        await _async_setup_push_subscription(mock_hass, mock_entry, coordinator)
        await _async_setup_alarm_subscription(mock_hass, mock_entry, coordinator)

        # A failed cancel must never clear the persisted codes.
        mock_hass.config_entries.async_update_entry.assert_not_called()

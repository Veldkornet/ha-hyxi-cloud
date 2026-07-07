"""Tests for the initial setup of the HYXI Cloud integration."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

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
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"

    request = MagicMock()
    request.headers = {"accessKey": "malicious_ñ_key"}

    res = await _async_handle_webhook(hass, "webhook_id", request, coordinator)
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
                "HYXI Cloud not ready: %s",
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
async def test_async_setup_battery_protection_options(mock_hass, mock_entry):
    """Test that battery protection is set up only when option is enabled."""
    from custom_components.hyxi_cloud.__init__ import _async_setup_battery_protection

    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "INVERTER_SN": {
            "device_name": "Hybrid Inverter",
            "deviceType": "HYBRID_INVERTER",
            "model": "H5K-HT",
            "metrics": {},
        }
    }
    mock_coordinator.protection_controllers = {}

    # Case 1: Option is False (default)
    mock_entry.options = {"enable_battery_control": False}
    mock_coordinator.entry = mock_entry

    await _async_setup_battery_protection(mock_hass, mock_coordinator)
    assert len(mock_coordinator.protection_controllers) == 0

    # Case 2: Option is True
    mock_entry.options = {"enable_battery_control": True}
    with patch(
        "custom_components.hyxi_cloud.__init__.HyxiBatteryProtectionController"
    ) as mock_controller_class:
        mock_controller = mock_controller_class.return_value
        mock_controller.async_start = AsyncMock()

        await _async_setup_battery_protection(mock_hass, mock_coordinator)

        assert "INVERTER_SN" in mock_coordinator.protection_controllers
        mock_controller.async_start.assert_called_once()


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
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"

    request = MagicMock()
    request.headers = {"accessKey": "wrong_ak"}

    res = await _async_handle_webhook(hass, "webhook_id", request, coordinator)
    assert res.status == 401


@pytest.mark.asyncio
async def test_webhook_handle_invalid_json():
    """Verify webhook handles invalid JSON payloads gracefully."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"

    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.json = AsyncMock(side_effect=ValueError("Invalid JSON"))

    res = await _async_handle_webhook(hass, "webhook_id", request, coordinator)
    assert res.status == 400


@pytest.mark.asyncio
async def test_webhook_handle_process_exceptions():
    """Verify webhook handles process payload exceptions gracefully."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"
    coordinator.data = {}

    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.json = AsyncMock(return_value={"data": "raw"})
    coordinator.client.process_push_data = MagicMock(side_effect=Exception("sdk_error"))

    res = await _async_handle_webhook(hass, "webhook_id", request, coordinator)
    assert res.status == 500


@pytest.mark.asyncio
async def test_webhook_handle_untracked_device():
    """Verify webhook handles push data for untracked devices."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.client.access_key = "correct_ak"
    coordinator.data = {"SN123": {}}

    request = MagicMock()
    request.headers = {"accessKey": "correct_ak"}
    request.json = AsyncMock(return_value={})

    # process_push_data returns updates for untracked device SN999
    coordinator.client.process_push_data = MagicMock(
        return_value={"SN999": {"metrics": {"batSoc": 80}}}
    )

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.debug") as mock_debug:
        res = await _async_handle_webhook(hass, "webhook_id", request, coordinator)
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
        hass, "alarm_webhook_id", request, coordinator
    )
    assert res_auth.status == 401

    # 6. Alarm Webhook: invalid JSON
    request.headers = {"accessKey": "correct_ak"}
    request.json = AsyncMock(side_effect=ValueError("Invalid JSON"))
    res_json = await _async_handle_alarm_webhook(
        hass, "alarm_webhook_id", request, coordinator
    )
    assert res_json.status == 400

    # 7. Alarm Webhook: process raises exception
    request.json = AsyncMock(return_value={})
    coordinator.client.process_alarm_push_data = MagicMock(
        side_effect=Exception("sdk_err")
    )
    res_err = await _async_handle_alarm_webhook(
        hass, "alarm_webhook_id", request, coordinator
    )
    assert res_err.status == 500

    # 8. Alarm Webhook: untracked device SN
    coordinator.client.process_alarm_push_data = MagicMock(
        return_value={"SN999": [{"alarmCode": "100"}]}
    )
    coordinator.data = {"SN123": {}}
    with patch("custom_components.hyxi_cloud.__init__._LOGGER.warning") as mock_warn:
        res_ok = await _async_handle_alarm_webhook(
            hass, "alarm_webhook_id", request, coordinator
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

    cloud.CloudNotAvailable = CustomCloudNotAvailable

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
    request.json = AsyncMock(return_value={})
    coordinator.client.process_push_data = MagicMock(return_value={})
    res = await _async_handle_webhook(mock_hass, "web_id", request, coordinator)
    assert res.status == 200

    # 6. Push data webhook with coordinator.data is None (line 554)
    coordinator.data = None
    coordinator.client.process_push_data = MagicMock(
        return_value={"SN123": {"metrics": {"batSoc": 85}}}
    )
    from custom_components.hyxi_cloud.const import mask_sn

    with patch("custom_components.hyxi_cloud.__init__._LOGGER.debug") as mock_debug:
        res = await _async_handle_webhook(mock_hass, "web_id", request, coordinator)
        assert res.status == 200
        assert coordinator.data == {}
        # SN123 is untracked now
        mock_debug.assert_any_call(
            "Received push data for untracked device SN: %s", mask_sn("SN123")
        )

    # 7. Push data webhook updates successfully (line 577-580)
    coordinator.data = {"SN123": {"metrics": {}}}
    coordinator.async_update_listeners = MagicMock()
    res = await _async_handle_webhook(mock_hass, "web_id", request, coordinator)
    assert res.status == 200
    assert coordinator.data["SN123"]["metrics"] == {"batSoc": 85}
    coordinator.async_update_listeners.assert_called_once()

    # 8. Alarm push webhook empty results (line 755)
    coordinator.client.process_alarm_push_data = MagicMock(return_value={})
    res = await _async_handle_alarm_webhook(
        mock_hass, "alarm_web_id", request, coordinator
    )
    assert res.status == 200

    # 9. Alarm push webhook with coordinator.data is None (line 758)
    coordinator.data = None
    coordinator.client.process_alarm_push_data = MagicMock(
        return_value={"SN123": [{"alarmCode": "99"}]}
    )
    with patch("custom_components.hyxi_cloud.__init__._LOGGER.warning") as mock_warn:
        res = await _async_handle_alarm_webhook(
            mock_hass, "alarm_web_id", request, coordinator
        )
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
    res = await _async_handle_alarm_webhook(
        mock_hass, "alarm_web_id", request, coordinator
    )
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
    coordinator.async_config_entry_first_refresh = AsyncMock()

    # Mock engine instance
    mock_engine = MagicMock()
    mock_engine.async_start = AsyncMock()
    mock_engine.async_stop = AsyncMock()

    # Mock protection controller
    mock_controller = MagicMock()
    mock_controller.async_start = AsyncMock()
    mock_controller.async_stop = AsyncMock()

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
                                    mock_engine.async_start.assert_called_once()
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
                                    mock_engine.async_stop.assert_called_once()
                                    mock_controller.async_stop.assert_called_once()


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
            mock_entry, data={**mock_entry.data, "push_subscribe_code": None}
        )
        mock_hass.config_entries.async_update_entry.assert_any_call(
            mock_entry, data={**mock_entry.data, "alarm_subscribe_code": None}
        )

"""Tests for HYXI Cloud custom services."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.hyxi_cloud import (
    DOMAIN,
    _energy_manager_manages,
    _resolve_battery_mode_targets,
    async_get_subscription_codes,
    async_register_subscription_code,
    async_unload_entry,
    async_unregister_subscription_code,
    setup_services,
)


@pytest.fixture
def mock_entry():
    entry = MagicMock()
    entry.entry_id = "entry_123"
    entry.data = {}
    entry.options = {}
    return entry


@pytest.fixture
def mock_coordinator(mock_entry):
    coordinator = MagicMock()
    coordinator.entry = mock_entry
    coordinator.client = MagicMock()
    coordinator.client.cancel_subscription = AsyncMock(return_value={"success": True})
    coordinator.protection_controllers = {}
    coordinator.engine = None
    return coordinator


@pytest.mark.asyncio
async def test_service_registration_and_unload(hass, mock_entry, mock_coordinator):
    """Both custom services register on setup and are removed when the last entry unloads."""
    hass.data[DOMAIN] = {mock_entry.entry_id: mock_coordinator}

    # Verify service registration
    setup_services(hass)
    assert hass.services.has_service(DOMAIN, "cancel_subscription")
    assert hass.services.has_service(DOMAIN, "set_battery_mode")

    # Re-registering doesn't crash or duplicate
    setup_services(hass)
    assert hass.services.has_service(DOMAIN, "cancel_subscription")

    # Unload entry
    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "custom_components.hyxi_cloud._async_teardown_push_subscription",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.hyxi_cloud._async_teardown_alarm_subscription",
            new_callable=AsyncMock,
        ),
    ):
        await async_unload_entry(hass, mock_entry)

    # Verify both services were removed because no config entries remain
    assert not hass.services.has_service(DOMAIN, "cancel_subscription")
    assert not hass.services.has_service(DOMAIN, "set_battery_mode")


@pytest.mark.asyncio
async def test_service_call_success(hass, mock_coordinator):
    """Test that the cancel_subscription service calls SDK method successfully."""
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    setup_services(hass)

    await hass.services.async_call(
        DOMAIN,
        "cancel_subscription",
        {"subscribe_code": " test-code-abc "},
        blocking=True,
    )

    mock_coordinator.client.cancel_subscription.assert_awaited_once_with(
        "test-code-abc"
    )


@pytest.mark.asyncio
async def test_service_call_empty_code(hass, mock_coordinator):
    """Test service raises error if subscription code is empty."""
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    setup_services(hass)

    with pytest.raises(HomeAssistantError, match="Subscription code cannot be empty"):
        await hass.services.async_call(
            DOMAIN,
            "cancel_subscription",
            {"subscribe_code": "   "},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_service_call_no_coordinators(hass):
    """Test service raises error if no integration coordinators are loaded."""
    if DOMAIN in hass.data:
        del hass.data[DOMAIN]

    # Ensure service is registered
    setup_services(hass)

    with pytest.raises(
        HomeAssistantError, match="No active HYXI Cloud integration entries found"
    ):
        await hass.services.async_call(
            DOMAIN,
            "cancel_subscription",
            {"subscribe_code": "some-code"},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_service_call_api_failure(hass, mock_coordinator):
    """Test service raises error if client cancel API returns success=False."""
    mock_coordinator.client.cancel_subscription.return_value = {
        "success": False,
        "msg": "Invalid subscribe code",
    }
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    setup_services(hass)

    with pytest.raises(
        HomeAssistantError,
        match="Failed to cancel subscription: Invalid subscribe code",
    ):
        await hass.services.async_call(
            DOMAIN,
            "cancel_subscription",
            {"subscribe_code": "bad-code"},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_service_call_api_exception(hass, mock_coordinator):
    """Test service raises error if client cancel API raises exception."""
    mock_coordinator.client.cancel_subscription.side_effect = RuntimeError(
        "network timeout"
    )
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    setup_services(hass)

    with pytest.raises(HomeAssistantError, match="API error: network timeout"):
        await hass.services.async_call(
            DOMAIN,
            "cancel_subscription",
            {"subscribe_code": "bad-code"},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_service_call_api_exception_with_parenthesized_code(
    hass, mock_coordinator
):
    """Test the parenthesized-code branch of the 'subscription request
    failed' error path is exercised (real SDK errors sometimes prefix the
    message with an API error code in parentheses)."""
    mock_coordinator.client.cancel_subscription.side_effect = RuntimeError(
        "subscription request failed: (C000001) Invalid subscribe code"
    )
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    setup_services(hass)

    with pytest.raises(
        HomeAssistantError,
        match=r"Failed to cancel subscription: \(C000001\) Invalid subscribe code",
    ):
        await hass.services.async_call(
            DOMAIN,
            "cancel_subscription",
            {"subscribe_code": "bad-code"},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_subscription_code_persistence(hass, mock_coordinator):
    """Test that subscription codes are successfully written to, loaded from, and removed from the Store."""
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    mock_coordinator.known_subscription_codes = []
    mock_coordinator.async_update_listeners = MagicMock()

    # Verify initially empty
    codes = await async_get_subscription_codes(hass)
    assert codes == []

    # Register subscription code
    await async_register_subscription_code(hass, "test-sub-code-123")

    # Verify stored in Store and set on coordinator
    codes = await async_get_subscription_codes(hass)
    assert codes == ["test-sub-code-123"]
    assert mock_coordinator.known_subscription_codes == ["test-sub-code-123"]
    mock_coordinator.async_update_listeners.assert_called_once()

    # Unregister code
    mock_coordinator.async_update_listeners.reset_mock()
    await async_unregister_subscription_code(hass, "test-sub-code-123")

    # Verify removed
    codes = await async_get_subscription_codes(hass)
    assert codes == []
    assert mock_coordinator.known_subscription_codes == []
    mock_coordinator.async_update_listeners.assert_called_once()


# ── set_battery_mode ───────────────────────────────────────────────────


def _fake_device(serial="SN1", entry_id="entry_123"):
    device = MagicMock()
    device.identifiers = {(DOMAIN, serial)}
    device.config_entries = [entry_id]
    return device


@pytest.fixture
def battery_mode_env(hass, mock_coordinator, mock_entry):
    """A registered set_battery_mode service, a control-capable coordinator
    holding one serial, and patched device/entity registries."""
    mock_entry.title = "HYXI"
    mock_entry.options = {"enable_battery_control": True}
    mock_coordinator.data = {"SN1": {"device_name": "Inverter"}}
    mock_coordinator.engine = None
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    setup_services(hass)

    dev_reg = MagicMock()
    dev_reg.async_get.return_value = _fake_device()
    ent_reg = MagicMock()
    with (
        patch("custom_components.hyxi_cloud.dr.async_get", return_value=dev_reg),
        patch("custom_components.hyxi_cloud.er.async_get", return_value=ent_reg),
        patch(
            "custom_components.hyxi_cloud.control.async_send_battery_mode",
            new_callable=AsyncMock,
        ) as send,
    ):
        yield hass, mock_coordinator, mock_entry, send


@pytest.mark.asyncio
async def test_set_battery_mode_registered(hass, mock_coordinator):
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    setup_services(hass)
    assert hass.services.has_service(DOMAIN, "set_battery_mode")


@pytest.mark.asyncio
async def test_set_battery_mode_success_forwards_to_the_helper(battery_mode_env):
    hass, coordinator, _entry, send = battery_mode_env

    await hass.services.async_call(
        DOMAIN,
        "set_battery_mode",
        {"device_id": "dev1", "mode": "charge", "power": 2500},
        blocking=True,
    )

    send.assert_awaited_once_with(hass, coordinator, "SN1", "charge", power=2500)


@pytest.mark.asyncio
async def test_set_battery_mode_no_target_raises(battery_mode_env):
    hass, _coordinator, _entry, send = battery_mode_env

    with pytest.raises(ServiceValidationError, match="No HYXI inverter matched"):
        await hass.services.async_call(
            DOMAIN, "set_battery_mode", {"mode": "idle"}, blocking=True
        )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_battery_mode_requires_device_control(battery_mode_env):
    hass, _coordinator, entry, send = battery_mode_env
    entry.options = {"enable_battery_control": False}

    with pytest.raises(ServiceValidationError, match="Device Control is not enabled"):
        await hass.services.async_call(
            DOMAIN,
            "set_battery_mode",
            {"device_id": "dev1", "mode": "idle"},
            blocking=True,
        )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_battery_mode_blocked_while_energy_manager_runs(battery_mode_env):
    hass, coordinator, _entry, send = battery_mode_env
    coordinator.engine = MagicMock(enabled=True, sn="SN1", status="running")

    with pytest.raises(ServiceValidationError, match="Energy Manager is managing"):
        await hass.services.async_call(
            DOMAIN,
            "set_battery_mode",
            {"device_id": "dev1", "mode": "discharge"},
            blocking=True,
        )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_battery_mode_preflights_soc_protection(battery_mode_env):
    """A charge command to a battery at SOC-max is rejected before any
    command goes out -- the same guard the mode buttons apply."""
    hass, coordinator, _entry, send = battery_mode_env
    controller = MagicMock()
    controller.should_block_manual_charge.return_value = True
    coordinator.protection_controllers = {"SN1": controller}

    with pytest.raises(HomeAssistantError, match="SOC Maximum"):
        await hass.services.async_call(
            DOMAIN,
            "set_battery_mode",
            {"device_id": "dev1", "mode": "charge"},
            blocking=True,
        )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_battery_mode_force_overrides_the_energy_manager(battery_mode_env):
    hass, coordinator, _entry, send = battery_mode_env
    coordinator.engine = MagicMock(enabled=True, sn="SN1", status="running")

    await hass.services.async_call(
        DOMAIN,
        "set_battery_mode",
        {"device_id": "dev1", "mode": "discharge", "force": True},
        blocking=True,
    )
    send.assert_awaited_once_with(hass, coordinator, "SN1", "discharge", power=None)


@pytest.mark.asyncio
async def test_set_battery_mode_resolves_an_entity_target(battery_mode_env):
    hass, coordinator, _entry, send = battery_mode_env
    with patch("custom_components.hyxi_cloud.er.async_get") as ent_reg_get:
        ent_reg_get.return_value.async_get.return_value = MagicMock(device_id="dev1")
        await hass.services.async_call(
            DOMAIN,
            "set_battery_mode",
            {"entity_id": "sensor.hyxi_sn1_batsoc", "mode": "idle"},
            blocking=True,
        )
    send.assert_awaited_once_with(hass, coordinator, "SN1", "idle", power=None)


def test_resolve_battery_mode_targets_skips_unknown_and_foreign_devices(
    hass, mock_coordinator
):
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    mock_coordinator.data = {"SN1": {}}

    unknown = MagicMock()
    unknown.identifiers = {("other_domain", "x")}
    unknown.config_entries = ["entry_123"]
    not_polled = MagicMock()
    not_polled.identifiers = {(DOMAIN, "SN_GONE")}
    not_polled.config_entries = ["entry_123"]

    dev_reg = MagicMock()
    dev_reg.async_get.side_effect = [None, unknown, not_polled]
    with (
        patch("custom_components.hyxi_cloud.dr.async_get", return_value=dev_reg),
        patch("custom_components.hyxi_cloud.er.async_get", return_value=MagicMock()),
    ):
        rows = _resolve_battery_mode_targets(hass, {"a", "b", "c"}, set())
    assert rows == []


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        (None, False),
        (MagicMock(enabled=False, sn="SN1", status="running"), False),
        (MagicMock(enabled=True, sn="OTHER", status="running"), False),
        (MagicMock(enabled=True, sn="SN1", status="disabled"), False),
        (MagicMock(enabled=True, sn="SN1", status="running"), True),
    ],
)
def test_energy_manager_manages(engine, expected):
    coordinator = MagicMock()
    coordinator.engine = engine
    assert _energy_manager_manages(coordinator, "SN1") is expected

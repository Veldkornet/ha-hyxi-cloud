"""Tests for HYXI Cloud custom services."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.hyxi_cloud import (
    DOMAIN,
    async_get_subscription_codes,
    async_register_subscription_code,
    async_setup_services,
    async_unload_entry,
    async_unregister_subscription_code,
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
    """Test that the cancel_subscription service is registered on setup and removed on unload."""
    hass.data[DOMAIN] = {mock_entry.entry_id: mock_coordinator}

    # Verify service registration
    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, "cancel_subscription")

    # Re-registering doesn't crash or duplicate
    await async_setup_services(hass)
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

    # Verify service was removed because no config entries remain
    assert not hass.services.has_service(DOMAIN, "cancel_subscription")


@pytest.mark.asyncio
async def test_service_call_success(hass, mock_coordinator):
    """Test that the cancel_subscription service calls SDK method successfully."""
    hass.data[DOMAIN] = {"entry_123": mock_coordinator}
    await async_setup_services(hass)

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
    await async_setup_services(hass)

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
    await async_setup_services(hass)

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
    await async_setup_services(hass)

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
    await async_setup_services(hass)

    with pytest.raises(HomeAssistantError, match="API error: network timeout"):
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

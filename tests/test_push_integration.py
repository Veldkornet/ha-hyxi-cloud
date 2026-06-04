"""Integration tests for HYXI webhook push subscription and callback processing."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "homeassistant.components.webhook" not in sys.modules:
    sys.modules["homeassistant.components.webhook"] = MagicMock()
if "homeassistant.components.cloud" not in sys.modules:
    sys.modules["homeassistant.components.cloud"] = MagicMock()

# These imports must follow sys.modules patching above — pylint: disable=wrong-import-position

from custom_components.hyxi_cloud.__init__ import (
    _async_handle_webhook,
    _async_setup_push_subscription,
    _async_teardown_push_subscription,
)
from custom_components.hyxi_cloud.button import HyxiRenewSubscriptionButton
from custom_components.hyxi_cloud.const import CONF_ENABLE_PUSH, CONF_PUSH_RATE, DOMAIN
from custom_components.hyxi_cloud.sensor import HyxiSubscriptionStatusSensor

# pylint: enable=wrong-import-position


@pytest.fixture
def mock_entry():
    entry = MagicMock()
    entry.entry_id = "entry_123"
    entry.options = {
        CONF_ENABLE_PUSH: True,
        CONF_PUSH_RATE: 10,  # stored in seconds; SDK receives *1000 ms
    }
    return entry


@pytest.fixture
def mock_coordinator(mock_entry):
    coordinator = MagicMock()
    coordinator.data = {
        "INV123": {
            "device_name": "Test Inverter",
            "model": "H5K-HT",
            "device_type_code": "HYBRID_INVERTER",
            "metrics": {},
        }
    }
    coordinator.entry = mock_entry
    coordinator.client = MagicMock()
    coordinator.client.access_key = "test_ak"
    coordinator.client.subscribe_real_time_data = AsyncMock(
        return_value={"success": True, "data": {"subscribeCode": "test-sub-code"}}
    )
    coordinator.client.cancel_subscription = AsyncMock()

    # Initialize push status fields as in coordinator.py
    coordinator.push_enabled = False
    coordinator.subscribe_code = None
    coordinator.webhook_id = None
    coordinator.push_url = None
    coordinator.last_push_received = None
    coordinator.push_status = "inactive"
    coordinator.push_error = None

    return coordinator


@pytest.mark.asyncio
async def test_setup_push_subscription_disabled(mock_coordinator, mock_entry):
    """Test setup is skipped when push is disabled."""
    mock_entry.options[CONF_ENABLE_PUSH] = False

    hass = MagicMock()
    with patch("custom_components.hyxi_cloud.__init__.webhook") as mock_webhook:
        await _async_setup_push_subscription(hass, mock_entry, mock_coordinator)

        assert mock_coordinator.push_status == "inactive"
        mock_webhook.async_register.assert_not_called()


@pytest.mark.asyncio
async def test_setup_push_subscription_success(mock_coordinator, mock_entry):
    """Test setup registers webhook and calls SDK subscribe method successfully."""
    import sys

    import homeassistant.components.cloud as cloud

    print(
        "DEBUG: sys.modules['homeassistant']",
        sys.modules.get("homeassistant"),
        "ID:",
        id(sys.modules.get("homeassistant")),
    )
    print(
        "DEBUG: sys.modules['homeassistant'].components",
        getattr(sys.modules.get("homeassistant"), "components", None),
        "ID:",
        id(getattr(sys.modules.get("homeassistant"), "components", None))
        if getattr(sys.modules.get("homeassistant"), "components", None)
        else None,
    )
    print(
        "DEBUG: sys.modules['homeassistant.components']",
        sys.modules.get("homeassistant.components"),
        "ID:",
        id(sys.modules.get("homeassistant.components")),
    )
    print(
        "DEBUG: sys.modules['homeassistant.components.cloud']",
        sys.modules.get("homeassistant.components.cloud"),
        "ID:",
        id(sys.modules.get("homeassistant.components.cloud")),
    )
    print(
        "DEBUG: sys.modules['homeassistant.components'].cloud",
        getattr(sys.modules.get("homeassistant.components"), "cloud", None),
        "ID:",
        id(getattr(sys.modules.get("homeassistant.components"), "cloud", None))
        if getattr(sys.modules.get("homeassistant.components"), "cloud", None)
        else None,
    )
    print(
        "DEBUG: cloud is sys.modules['homeassistant.components.cloud']",
        cloud is sys.modules.get("homeassistant.components.cloud"),
    )
    hass = MagicMock()

    # Bypass the mock components trap
    if "homeassistant.components" in sys.modules:
        mock_comp = sys.modules["homeassistant.components"]
        if isinstance(mock_comp, MagicMock):
            mock_comp.cloud.async_active_subscription.return_value = False

    with (
        patch("custom_components.hyxi_cloud.__init__.webhook") as mock_webhook,
        patch(
            "custom_components.hyxi_cloud.__init__.network.get_url",
            return_value="https://my-ha.local",
        ),
        patch(
            "homeassistant.components.cloud.async_active_subscription",
            return_value=False,
        ),
    ):
        mock_webhook.async_generate_path.return_value = (
            "/api/webhook/hyxi_cloud_entry_123"
        )

        await _async_setup_push_subscription(hass, mock_entry, mock_coordinator)

        assert mock_coordinator.push_enabled is True
        assert mock_coordinator.webhook_id == "hyxi_cloud_entry_123"
        assert mock_coordinator.push_status == "active"
        assert mock_coordinator.subscribe_code == "test-sub-code"
        assert (
            mock_coordinator.push_url
            == "https://my-ha.local/api/webhook/hyxi_cloud_entry_123"
        )

        mock_webhook.async_register.assert_called_once()
        mock_coordinator.client.subscribe_real_time_data.assert_called_once_with(
            "https://my-ha.local/api/webhook/hyxi_cloud_entry_123",
            ["INV123"],
            10000,
        )


@pytest.mark.asyncio
async def test_teardown_push_subscription(mock_coordinator):
    """Test teardown unregisters webhook and cancels subscription."""
    hass = MagicMock()
    mock_coordinator.webhook_id = "hyxi_cloud_entry_123"
    mock_coordinator.subscribe_code = "test-sub-code"

    with patch("custom_components.hyxi_cloud.__init__.webhook") as mock_webhook:
        await _async_teardown_push_subscription(hass, mock_coordinator)

        assert mock_coordinator.push_enabled is False
        assert mock_coordinator.push_status == "inactive"
        assert mock_coordinator.subscribe_code is None
        assert mock_coordinator.webhook_id is None

        mock_webhook.async_unregister.assert_called_once_with(
            hass, "hyxi_cloud_entry_123"
        )
        mock_coordinator.client.cancel_subscription.assert_called_once_with(
            "test-sub-code"
        )


@pytest.mark.asyncio
async def test_webhook_handler_unauthorized(mock_coordinator):
    """Test webhook handler rejects unauthorized calls."""
    hass = MagicMock()
    request = MagicMock()
    request.headers = {"accessKey": "wrong_ak"}

    with patch(
        "custom_components.hyxi_cloud.__init__.web.Response"
    ) as mock_response_class:
        await _async_handle_webhook(hass, "webhook_123", request, mock_coordinator)
        mock_response_class.assert_called_once_with(status=401, text="Unauthorized")


@pytest.mark.asyncio
async def test_webhook_handler_success(mock_coordinator):
    """Test webhook handler successfully processes valid request and updates coordinator."""
    hass = MagicMock()
    request = MagicMock()
    request.headers = {"accessKey": "test_ak"}
    request.json = AsyncMock(
        return_value={"dataList": [{"deviceSn": "INV123", "batSoc": 85}]}
    )

    mock_coordinator.client.process_push_data.return_value = {
        "INV123": {
            "sn": "INV123",
            "metrics": {"batSoc": 85},
        }
    }

    with patch(
        "custom_components.hyxi_cloud.__init__.web.json_response"
    ) as mock_json_res:
        await _async_handle_webhook(hass, "webhook_123", request, mock_coordinator)

        # Verify SDK process method was called
        mock_coordinator.client.process_push_data.assert_called_once_with(
            {"dataList": [{"deviceSn": "INV123", "batSoc": 85}]},
            existing_metrics={"INV123": {}},
        )

        # Verify coordinator data updated and update_listeners called
        assert mock_coordinator.data["INV123"]["metrics"]["batSoc"] == 85
        assert mock_coordinator.last_push_received is not None
        mock_coordinator.async_update_listeners.assert_called_once()
        mock_json_res.assert_called_once_with(
            {"code": "0", "msg": "Success", "success": True}
        )


def test_sensor_state_and_attributes(mock_coordinator, mock_entry):
    """Test push status sensor reflects combined push state correctly."""
    # ---- Only data push active → partial ----
    mock_coordinator.push_status = "active"
    mock_coordinator.subscribe_code = "sub-123"
    mock_coordinator.push_url = "https://example.com/webhook"
    mock_coordinator.push_error = None
    mock_coordinator.last_push_received = None
    mock_coordinator.alarm_push_status = "inactive"
    mock_coordinator.alarm_subscribe_code = None
    mock_coordinator.alarm_push_url = None
    mock_coordinator.alarm_last_push_received = None

    with patch("custom_components.hyxi_cloud.sensor.DOMAIN", DOMAIN):
        sensor = HyxiSubscriptionStatusSensor(mock_coordinator, mock_entry)

        assert sensor.native_value == "partial"
        attrs = sensor.extra_state_attributes
        assert "data_push" in attrs
        assert "alarm_push" in attrs
        assert attrs["data_push"]["status"] == "active"
        assert attrs["data_push"]["subscribe_code"] == "sub-123"
        assert attrs["data_push"]["callback_url"] == "https://example.com/webhook"
        assert attrs["data_push"]["post_rate"] == 10  # stored in seconds
        assert attrs["alarm_push"]["status"] == "inactive"

    # ---- Both active → active ----
    mock_coordinator.alarm_push_status = "active"
    mock_coordinator.alarm_subscribe_code = "alarm-456"
    mock_coordinator.alarm_push_url = "https://example.com/webhook_alarm"

    with patch("custom_components.hyxi_cloud.sensor.DOMAIN", DOMAIN):
        sensor2 = HyxiSubscriptionStatusSensor(mock_coordinator, mock_entry)

        assert sensor2.native_value == "active"
        attrs2 = sensor2.extra_state_attributes
        assert attrs2["alarm_push"]["status"] == "active"
        assert attrs2["alarm_push"]["subscribe_code"] == "alarm-456"


@pytest.mark.asyncio
async def test_button_press_renew(mock_coordinator, mock_entry):
    """Test renew button tears down and sets up subscription again."""
    hass = MagicMock()
    with patch("custom_components.hyxi_cloud.button.DOMAIN", DOMAIN):
        button = HyxiRenewSubscriptionButton(mock_coordinator, mock_entry)
        button.hass = hass

        with (
            patch(
                "custom_components.hyxi_cloud._async_teardown_push_subscription"
            ) as mock_teardown,
            patch(
                "custom_components.hyxi_cloud._async_setup_push_subscription"
            ) as mock_setup,
        ):
            await button.async_press()

            mock_teardown.assert_called_once_with(hass, mock_coordinator, mock_entry)
            mock_setup.assert_called_once_with(hass, mock_entry, mock_coordinator)
            mock_coordinator.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_setup_push_subscription_via_nabu_casa(mock_coordinator, mock_entry):
    """Test push subscription setup using Nabu Casa cloudhook URL resolution."""
    hass = MagicMock()

    import homeassistant.components.cloud as cloud_mock

    old_active = cloud_mock.async_active_subscription
    old_hook = getattr(cloud_mock, "async_get_or_create_cloudhook", None)

    cloud_mock.async_active_subscription = MagicMock(return_value=True)
    cloud_mock.async_get_or_create_cloudhook = AsyncMock(
        return_value="https://hooks.nabucasa.com/12345"
    )

    try:
        with patch("custom_components.hyxi_cloud.__init__.webhook") as mock_webhook:
            mock_webhook.async_generate_path.return_value = (
                "/api/webhook/hyxi_cloud_entry_123"
            )

            await _async_setup_push_subscription(hass, mock_entry, mock_coordinator)

            assert mock_coordinator.push_enabled is True
            assert mock_coordinator.webhook_id == "hyxi_cloud_entry_123"
            assert mock_coordinator.push_status == "active"
            assert mock_coordinator.push_url == "https://hooks.nabucasa.com/12345"

            mock_webhook.async_register.assert_called_once()
            mock_coordinator.client.subscribe_real_time_data.assert_called_once_with(
                "https://hooks.nabucasa.com/12345",
                ["INV123"],
                10000,
            )
    finally:
        cloud_mock.async_active_subscription = old_active
        if old_hook is not None:
            cloud_mock.async_get_or_create_cloudhook = old_hook
        else:
            delattr(cloud_mock, "async_get_or_create_cloudhook")


@pytest.mark.asyncio
async def test_webhook_handler_logging_details(mock_coordinator, caplog):
    """Test that the webhook handler logs simplified request details."""
    import logging

    hass = MagicMock()
    request = MagicMock()
    request.headers = {"accessKey": "test_ak"}
    request.json = AsyncMock(
        return_value={"dataList": [{"deviceSn": "INV123", "batSoc": 85}]}
    )

    mock_coordinator.subscribe_code = "coord-sub-code"
    mock_coordinator.client.process_push_data.return_value = {
        "INV123": {
            "sn": "INV123",
            "metrics": {"batSoc": 85},
        }
    }

    caplog.set_level(logging.DEBUG)

    with patch("custom_components.hyxi_cloud.__init__.web.json_response"):
        await _async_handle_webhook(hass, "webhook_123", request, mock_coordinator)

        # Check logs for expected text
        log_records = [rec.message for rec in caplog.records]
        debug_log = [msg for msg in log_records if "webhook callback received" in msg]
        assert len(debug_log) == 1
        log_msg = debug_log[0]

        # Verify webhook ID and active subscribe code are logged correctly
        assert "Webhook ID: webhook_123" in log_msg
        assert "Active Subscribe Code: coord-sub-code" in log_msg

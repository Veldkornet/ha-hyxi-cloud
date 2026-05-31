"""Tests for HYXI alarm push subscription lifecycle and webhook handler."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "homeassistant.components.webhook" not in sys.modules:
    sys.modules["homeassistant.components.webhook"] = MagicMock()
if "homeassistant.components.cloud" not in sys.modules:
    sys.modules["homeassistant.components.cloud"] = MagicMock()

# These imports must follow sys.modules patching — pylint: disable=wrong-import-position
import homeassistant.components.cloud as mock_cloud

mock_cloud.async_active_subscription = MagicMock(return_value=False)

from custom_components.hyxi_cloud.__init__ import (  # pylint: disable=wrong-import-position
    _async_handle_alarm_webhook,
    _async_setup_alarm_subscription,
    _async_teardown_alarm_subscription,
)
from custom_components.hyxi_cloud.const import (  # pylint: disable=wrong-import-position
    CONF_ENABLE_PUSH,
    CONF_PUSH_RATE,
)

# pylint: enable=wrong-import-position


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    return hass


@pytest.fixture
def mock_entry():
    entry = MagicMock()
    entry.entry_id = "entry_test"
    entry.options = {CONF_ENABLE_PUSH: True, CONF_PUSH_RATE: 10}
    entry.data = {}
    return entry


@pytest.fixture
def mock_coordinator():
    coord = MagicMock()
    coord.data = {"SN001": {"metrics": {}, "alarms": []}}
    coord.alarm_subscribe_code = None
    coord.alarm_webhook_id = None
    coord.alarm_push_status = "inactive"
    coord.alarm_last_push_received = None
    coord.client = MagicMock()
    coord.client.access_key = "test_ak"
    coord.client.cancel_subscription = AsyncMock()
    coord.client.subscribe_alarm = AsyncMock(
        return_value={"success": True, "data": {"subscribeCode": "alarm_code_abc"}}
    )
    coord.async_set_updated_data = MagicMock()
    return coord


# ---------------------------------------------------------------------------
# _async_setup_alarm_subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_alarm_subscription_success(
    mock_hass, mock_entry, mock_coordinator
):
    """Happy path: subscription registered and code persisted."""
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        new=AsyncMock(
            return_value="https://example.ngrok.app/api/webhook/hyxi_cloud_entry_test_alarm"
        ),
    ):
        await _async_setup_alarm_subscription(mock_hass, mock_entry, mock_coordinator)

    assert mock_coordinator.alarm_subscribe_code == "alarm_code_abc"
    assert mock_coordinator.alarm_push_status == "active"
    mock_hass.config_entries.async_update_entry.assert_called()


@pytest.mark.asyncio
async def test_setup_alarm_subscription_no_url(mock_hass, mock_entry, mock_coordinator):
    """When URL resolution fails, status is set to error and subscribe is not called."""
    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        new=AsyncMock(return_value=None),
    ):
        await _async_setup_alarm_subscription(mock_hass, mock_entry, mock_coordinator)

    assert mock_coordinator.alarm_push_status == "error"
    mock_coordinator.client.subscribe_alarm.assert_not_called()


@pytest.mark.asyncio
async def test_setup_alarm_cancels_prior_orphan(
    mock_hass, mock_entry, mock_coordinator
):
    """Prior orphaned subscribe_code is cancelled before new subscription."""
    mock_entry.data = {"alarm_subscribe_code": "old_code_xyz"}

    with patch(
        "custom_components.hyxi_cloud.__init__._async_resolve_webhook_url",
        new=AsyncMock(
            return_value="https://example.ngrok.app/api/webhook/hyxi_cloud_entry_test_alarm"
        ),
    ):
        await _async_setup_alarm_subscription(mock_hass, mock_entry, mock_coordinator)

    mock_coordinator.client.cancel_subscription.assert_awaited_with("old_code_xyz")


# ---------------------------------------------------------------------------
# _async_teardown_alarm_subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teardown_alarm_subscription_cancels_and_clears(
    mock_hass, mock_entry, mock_coordinator
):
    """Teardown cancels the subscription, unregisters webhook, clears coordinator state."""
    mock_coordinator.alarm_subscribe_code = "alarm_code_abc"
    mock_coordinator.alarm_webhook_id = "hyxi_cloud_entry_test_alarm"

    await _async_teardown_alarm_subscription(mock_hass, mock_coordinator, mock_entry)

    mock_coordinator.client.cancel_subscription.assert_awaited_once_with(
        "alarm_code_abc"
    )
    assert mock_coordinator.alarm_subscribe_code is None
    assert mock_coordinator.alarm_push_status == "inactive"
    mock_hass.config_entries.async_update_entry.assert_called()


@pytest.mark.asyncio
async def test_teardown_alarm_subscription_no_entry(mock_hass, mock_coordinator):
    """Teardown without entry argument skips entry.data update (safe for crash path)."""
    mock_coordinator.alarm_subscribe_code = "alarm_code_abc"

    await _async_teardown_alarm_subscription(mock_hass, mock_coordinator)

    mock_coordinator.client.cancel_subscription.assert_awaited_once_with(
        "alarm_code_abc"
    )
    assert mock_coordinator.alarm_subscribe_code is None


# ---------------------------------------------------------------------------
# _async_handle_alarm_webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_alarm_webhook_merges_records(mock_hass, mock_coordinator):
    """Alarm push updates existing alarm records and appends new ones by alarmCode."""
    mock_coordinator.data = {
        "SN001": {
            "alarms": [
                {"alarmCode": "768", "alarmState": "0", "alarmName": "Old Overvoltage"}
            ]
        }
    }

    mock_coordinator.client.process_alarm_push_data = MagicMock(
        return_value={
            "SN001": [
                {
                    "alarmCode": "768",
                    "alarmName": "Overvoltage alarm",
                    "alarmState": "1",
                    "alarmTime": None,
                    "endTime": None,
                },
                {
                    "alarmCode": "769",
                    "alarmName": "Over temperature alarm",
                    "alarmState": "2",
                    "alarmTime": None,
                    "endTime": None,
                },
            ]
        }
    )

    request = MagicMock()
    request.headers = {"accessKey": "test_ak"}
    request.json = AsyncMock(return_value={"dataList": []})

    response = await _async_handle_alarm_webhook(
        mock_hass, "hyxi_cloud_entry_test_alarm", request, mock_coordinator
    )

    assert response.status == 200
    alarms = mock_coordinator.data["SN001"]["alarms"]
    codes = {a["alarmCode"] for a in alarms}
    assert codes == {"768", "769"}

    # Verify 768 was updated from state "0" → "1"
    code_768 = next(a for a in alarms if a["alarmCode"] == "768")
    assert code_768["alarmState"] == "1"
    mock_coordinator.async_set_updated_data.assert_called_once()
    # Timestamp must be set on any valid delivery, including ones with alarm data
    assert mock_coordinator.alarm_last_push_received is not None


@pytest.mark.asyncio
async def test_handle_alarm_webhook_unauthorized(mock_hass, mock_coordinator):
    """Wrong accessKey header returns 401 without processing payload."""
    request = MagicMock()
    request.headers = {"accessKey": "wrong_key"}

    response = await _async_handle_alarm_webhook(
        mock_hass, "hyxi_cloud_entry_test_alarm", request, mock_coordinator
    )
    assert response.status == 401
    mock_coordinator.client.process_alarm_push_data.assert_not_called()
    # Timestamp must NOT be set — request was rejected before payload parsing
    assert mock_coordinator.alarm_last_push_received is None


@pytest.mark.asyncio
async def test_handle_alarm_webhook_invalid_json(mock_hass, mock_coordinator):
    """Malformed JSON returns 400."""
    request = MagicMock()
    request.headers = {"accessKey": "test_ak"}
    request.json = AsyncMock(side_effect=ValueError("bad json"))

    response = await _async_handle_alarm_webhook(
        mock_hass, "hyxi_cloud_entry_test_alarm", request, mock_coordinator
    )
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_alarm_webhook_untracked_device(mock_hass, mock_coordinator):
    """Alarm for unknown device SN is logged and skipped gracefully."""
    mock_coordinator.data = {"SN001": {"alarms": []}}
    mock_coordinator.client.process_alarm_push_data = MagicMock(
        return_value={
            "UNKNOWN_SN": [
                {
                    "alarmCode": "768",
                    "alarmState": "1",
                    "alarmName": "X",
                    "alarmTime": None,
                    "endTime": None,
                }
            ]
        }
    )

    request = MagicMock()
    request.headers = {"accessKey": "test_ak"}
    request.json = AsyncMock(return_value={"dataList": []})

    response = await _async_handle_alarm_webhook(
        mock_hass, "hyxi_cloud_entry_test_alarm", request, mock_coordinator
    )
    assert response.status == 200
    # coordinator data for SN001 should be untouched
    assert mock_coordinator.data["SN001"]["alarms"] == []
    mock_coordinator.async_set_updated_data.assert_not_called()
    # Timestamp IS set — the push was valid and parseable even though SN was unknown
    assert mock_coordinator.alarm_last_push_received is not None

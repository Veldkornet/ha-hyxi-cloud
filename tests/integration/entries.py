"""Config entries shared by the integration tests."""

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from hyxi_cloud_api import DiscoveryResult
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyxi_cloud.const import CONF_ACCESS_KEY, CONF_SECRET_KEY, DOMAIN


def cloud_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A cloud config entry with placeholder keys, added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"},
        options={},
        unique_id="ak",
    )
    entry.add_to_hass(hass)
    return entry


def serve_devices(client: AsyncMock, devices: dict) -> None:
    """Make a mocked HyxiApiClient discover devices and poll them as
    devices."""
    client.discover_devices.return_value = DiscoveryResult(
        devices={sn: {} for sn in devices}, complete=True
    )
    client.poll_devices.return_value = devices


def lookup_entity_id(
    hass: HomeAssistant, platform: str, sn: str, key: str
) -> str | None:
    """Look up an entity by its unique_id, the same way _get_power_value
    does in production -- entity_id is slugified from the device name, not
    derived from the serial number, so guessing the string directly is
    fragile."""
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"hyxi_{sn}_{key}")


def accept_subscriptions(
    client: AsyncMock, push_code: str = "code-push", alarm_code: str = "code-alarm"
) -> None:
    """Make a mocked HyxiApiClient accept push and alarm subscriptions with
    the given codes."""
    client.subscribe_real_time_data.return_value = {
        "success": True,
        "data": {"subscribeCode": push_code},
    }
    client.subscribe_alarm.return_value = {
        "success": True,
        "data": {"subscribeCode": alarm_code},
    }

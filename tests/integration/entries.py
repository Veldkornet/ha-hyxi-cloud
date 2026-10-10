"""Config entries shared by the integration tests."""

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
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

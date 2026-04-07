"""Extra tests for the DataUpdateCoordinator logic."""

# pylint: disable=protected-access, wrong-import-position

import os
import sys
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock
from unittest.mock import patch

# Mock basic environment to avoid import errors when running via unittest
# (mimicking conftest.py which is for pytest)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def ensure_mock(module_name, attributes=None):
    """Ensure a module is mocked."""
    mock_obj = MagicMock()
    if attributes:
        for attr_name, attr_value in attributes.items():
            setattr(mock_obj, attr_name, attr_value)
    sys.modules[module_name] = mock_obj
    return mock_obj


ensure_mock("homeassistant")
ensure_mock("homeassistant.helpers.update_coordinator", {"UpdateFailed": Exception})
ensure_mock("homeassistant.exceptions", {"ConfigEntryAuthFailed": Exception})
ensure_mock("homeassistant.const", {"Platform": MagicMock()})
ensure_mock("homeassistant.config_entries")
ensure_mock("homeassistant.core")
ensure_mock("homeassistant.helpers")
ensure_mock("homeassistant.helpers.device_registry")
ensure_mock("homeassistant.helpers.aiohttp_client")
ensure_mock("homeassistant.helpers.entity_platform")
ensure_mock("homeassistant.util")
ensure_mock("homeassistant.util.dt")
ensure_mock("aiohttp", {"ClientError": Exception})
ensure_mock("hyxi_cloud_api", {"__version__": "1.0.4"})


# Re-mock DataUpdateCoordinator as a real class to avoid recursion/mock issues
class MockDataUpdateCoordinator:
    def __init__(self, *args, **kwargs):
        pass


sys.modules[
    "homeassistant.helpers.update_coordinator"
].DataUpdateCoordinator = MockDataUpdateCoordinator

# We need to import after mocks are set up but before they are patched in tests
import custom_components.hyxi_cloud.coordinator as coordinator_mod

from custom_components.hyxi_cloud.const import DOMAIN
from custom_components.hyxi_cloud.coordinator import HyxiDataUpdateCoordinator


class TestHyxiDataUpdateCoordinatorExtra(IsolatedAsyncioTestCase):
    """Extra tests for HyxiDataUpdateCoordinator."""

    async def asyncSetUp(self):
        """Set up for each test."""
        self.hass = MagicMock()
        self.client = MagicMock()
        self.entry = MagicMock()
        self.entry.options = {"update_interval": 5}

        self.coordinator = HyxiDataUpdateCoordinator(self.hass, self.client, self.entry)
        # Manually set attributes that __init__ would have set
        self.coordinator.hass = self.hass
        self.coordinator.client = self.client
        self.coordinator.entry = self.entry

    async def test_async_sync_device_metadata_no_change(self):
        """Test that device registry is not updated if versions match."""
        # Mock device registry
        mock_dev_reg = MagicMock()
        # Patch dr.async_get where it is used in coordinator.py
        with patch.object(coordinator_mod.dr, "async_get", return_value=mock_dev_reg):
            # Mock existing device
            mock_device = MagicMock()
            mock_device.sw_version = "1.2.3"
            mock_device.hw_version = "V1"
            mock_device.id = "device_id"

            mock_dev_reg.async_get_device.return_value = mock_device

            # Test data - sw_version matches
            devices = {
                "SN123": {"sw_version": "1.2.3", "hw_version": "V1", "metrics": {}}
            }

            await self.coordinator._async_sync_device_metadata(devices)

            # Verify
            mock_dev_reg.async_get_device.assert_called_once_with(
                identifiers={(DOMAIN, "SN123")}
            )
            mock_dev_reg.async_update_device.assert_not_called()

    async def test_async_sync_device_metadata_with_change(self):
        """Test that device registry is updated if versions differ."""
        # Mock device registry
        mock_dev_reg = MagicMock()
        with patch.object(coordinator_mod.dr, "async_get", return_value=mock_dev_reg):
            # Mock existing device with different SW version
            mock_device = MagicMock()
            mock_device.sw_version = "1.2.2"
            mock_device.hw_version = "V1"
            mock_device.id = "device_id"

            mock_dev_reg.async_get_device.return_value = mock_device

            # Test data
            devices = {
                "SN123": {"sw_version": "1.2.3", "hw_version": "V1", "metrics": {}}
            }

            await self.coordinator._async_sync_device_metadata(devices)

            # Verify
            mock_dev_reg.async_update_device.assert_called_once_with(
                "device_id", sw_version="1.2.3", hw_version="V1"
            )

    async def test_async_sync_device_metadata_device_not_found(self):
        """Test that it handles case where device is not in registry."""
        # Mock device registry
        mock_dev_reg = MagicMock()
        with patch.object(coordinator_mod.dr, "async_get", return_value=mock_dev_reg):
            mock_dev_reg.async_get_device.return_value = None

            # Test data
            devices = {
                "SN123": {"sw_version": "1.2.3", "hw_version": "V1", "metrics": {}}
            }

            # Should not raise exception
            await self.coordinator._async_sync_device_metadata(devices)

            mock_dev_reg.async_update_device.assert_not_called()


# Reminder: Run `ruff format .` locally before you commit!

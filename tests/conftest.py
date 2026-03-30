"""Configuration for pytest."""

import os
import sys
from unittest.mock import MagicMock

# This adds the root directory to the path so 'custom_components' can be found
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock required HA modules and external library so test discovery doesn't crash
mock_ha = MagicMock()
mock_ha.__path__ = []

sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = MagicMock()
sys.modules["homeassistant.components.sensor"] = MagicMock()
sys.modules["homeassistant.components.binary_sensor"] = MagicMock()
sys.modules["homeassistant.config_entries"] = MagicMock()
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.aiohttp_client"] = MagicMock()
sys.modules["homeassistant.helpers.device_registry"] = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
sys.modules["homeassistant.util"] = MagicMock()


class MockClientError(Exception):
    """Mock ClientError."""


mock_aiohttp = MagicMock()
mock_aiohttp.ClientError = MockClientError
sys.modules["aiohttp"] = mock_aiohttp


class MockUpdateFailed(Exception):
    """Mock UpdateFailed."""


mock_coordinator = MagicMock()
mock_coordinator.UpdateFailed = MockUpdateFailed
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator


class MockConfigEntryAuthFailed(Exception):
    """Mock ConfigEntryAuthFailed."""


class MockConfigEntryNotReady(Exception):
    """Mock ConfigEntryNotReady."""


mock_exceptions = MagicMock()
mock_exceptions.ConfigEntryAuthFailed = MockConfigEntryAuthFailed
mock_exceptions.ConfigEntryNotReady = MockConfigEntryNotReady
sys.modules["homeassistant.exceptions"] = mock_exceptions

# Robustly mock hyxi_cloud_api with required __version__
mock_api = MagicMock()
mock_api.__version__ = "1.0.4"
sys.modules["hyxi_cloud_api"] = mock_api

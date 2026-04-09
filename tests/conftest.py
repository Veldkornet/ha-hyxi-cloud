"""Configuration for pytest."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# This adds the root directory to the path so 'custom_components' can be found
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))


# Custom Exception classes to avoid TypeError when catching
class MockUpdateFailed(Exception):
    """Mock UpdateFailed."""


class MockConfigEntryAuthFailed(Exception):
    """Mock ConfigEntryAuthFailed."""


class MockConfigEntryNotReady(Exception):
    """Mock ConfigEntryNotReady."""


class MockClientError(Exception):
    """Mock ClientError."""


def ensure_mock(module_name, attributes=None, mock_obj=None):
    """Ensure a module is mocked and has the required attributes if it is a mock."""
    if module_name not in sys.modules:
        sys.modules[module_name] = mock_obj if mock_obj is not None else MagicMock()

    mod = sys.modules[module_name]
    if isinstance(mod, MagicMock) and attributes:
        for attr_name, attr_value in attributes.items():
            if not hasattr(mod, attr_name) or isinstance(
                getattr(mod, attr_name), MagicMock
            ):
                setattr(mod, attr_name, attr_value)
    return mod


# Mock required HA modules and external library so test discovery doesn't crash
mock_ha = MagicMock()
mock_ha.__path__ = []

ensure_mock("homeassistant", mock_obj=mock_ha)
ensure_mock("homeassistant.components")
ensure_mock("homeassistant.components.sensor")
ensure_mock("homeassistant.components.binary_sensor")
ensure_mock("homeassistant.config_entries")
ensure_mock("homeassistant.core")
ensure_mock("homeassistant.helpers")
ensure_mock("homeassistant.helpers.aiohttp_client")
ensure_mock("homeassistant.helpers.device_registry")
ensure_mock("homeassistant.helpers.entity_platform")
ensure_mock(
    "homeassistant.helpers.update_coordinator", {"UpdateFailed": MockUpdateFailed}
)
ensure_mock("homeassistant.util")
ensure_mock("homeassistant.const")
ensure_mock(
    "homeassistant.exceptions",
    {
        "ConfigEntryAuthFailed": MockConfigEntryAuthFailed,
        "ConfigEntryNotReady": MockConfigEntryNotReady,
    },
)
ensure_mock("aiohttp", {"ClientError": MockClientError})

# Robustly mock hyxi_cloud_api with required __version__
if "hyxi_cloud_api" not in sys.modules:
    mock_api = MagicMock()
    mock_api.__version__ = "1.0.4"
    sys.modules["hyxi_cloud_api"] = mock_api

"""Configuration for pytest."""

import os
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

    # Bind child to parent (if child was just created and parent exists)
    if "." in module_name:
        parent_name, child_attr = module_name.rsplit(".", 1)
        if parent_name in sys.modules:
            parent_mod = sys.modules[parent_name]
            setattr(parent_mod, child_attr, mod)

    # Bind children to parent (if parent was just created and children exist)
    for existing_name, existing_mod in list(sys.modules.items()):
        if existing_name.startswith(module_name + ".") and existing_name != module_name:
            subpath = existing_name[len(module_name) + 1 :]
            if "." not in subpath:  # Direct child
                setattr(mod, subpath, existing_mod)

    return mod


if os.environ.get("HYXI_INTEGRATION_TEST") != "1" and not any(
    "tests/integration" in arg for arg in sys.argv
):
    # Mock required HA modules and external library so test discovery doesn't crash
    mock_ha = MagicMock()
    mock_ha.__path__ = []

    ensure_mock("homeassistant", mock_obj=mock_ha)
    ensure_mock("homeassistant.components")
    ensure_mock("homeassistant.components.cloud")
    ensure_mock("homeassistant.components.webhook")
    ensure_mock("homeassistant.components.sensor")
    ensure_mock("homeassistant.components.binary_sensor")
    ensure_mock("homeassistant.config_entries")
    ensure_mock("homeassistant.core")
    ensure_mock("homeassistant.helpers")
    ensure_mock("homeassistant.helpers.network")
    ensure_mock("homeassistant.helpers.aiohttp_client")
    ensure_mock("homeassistant.helpers.device_registry")
    ensure_mock("homeassistant.helpers.entity_platform")
    ensure_mock("homeassistant.helpers.restore_state")
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

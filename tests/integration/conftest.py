"""Fixtures for hyxi_cloud integration tests."""

from pathlib import Path

import pytest

import custom_components

# Filter out non-existent directory paths (like setuptools editable installation finder hooks)
# which cause Home Assistant's loader to throw FileNotFoundError when trying to iterdir() them.
custom_components.__path__ = [p for p in custom_components.__path__ if Path(p).is_dir()]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for testing."""
    yield

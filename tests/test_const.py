"""Tests for const.py."""
from custom_components.hyxi_cloud.const import normalize_device_type


def test_const_normalize_device_type():
    """Verify that normalize_device_type gracefully handles float conversion errors."""

    # Test error path where float conversion fails
    assert normalize_device_type("invalid.string") == "unknown"

    # Test valid float string path
    assert normalize_device_type("1.0") == "hybrid_inverter"

    # Test numeric inputs
    assert normalize_device_type(15.0) == "micro_ess"
    assert normalize_device_type(1) == "hybrid_inverter"

    # Test empty values
    assert normalize_device_type(None) == "unknown"
    assert normalize_device_type("") == "unknown"

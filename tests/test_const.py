"""Tests for the hyxi_cloud const module."""

from custom_components.hyxi_cloud.const import normalize_device_type


def test_normalize_device_type():
    """Test the normalization of device types."""
    # 1. Empty string / None
    assert normalize_device_type(None) == "unknown"
    assert normalize_device_type("") == "unknown"

    # 2. Exact device code string
    assert normalize_device_type("1") == "hybrid_inverter"
    assert normalize_device_type("3") == "collector"

    # 3. Float string direct mapping
    assert normalize_device_type("15.0") == "micro_ess"
    assert normalize_device_type("16.0") == "micro_ess"

    # 4. Int/Float input
    assert normalize_device_type(1) == "hybrid_inverter"
    assert normalize_device_type(15.0) == "micro_ess"

    # 5. String aliases defined in DEVICE_TYPE_KEYS
    assert normalize_device_type("EMS") == "micro_ess"
    assert normalize_device_type("COLLECTOR") == "collector"

    # 6. Substring match (if API returned a name instead of code)
    assert normalize_device_type("SOME_COLLECTOR") == "collector"
    assert normalize_device_type("FOO_DMU_BAR") == "collector"
    assert normalize_device_type("GRID_INVERTER") == "grid_connected_inverter"
    assert normalize_device_type("MY_INVERTER") == "hybrid_inverter"
    assert normalize_device_type("HALO_DEVICE") == "micro_ess"
    assert normalize_device_type("ESS_DEVICE") == "micro_ess"

    # 7. Case insensitivity and whitespace handling
    assert normalize_device_type(" EMS ") == "micro_ess"
    assert normalize_device_type("dmu") == "collector"

    # 8. Failed float conversions fallbacks to original logic
    assert normalize_device_type("20.ABC") == "unknown"
    assert normalize_device_type("15.0.0") == "unknown"

    # 9. Unmatched strings
    assert normalize_device_type("UNKNOWN_DEVICE") == "unknown"
    assert normalize_device_type("RANDOM_STRING") == "unknown"


def test_normalize_device_type_invalid_float():
    """Verify that normalize_device_type gracefully handles float conversion errors."""
    # Test error path where float conversion fails with ValueError
    assert normalize_device_type("invalid.string") == "unknown"

    # Test error path with TypeError (e.g. invalid object)
    assert normalize_device_type([1, 2, 3]) == "unknown"

    # Test valid float string path
    assert normalize_device_type("1.0") == "hybrid_inverter"

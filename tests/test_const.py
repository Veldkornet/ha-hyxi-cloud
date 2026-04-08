"""Tests for the hyxi_cloud const module."""

from custom_components.hyxi_cloud.const import get_raw_device_code
from custom_components.hyxi_cloud.const import get_software_version
from custom_components.hyxi_cloud.const import mask_sn
from custom_components.hyxi_cloud.const import normalize_device_type


def test_mask_sn():
    """Verify mask_sn correctly obscures serial numbers."""
    # 1. Normal SN (8+ chars)
    assert mask_sn("12345678") == "XXXX5678"
    assert mask_sn("SN123456789") == "XXXXXXX6789"

    # 2. Short SN (< 8 chars)
    assert mask_sn("1234567") == "****"
    assert mask_sn("123") == "****"

    # 3. Empty or None
    assert mask_sn("") == "****"
    assert mask_sn(None) == "****"


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


def test_normalize_device_type_extra_edge_cases():
    """Extra edge cases for normalize_device_type."""
    # Boolean inputs (converted to "TRUE"/"FALSE")
    assert normalize_device_type(True) == "unknown"
    assert normalize_device_type(False) == "unknown"

    # Large numbers
    assert normalize_device_type(999999) == "unknown"
    assert normalize_device_type(1e10) == "unknown"

    # Special characters
    assert normalize_device_type("!!!") == "unknown"
    assert normalize_device_type("@#$%") == "unknown"

    # Float strings with decimals (verify truncation/rounding behavior)
    # "15.9" -> float(15.9) -> int(15.9) -> 15 -> "15" -> "micro_ess"
    assert normalize_device_type("15.9") == "micro_ess"
    assert normalize_device_type("2.1") == "grid_connected_inverter"

    # Whitespace and casing combinations
    assert normalize_device_type("  InVeRtEr  ") == "hybrid_inverter"
    assert normalize_device_type("\tgrid_inverter\n") == "grid_connected_inverter"


def test_get_raw_device_code():
    assert get_raw_device_code({"device_type_code": "1"}) == "1"
    assert get_raw_device_code({"deviceType": "2"}) == "2"
    assert get_raw_device_code({"devType": "3"}) == "3"
    assert get_raw_device_code({"deviceCode": "4"}) == "4"
    assert get_raw_device_code({}) == ""


def test_get_software_version():
    """Test get_software_version handles different version field combinations."""
    # 1. Base case: sw_version present
    assert get_software_version({"sw_version": "V1.0.0"}) == "V1.0.0"

    # 2. Collector with wifiVer
    dev_data = {
        "sw_version": "V1.0.0",
        "device_type_code": "3",  # collector
        "metrics": {"wifiVer": "W1.0"},
    }
    assert get_software_version(dev_data) == "V1.0.0 / W1.0"

    # 3. Fallback: master and slave
    dev_data = {"metrics": {"swVerMaster": "M1.0", "swVerSlave": "S1.0"}}
    assert get_software_version(dev_data) == "Master: M1.0 | Slave: S1.0"

    # 4. Fallback: only master
    dev_data = {
        "metrics": {
            "swVerMaster": "M1.0",
        }
    }
    assert get_software_version(dev_data) == "M1.0"

    # 5. Fallback: only slave
    dev_data = {"metrics": {"swVerSlave": "S1.0"}}
    assert get_software_version(dev_data) == "S1.0"

    # 6. Edge case: nothing present
    assert get_software_version({}) is None
    assert get_software_version({"metrics": {}}) is None

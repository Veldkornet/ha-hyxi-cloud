"""Tests for the hyxi_cloud const module."""

from custom_components.hyxi_cloud.const import (
    detect_phase_type,
    get_raw_device_code,
    get_software_version,
    is_null_value,
    mask_sensitive_key_value,
    mask_sn,
    mask_url,
    normalize_device_type,
)


def test_is_null_value():
    """Verify is_null_value correctly identifies null-equivalent values."""
    # 1. None
    assert is_null_value(None) is True

    # 2. Exact match from NULL_VALUES
    assert is_null_value("") is True
    assert is_null_value("null") is True
    assert is_null_value("none") is True
    assert is_null_value("na") is True
    assert is_null_value("--") is True

    # 3. Different casing
    assert is_null_value("NULL") is True
    assert is_null_value("None") is True
    assert is_null_value("Na") is True

    # 4. Leading/trailing whitespaces
    assert is_null_value("  null  ") is True
    assert is_null_value("\tnone\n") is True
    assert is_null_value(" -- ") is True

    # 5. Non-matching strings
    assert is_null_value("0") is False
    assert is_null_value("false") is False
    assert is_null_value("unknown") is False
    assert is_null_value("valid_string") is False

    # 6. Other types
    assert is_null_value(0) is False
    assert is_null_value(0.0) is False
    assert is_null_value(False) is False
    assert is_null_value([]) is False
    assert is_null_value({}) is False


def test_mask_sn():
    """Verify mask_sn correctly obscures serial numbers."""
    # 1. Normal SN (8+ chars)
    assert mask_sn("12345678") == "ef797c81"
    assert mask_sn("SN123456789") == "c90391cf"

    # 2. Short SN (< 8 chars) are now hashed too to match API library
    assert mask_sn("1234567") == "8bb0cf6e"
    assert mask_sn("123") == "a665a459"

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
    assert normalize_device_type("MICRO_INVERTER") == "micro_inverter"
    assert normalize_device_type("OPTIMIZER") == "optimizer"
    assert normalize_device_type("METER") == "meter"
    assert normalize_device_type("ENERGY_STORAGE_BATTERY") == "battery"
    assert normalize_device_type("AC_BATTERY") == "ac_battery"
    assert normalize_device_type("MICRO_STORAGE_ALL_IN_ONE") == "micro_ess"

    # 6. Substring match (if API returned a name instead of code)
    assert normalize_device_type("SOME_COLLECTOR") == "collector"
    assert normalize_device_type("FOO_DMU_BAR") == "collector"
    assert normalize_device_type("GRID_INVERTER") == "grid_connected_inverter"
    assert normalize_device_type("SOME_MICRO_INVERTER") == "micro_inverter"
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


def test_detect_phase_type_model_suffix():
    """Test phase detection from model name suffix."""
    # Three-phase models
    assert detect_phase_type({"model": "H5K-HT"}) == "three_phase"
    assert detect_phase_type({"model": "H12K-HTA"}) == "three_phase"
    assert detect_phase_type({"model": "H50K-125K-ET"}) == "three_phase"

    # Single-phase models
    assert detect_phase_type({"model": "H3K-HS"}) == "single_phase"
    assert detect_phase_type({"model": "H6K-LS"}) == "single_phase"
    assert detect_phase_type({"model": "H3K-HS1"}) == "single_phase"

    # Case insensitive
    assert detect_phase_type({"model": "h5k-ht"}) == "three_phase"
    assert detect_phase_type({"model": "h3k-hs"}) == "single_phase"


def test_detect_phase_type_metrics():
    """Test phase detection from runtime metrics."""
    # Three-phase: structural power keys are conclusive even when values are zero.
    assert detect_phase_type({"metrics": {"ph2Loadp": 0}}) == "three_phase"
    assert detect_phase_type({"metrics": {"ph3Loadp": 0}}) == "three_phase"
    assert detect_phase_type({"metrics": {"ph2p": 0}}) == "three_phase"
    assert detect_phase_type({"metrics": {"ph3p": 0}}) == "three_phase"

    # Three-phase: non-zero ph2v/ph3v
    assert detect_phase_type({"metrics": {"ph2v": 230.0}}) == "three_phase"
    assert detect_phase_type({"metrics": {"ph3v": 228.5}}) == "three_phase"
    assert (
        detect_phase_type({"metrics": {"ph2v": 230.0, "ph3v": 229.0}}) == "three_phase"
    )

    # Zero values — not conclusive
    assert detect_phase_type({"metrics": {"ph2v": 0, "ph3v": 0}}) == "unknown"
    assert detect_phase_type({"metrics": {"ph2v": "0"}}) == "unknown"

    # Invalid metric values
    assert detect_phase_type({"metrics": {"ph2v": "N/A"}}) == "unknown"
    assert detect_phase_type({"metrics": {"ph2v": None}}) == "unknown"


def test_detect_phase_type_model_takes_priority():
    """Model suffix takes priority over metrics."""
    # Single-phase model but three-phase metrics — model wins
    dev = {"model": "H3K-HS", "metrics": {"ph2v": 230.0, "ph3v": 229.0}}
    assert detect_phase_type(dev) == "single_phase"


def test_detect_phase_type_unknown():
    """Test unknown phase detection fallback."""
    assert detect_phase_type({}) == "unknown"
    assert detect_phase_type({"model": ""}) == "unknown"
    assert detect_phase_type({"model": "SomeRandomModel"}) == "unknown"
    assert detect_phase_type({"model": None, "metrics": {}}) == "unknown"


def test_mask_url():
    """Verify mask_url correctly obscures URLs."""
    # 1. Custom URL webhook
    assert (
        mask_url("https://example.com/api/webhook/hyxi_cloud_entry_123")
        == "https://[MASKED_DOMAIN]/api/webhook/hyxi_cloud_***"
    )

    # 2. Nabu Casa webhook
    assert (
        mask_url("https://hooks.nabucasa.com/v1/abc123xyz789")
        == "https://[MASKED_DOMAIN]/v1/***"
    )

    # 3. None or empty
    assert mask_url("") == ""
    assert mask_url(None) == ""


def test_mask_sensitive_key_value():
    """Verify mask_sensitive_key_value correctly obscures sensitive info."""
    # 1. Null values
    assert mask_sensitive_key_value("sn", None) is None
    # Note: the function actually only checks `value is None`, it does not use `is_null_value`
    # Let's verify empty string isn't considered sensitive and masked to ****, or is it?
    # mask_sn("") -> "****"
    assert mask_sensitive_key_value("sn", "") == "****"

    # 2. Exact matches in sensitive_exact
    assert mask_sensitive_key_value("alias", "Home Inverter") == mask_sn(
        "Home Inverter"
    )
    assert mask_sensitive_key_value("plantAddress", "123 Main St") == mask_sn(
        "123 Main St"
    )
    assert mask_sensitive_key_value("sn", "12345678") == mask_sn("12345678")

    # 3. Endswith "sn" matches
    assert mask_sensitive_key_value("devicesn", "dev_123") == mask_sn("dev_123")
    assert mask_sensitive_key_value("batsn", "bat_123") == mask_sn("bat_123")
    assert mask_sensitive_key_value("someRandomSN", "rand_123") == mask_sn("rand_123")

    # 4. "plantid" or "imei" in key_lower
    assert mask_sensitive_key_value("my_plantid_number", "plant_123") == mask_sn(
        "plant_123"
    )
    assert mask_sensitive_key_value("gprs_imei_code", "imei_123") == mask_sn("imei_123")

    # 5. Non-sensitive keys remain untouched
    assert mask_sensitive_key_value("power", 5000) == 5000
    assert mask_sensitive_key_value("voltage", 230.5) == 230.5
    assert mask_sensitive_key_value("status", "OK") == "OK"
    assert mask_sensitive_key_value("model", "H5K-HT") == "H5K-HT"

    # 6. Check different types of values
    assert mask_sensitive_key_value("sn", 12345) == mask_sn("12345")

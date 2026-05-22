"""Constants for the HYXI Cloud integration."""

from homeassistant.const import Platform

DOMAIN = "hyxi_cloud"
CONF_ACCESS_KEY = "access_key"
CONF_SECRET_KEY = "secret_key"
BASE_URL = "https://open.hyxicloud.com"

MANUFACTURER = "HYXI Power"
VERSION = "1.4.1"

CONF_BACK_DISCOVERY = "back_discovery"

NULL_VALUES = {"", "null", "none", "na", "--"}

# Helper to map device codes to translation keys for HA sensor states
DEVICE_TYPE_KEYS = {
    "1": "hybrid_inverter",
    "2": "grid_connected_inverter",
    "3": "collector",
    "15": "micro_ess",
    "16": "micro_ess",
    "106": "hybrid_inverter",
    "607": "collector",
    "HYBRID_INVERTER": "hybrid_inverter",
    "STRING_INVERTER": "grid_connected_inverter",
    "MICRO_INVERTER": "micro_inverter",
    "EMS": "micro_ess",
    "DMU": "collector",
    "COLLECTOR": "collector",
    "ALL_IN_ONE": "all_in_one",
    "OPTIMIZER": "optimizer",
    "METER": "meter",
    "ENERGY_STORAGE_BATTERY": "battery",
    "AC_BATTERY": "ac_battery",
    "MICRO_STORAGE_ALL_IN_ONE": "micro_ess",
}


def mask_sn(sn: str) -> str:
    """Mask a serial number for logs, masking all but the last 4 chars with X.

    Matches the _mask_id format used in the API library.
    """
    if not sn:
        return "****"
    sn_str = str(sn)
    if len(sn_str) < 8:
        return "****"
    mask_len = len(sn_str) - 4
    return f"{'X' * mask_len}{sn_str[-4:]}"


def get_raw_device_code(dev_data: dict) -> str:
    """Extract the raw device type code from device data payload."""
    return (
        dev_data.get("device_type_code")
        or dev_data.get("deviceType")
        or dev_data.get("devType")
        or dev_data.get("deviceCode")
        or ""
    )


def get_software_version(dev_data: dict) -> str | None:
    """Extract and format the software version for a device."""
    sw_version = dev_data.get("sw_version")
    if sw_version:
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if device_type == "collector":
            metrics = dev_data.get("metrics", {})
            wifi_ver = metrics.get("wifiVer")
            if wifi_ver:
                sw_version = f"{sw_version} / {wifi_ver}"
        return sw_version

    metrics = dev_data.get("metrics", {})
    sw_master = metrics.get("swVerMaster")
    sw_slave = metrics.get("swVerSlave")

    if sw_master and sw_slave:
        return f"Master: {sw_master} | Slave: {sw_slave}"
    if sw_master:
        return sw_master
    if sw_slave:
        return sw_slave

    return None


def normalize_device_type(code: str | int | float) -> str:
    """Normalize a device type code/string to a translation key.

    Ensures that values match the keys in strings.json (lowercase, no spaces).
    """
    if code is None or code == "":
        return "unknown"

    code_str = str(code).upper().strip()

    # 1. Check numeric/direct mapping (handle float strings like "15.0")
    lookup_key = code_str
    if "." in code_str:
        try:
            lookup_key = str(int(float(code_str)))
        except (
            ValueError,
            TypeError,
        ):
            # If float conversion fails (e.g. string labels), just use original code_str
            pass

    if (res := DEVICE_TYPE_KEYS.get(lookup_key)) is not None:
        return res

    # 2. String mapping (if API returned a name instead of code)
    if "COLLECTOR" in code_str or "DMU" in code_str:
        return "collector"
    if "INVERTER" in code_str:
        if "MICRO" in code_str:
            return "micro_inverter"
        if "GRID" in code_str:
            return "grid_connected_inverter"
        return "hybrid_inverter"
    if "ESS" in code_str or "HALO" in code_str:
        return "micro_ess"
    if "ALL_IN_ONE" in code_str or "ALL-IN-ONE" in code_str:
        return "all_in_one"

    return "unknown"


def detect_phase_type(dev_data: dict) -> str:
    """Detect whether a device is single-phase or three-phase.

    Detection strategy (in priority order):
    1. Model name suffix: -HT/-HTA = three-phase, -HS/-LS = single-phase
    2. Runtime metrics: structural phase keys or non-zero ph2v/ph3v = three-phase
    3. Default: "unknown" means no control entities are created (safety-first)
    """
    # 1. Model name suffix check
    model = (dev_data.get("model") or "").upper().strip()
    if model:
        # Strip trailing power rating (e.g. "H5K-HT" -> check "-HT")
        for suffix in ("-HTA", "-HT", "-ET"):
            if suffix in model:
                return "three_phase"
        for suffix in ("-HS", "-LS", "-HS1"):
            if suffix in model:
                return "single_phase"

    # 2. Runtime metrics — structural indicators of three-phase
    # Power metric keys (ph3Loadp, ph3p, ph2p, ph2Loadp) are checked by PRESENCE only —
    # the API only includes these keys for three-phase devices; the value can
    # legitimately be zero (e.g. no load at night). Voltage metrics are
    # checked by value since the schema may include them on single-phase devices.
    metrics = dev_data.get("metrics") or {}
    for key in ("ph3Loadp", "ph3p", "ph2p", "ph2Loadp"):
        if key in metrics:
            return "three_phase"

    # Voltage metrics are checked by value since the schema may include them
    # on single-phase devices.
    for key in ("ph2v", "ph3v"):
        try:
            if float(metrics.get(key, 0)) > 0:
                return "three_phase"
        except ValueError, TypeError:
            continue

    return "unknown"


PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SWITCH,
]

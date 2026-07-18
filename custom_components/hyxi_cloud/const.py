"""Constants for the HYXI Cloud integration."""

from typing import Any

from homeassistant.const import Platform

DOMAIN = "hyxi_cloud"
CONF_ACCESS_KEY = "access_key"
CONF_SECRET_KEY = "secret_key"
BASE_URL_DEFAULT = "https://open.hyxicloud.com"
# Legacy alias kept for any imports that haven't migrated yet
BASE_URL = BASE_URL_DEFAULT

MANUFACTURER = "HYXI Power"
VERSION = "1.6.5"

CONF_BACK_DISCOVERY = "back_discovery"

# Real-time Webhook Push Constants
CONF_ENABLE_PUSH = "enable_realtime_push"
CONF_PUSH_RATE = "realtime_push_rate"
CONF_PUSH_URL = "realtime_push_url"
DEFAULT_PUSH_RATE = 10  # 10 seconds (converted to ms at SDK call site)

TOKEN_ERROR_MESSAGES = (
    "Authentication failed",
    "User information not found",
    "Invalid access token",
    "Server rejected token",
    "A0000",
    "C000006",
)

NULL_VALUES = {"", "null", "none", "na", "--"}


def is_null_value(value: Any) -> bool:
    """Check if a value is considered null or equivalent."""
    return value is None or (
        isinstance(value, str) and value.strip().lower() in NULL_VALUES
    )


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
    """Mask a serial number/identifier securely using SHA-256 (first 8 chars) to match API library.

    Matches the _mask_id format used in the API library.
    """
    import hashlib

    if not sn or str(sn) == "None":
        return "****"
    sn_str = str(sn)
    return hashlib.sha256(sn_str.encode("utf-8")).hexdigest()[:8]


def mask_url(url: str | None) -> str:
    """Mask a URL host and webhook ID to prevent leaks in logs."""
    if not url:
        return ""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(str(url))
        # Mask netloc
        # For path, mask the final part which is the webhook ID (e.g. /api/webhook/hyxi_cloud_abc123)
        path_parts = parsed.path.strip("/").split("/")
        if path_parts:
            # Check if it looks like a webhook ID
            if path_parts[-1].startswith("hyxi_cloud_"):
                path_parts[-1] = "hyxi_cloud_***"
            elif len(path_parts[-1]) > 10:  # arbitrary long ID
                path_parts[-1] = "***"
        masked_path = "/" + "/".join(path_parts)
        return f"{parsed.scheme}://[MASKED_DOMAIN]{masked_path}"
    except Exception:  # pylint: disable=broad-except
        return "https://[MASKED_DOMAIN]/api/webhook/hyxi_cloud_***"


def mask_sensitive_key_value(key: str, value: Any) -> Any:
    """Check if key contains sensitive info (SN, plant ID, IMEI, alias, address, etc.) and mask it."""
    if value is None:
        return None

    sensitive_exact = {
        "alias",
        "plantaddress",
        "plantname",
        "devicename",
        "alarmname",
        "gprsimei",
        "sn",
        "plantid",
        "parentsn",
        "devicesn",
        "batsn",
        "emssn",
    }

    key_lower = str(key).lower()
    if (
        key_lower in sensitive_exact
        or key_lower.endswith("sn")
        or "plantid" in key_lower
        or "imei" in key_lower
    ):
        return mask_sn(str(value))

    return value


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
        except ValueError, TypeError:
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


def is_battery_control_enabled(entry: Any, coordinator: Any) -> bool:
    """Return True if battery control is enabled by user options.

    If not explicitly set in options, defaults to False.
    """
    val = entry.options.get("enable_battery_control")
    if val is not None:
        return val

    return False


PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SWITCH,
]


# Energy Manager option keys
CONF_EM_ENABLED = "em_enabled"
CONF_EM_INVERTER_SN = "em_inverter_sn"
CONF_EM_P1_ENTITY = "em_p1_entity"
CONF_EM_FORECAST_ENTITY = "em_forecast_entity"
CONF_EM_FORECAST_POWER_ENTITY = "em_forecast_power_entity"
CONF_EM_BATTERY_OVERRIDE = "em_battery_capacity_override"
CONF_EM_BATTERY_CAPACITY = "em_battery_capacity_wh"
CONF_EM_DRY_RUN = "em_dry_run"
CONF_EM_LOOP_INTERVAL = "em_loop_interval"

# EM parameter defaults (match pyscript values)
EM_DEFAULTS: dict[str, int | float] = {
    "high_load_threshold": 6500,
    "max_charge_power": 5000,
    "max_discharge_power": 5000,
    "min_solar_for_charge": 1000,
    "mode_switch_cooldown": 60,
    "power_change_threshold": 100,
    "power_adjust_cooldown": 30,
    "night_buffer_pct": 5,
    "avg_night_consumption": 400,
    "charge_margin": 150,
    "charge_entry_threshold": 500,
    "charge_reentry_delay": 300,
    "bottomout_cooldown": 300,
    "p1_smoothing_period": 60,
    "max_grid_export": 0,
}
EM_LOOP_INTERVAL = 15  # seconds

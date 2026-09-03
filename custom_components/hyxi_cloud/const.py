"""Constants for the HYXI Cloud integration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.const import Platform

if TYPE_CHECKING:
    from modbus_connection import ModbusSerialParams, ModbusTcpParams

DOMAIN = "hyxi_cloud"
CONF_ACCESS_KEY = "access_key"
CONF_SECRET_KEY = "secret_key"
BASE_URL_DEFAULT = "https://open.hyxicloud.com"
# Legacy alias kept for any imports that haven't migrated yet
BASE_URL = BASE_URL_DEFAULT

# HYXI Cloud server regions -- each region is a physically separate server,
# so the wrong one means the account simply can't be found (not a bad-credentials error).
CONF_REGION = "region"
DEFAULT_REGION = "eu"
REGION_BASE_URLS: dict[str, str] = {
    "eu": BASE_URL_DEFAULT,
    "na": "https://open-or.hyxicloud.com",
    "cn": "https://open-cn.hyxicloud.com",
}

# ISO 3166-1 alpha-2 country codes mapped to their HYXI server region, used to
# suggest a default in the config flow from Home Assistant's configured country.
_COUNTRY_REGION_MAP: dict[str, str] = {
    "CN": "cn",
    "US": "na",
    "CA": "na",
    "MX": "na",
}


def resolve_base_url(region: str | None) -> str:
    """Resolve a region code to its HYXI Cloud server base URL."""
    return REGION_BASE_URLS.get(region or DEFAULT_REGION, BASE_URL_DEFAULT)


def region_for_base_url(base_url: str | None) -> str:
    """Reverse-lookup a region code from a stored base URL.

    Used to preselect the reauth region dropdown for entries created
    before region selection existed (which only ever stored "base_url",
    never a "region" key).
    """
    if not base_url:
        return DEFAULT_REGION
    for region, url in REGION_BASE_URLS.items():
        if url == base_url:
            return region
    return DEFAULT_REGION


def default_region_for_country(country: str | None) -> str:
    """Suggest a HYXI server region from a Home Assistant country code.

    This is only ever a suggestion preselected in the config flow --
    the user can always override it, since country and account region
    don't always match (e.g. an expat using a developer account tied to
    their home region).
    """
    if not country:
        return DEFAULT_REGION
    return _COUNTRY_REGION_MAP.get(country.upper(), DEFAULT_REGION)


MANUFACTURER = "HYXI Power"
# manifest.json is the single source of truth for the integration version.
_MANIFEST = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)
VERSION = _MANIFEST["version"]

# Transport selection. The integration can reach a device through HYXI's
# cloud API or over a local RS485 Modbus link. Entries created before this
# existed have no transport key at all, so absence means cloud.
CONF_TRANSPORT = "transport"
TRANSPORT_CLOUD = "cloud"
TRANSPORT_MODBUS = "modbus"
DEFAULT_TRANSPORT = TRANSPORT_CLOUD

# Modbus connection settings, stored in entry.data
CONF_MODBUS_TYPE = "modbus_type"
MODBUS_TYPE_TCP = "tcp"
MODBUS_TYPE_SERIAL = "serial"
CONF_MODBUS_HOST = "modbus_host"
CONF_MODBUS_PORT = "modbus_port"
CONF_MODBUS_DEVICE = "modbus_device"
CONF_MODBUS_BAUDRATE = "modbus_baudrate"
CONF_MODBUS_UNIT = "modbus_unit"

DEFAULT_MODBUS_PORT = 502
DEFAULT_MODBUS_BAUDRATE = 115200
DEFAULT_MODBUS_UNIT = 1
# Read/response timeout for the one-time setup probe only
# (config_flow._probe_and_detect_modbus / _probe_and_detect_modbus_tcp).
# The operational path takes its unit from Home Assistant's shared `modbus`
# connection, which fixes its own timeout (10s) and is not tunable from
# here. A real device on a local network answers in well under a second,
# and the framer probe already has to try up to two framers x two signature
# registers each: at the shared connection's 10s a wrong-framer gateway
# (which looks exactly like an unreachable device from here) would cost
# ~20s before the other framer is tried; at DETECTION_TIMEOUT, ~6s.
DETECTION_TIMEOUT = 3.0
# A bare TCP reachability check, tried before any framer -- tmodbus's own
# TCP connect timeout defaults to 10s and modbus_connection never overrides
# it, so it is not reachable through DETECTION_TIMEOUT (which only bounds
# the read/response wait *after* a connection exists). Without this, a
# gateway that's simply unreachable (wrong IP, powered off, wrong VLAN --
# anything where packets are silently dropped rather than actively
# refused) costs up to that hidden 10s once per framer tried. A real
# device on a local network completes a TCP handshake in a few
# milliseconds, so this has ample margin without inheriting that cost.
DETECTION_CONNECT_TIMEOUT = 2.0
# Local polling is cheap compared with the rate-limited cloud API.
DEFAULT_MODBUS_INTERVAL = 15

# Which wire framing a TCP gateway speaks. Detected automatically during
# setup (see config_flow._probe_and_detect_modbus_tcp), same as family --
# nothing in the setup form distinguishes an RS485-to-Ethernet gateway
# that tunnels raw RTU frames over a plain TCP socket ("rtu", e.g.
# Waveshare's "Protocol: None") from one that speaks native Modbus-TCP/
# MBAP framing and translates to RTU on the wire itself ("socket", e.g.
# Waveshare's "Modbus TCP to RTU"). A serial connection has no such
# ambiguity -- a USB RS485 adapter only ever carries raw RTU -- so this
# only applies to TCP entries.
CONF_MODBUS_FRAMER = "modbus_framer"
DEFAULT_MODBUS_FRAMER = "rtu"
# Tried in this order: "socket" first. Home Assistant's own built-in
# `modbus` integration treats plain "tcp" as real Modbus-TCP/MBAP framing
# and raw RTU-over-TCP as the separate, explicitly-named "rtuovertcp"
# option, and vendor tutorials for these cheap RS485-Ethernet gateways
# generally push "Modbus TCP to RTU" (-> "socket" here) as the intended
# mode for talking to a smart-home platform -- passthrough ("rtu" here) is
# more of a niche "make a remote serial port look local" setup. Previously
# "rtu" was tried first, matching the one gateway this transport had been
# validated against at the time; a second, independent real-world gateway
# turned out to be "socket", matching this reasoning rather than that
# original assumption.
MODBUS_TCP_FRAMERS: tuple[Literal["rtu", "socket"], ...] = ("socket", "rtu")

# Which register map an entry talks. Detected automatically during setup
# (see config_flow._detect_modbus_family) rather than asked of the user --
# the two documents' confirmed address ranges don't overlap (hybrid tops
# out at 3121, HALO starts at 4000), so a real value at either family's
# signature register is strong, direct evidence.
CONF_MODBUS_FAMILY = "modbus_family"
MODBUS_FAMILY_HALO = "halo"
MODBUS_FAMILY_HYBRID = "hybrid"
# Two different jobs, both about there being no confirmed family to use:
# - In config_flow._probe_and_detect_modbus, a placeholder returned
#   alongside every error path (unidentified device, no response,
#   connection failure, library missing) where no family is ever
#   persisted, since an error there means no entry gets created. A device
#   that's confirmed reachable but answers neither signature register is
#   refused rather than guessed at, so this value never actually picks a
#   register map at setup time.
# - In __init__.py's coordinator builder, a genuine fallback for an entry
#   created before CONF_MODBUS_FAMILY existed at all and so carries none
#   -- absence there means the newer, stronger-evidenced default, the
#   same way entry_transport() covers pre-Modbus entries. Hybrid is the
#   stronger-evidenced document of the two -- the vendor's current one,
#   for the exact hardware this transport was built against.
DEFAULT_MODBUS_FAMILY = MODBUS_FAMILY_HYBRID

# One register per family that's cheap and safe to read and lives outside
# the other family's confirmed range: HALO's power on/off state (input 4100,
# from the Micro Storage RS485 document V1.0) and the hybrid's own
# communication protocol version (input 0, from the RS485_MODBUS RTU Hybrid
# Inverter Protocol V4.1). A plausible value at either is treated as
# identifying evidence; a Modbus exception -- or a zero-length read result,
# which some inverters send instead -- still proves the device is present and
# speaking Modbus, just not which family.
#
# Order and choice both changed after issue #611. A HALO whose BMS was
# offline answered the old HALO signature (input 4980, BMS SOC) with nothing
# at all, while answering the hybrid protocol-version register with a real
# value -- so it was detected as a hybrid. switch_status is a core inverter
# register that doesn't depend on the BMS, and checking HALO first means such
# a unit is identified before the hybrid register is ever read.
MODBUS_FAMILY_SIGNATURES: tuple[tuple[str, str, int], ...] = (
    (MODBUS_FAMILY_HALO, "input", 4100),
    (MODBUS_FAMILY_HYBRID, "input", 0),
)

# HALO's switch_status (registers.py) is a documented on/off flag -- 0 or 1,
# nothing else. A value outside that at the signature register isn't a HALO
# answering oddly, it's some other device having *something* at that address.
# The hybrid protocol version is a positive version number; zero is commonly
# returned by gateways/devices for an unmapped register, including HALO, so it
# must not identify a device as hybrid.
HALO_SWITCH_SIGNATURE_MAX_RAW = 1
HYBRID_PROTOCOL_SIGNATURE_MIN_RAW = 1

# Minimum inter-frame spacing per family. The HALO document requires more
# than 200ms; the hybrid document requires more than 500ms -- a real
# difference, not a rounding choice, and using the HALO figure against a
# hybrid device would violate its documented timing. DETECTION_SPACING is
# used only before a family is known (during setup's probe/detect pass),
# and is deliberately the more conservative of the two.
MODBUS_MESSAGE_SPACING: dict[str, float] = {
    MODBUS_FAMILY_HALO: 0.2,
    MODBUS_FAMILY_HYBRID: 0.5,
}
DETECTION_MESSAGE_SPACING = max(MODBUS_MESSAGE_SPACING.values())


def modbus_params(
    config: Mapping[str, Any],
    *,
    framer: Literal["socket", "rtu", "ascii"] | None = None,
) -> ModbusSerialParams | ModbusTcpParams:
    """Build the modbus_connection link parameters from a config mapping.

    `config` is an entry's ``.data`` or a config-flow ``user_input``.
    Optional keys fall back to their defaults, so an entry written by an
    older schema still resolves. `framer` overrides the TCP wire framing:
    the setup probe passes each candidate in turn, the operational path
    passes None to take whatever detection stored.

    Every HYXI inverter runs its RS485 line at 8 data bits, no parity, one
    stop bit -- both protocol documents state it, only the baud rate is
    user-selectable -- so those three are fixed here rather than asked of
    the caller or the form.
    """
    from modbus_connection import ModbusSerialParams, ModbusTcpParams

    if config.get(CONF_MODBUS_TYPE) == MODBUS_TYPE_SERIAL:
        return ModbusSerialParams(
            device=config[CONF_MODBUS_DEVICE],
            baudrate=int(config.get(CONF_MODBUS_BAUDRATE, DEFAULT_MODBUS_BAUDRATE)),
            bytesize=8,
            parity="N",
            stopbits=1,
        )
    return ModbusTcpParams(
        host=config[CONF_MODBUS_HOST],
        port=int(config.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)),
        framer=framer or config.get(CONF_MODBUS_FRAMER, DEFAULT_MODBUS_FRAMER),
    )


def entry_transport(entry: Any) -> str:
    """Return the transport an entry uses.

    Entries predating local Modbus support carry no transport key, so a
    missing value means cloud rather than an error.
    """
    return (getattr(entry, "data", None) or {}).get(CONF_TRANSPORT, DEFAULT_TRANSPORT)


def is_modbus_entry(entry: Any) -> bool:
    """Return True when an entry talks to its device over local Modbus."""
    return entry_transport(entry) == TRANSPORT_MODBUS


def entry_stable_key(entry: Any) -> str:
    """Return a per-entry identifier that survives a remove-and-re-add.

    `entry.entry_id` is regenerated every time the integration is deleted
    and set up again, so anything derived from it (an entity unique_id, a
    device identifier) points at a fresh object on re-add and strands the
    old one's long-term statistics. `entry.unique_id` -- the account access
    key for a cloud entry, `host:port:unit` / `device:unit` for Modbus --
    is stable across a re-add, but it's a credential/address, so it's
    returned as a SHA-256 digest rather than in cleartext: the result ends
    up in entity ids, device identifiers, and migration log lines. An entry
    with no `unique_id` falls back to the (non-sensitive, opaque) entry_id.
    """
    import hashlib

    unique_id = getattr(entry, "unique_id", None)
    if isinstance(unique_id, str) and unique_id:
        return hashlib.sha256(unique_id.encode("utf-8")).hexdigest()[:16]
    return entry.entry_id


CONF_BACK_DISCOVERY = "back_discovery"

# Real-time Webhook Push Constants
CONF_ENABLE_PUSH = "enable_realtime_push"
CONF_PUSH_RATE = "realtime_push_rate"
CONF_PUSH_URL = "realtime_push_url"
DEFAULT_PUSH_RATE = 10  # 10 seconds (converted to ms at SDK call site)

NULL_VALUES = {"", "null", "none", "na", "--"}

# Metric keys whose sensors belong to the battery pack rather than the
# inverter, so HyxiSensor groups them under the "Battery {batSn}" device.
# (Their unique_id keys off the inverter serial, not batSn -- see
# HyxiSensor.__init__.)
BATTERY_SENSORS = {
    "batSoc",
    "pbat",
    "batP",
    "batSoh",
    "bat_charge_total",
    "bat_discharge_total",
    "bat_charging",
    "bat_discharging",
    "batV",
    "batI",
    "batVch",
    "batVcl",
    "batTch",
    "batTcl",
    "batTmp",
    "batIcm",
    "batIdm",
    "bmsState",
    "batOperatingStatus",
    "batAlarm1",
    "batAlarm2",
    "batAlarm3",
    "batCapacityAh",
    "batNominalCapacity",
    "llcBusVoltage",
    "batChargeV",
    "batChargeI",
    "batChargeP",
    "batDischargeV",
    "batDischargeI",
    "batDischargeP",
}


def is_null_value(value: Any) -> bool:
    """Check if a value is considered null or equivalent."""
    return value is None or (
        isinstance(value, str) and value.strip().lower() in NULL_VALUES
    )


def is_zero_value(value: Any) -> bool:
    """Check if a value is numerically zero, regardless of its exact string/type form.

    Unlike a literal `str(value) == "0.0"` check, this matches ints, "0",
    "0.00", "-0.0", etc. Non-numeric/unparseable values are treated as
    not-zero rather than raising.
    """
    try:
        return not float(value)
    except TypeError, ValueError, OverflowError:
        return False


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

# Micro ESS (HALO/HYX-MS3000AC) Power On/Off control (controlId 1011) is
# fully implemented (HyxiMicroEssPowerSwitch, hyxi_cloud_api.set_micro_ess_power)
# but disabled here: live community testing confirmed HYXI's API rejects the
# control write for third-party developer apps with a permission error
# (code=B003026), and there's no developer portal setting to request access.
# Flip to True if HYXI ever grants Micro ESS control API access — see
# README's "Micro ESS (HALO)" section for details.
MICRO_ESS_CONTROL_SUPPORTED = False


def mask_sn(sn: str | None) -> str:
    """Mask a serial number/identifier securely using SHA-256 (first 8 chars) to match API library.

    Matches the _mask_id format used in the API library.
    """
    import hashlib

    if not sn or str(sn) == "None":
        return "****"
    sn_str = str(sn)
    return hashlib.sha256(sn_str.encode("utf-8")).hexdigest()[:8]


def mask_subscription_code(code: str | None) -> str:
    """Mask a HYXI push subscription code securely using SHA-256 (first 8 chars).

    Uses the same approach as mask_sn -- a subscription code is just as
    much an opaque account-linked identifier and shouldn't appear in
    cleartext in logs, even though it isn't a device serial number. The
    "(masked)" suffix makes it obvious this isn't the literal code to copy
    into the hyxi_cloud.cancel_subscription service or the API library.
    """
    return f"{mask_sn(code)} (masked)"


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

    return _match_device_type_by_name(code_str)


def _match_device_type_by_name(code_str: str) -> str:
    """Match a device type by substring when there's no direct code lookup."""
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
    phase = _phase_from_model_suffix(dev_data)
    if phase is not None:
        return phase

    phase = _phase_from_metrics(dev_data)
    if phase is not None:
        return phase

    return "unknown"


def _phase_from_model_suffix(dev_data: dict) -> str | None:
    """Detect phase from the model name's trailing suffix, if present.

    None means the suffix was absent or unrecognized -- try the next
    strategy.
    """
    model = (dev_data.get("model") or "").upper().strip()
    if not model:
        return None
    # Strip trailing power rating (e.g. "H5K-HT" -> check "-HT")
    for suffix in ("-HTA", "-HT", "-ET"):
        if suffix in model:
            return "three_phase"
    for suffix in ("-HS", "-LS", "-HS1"):
        if suffix in model:
            return "single_phase"
    return None


def _phase_from_metrics(dev_data: dict) -> str | None:
    """Detect phase from runtime metrics: structural key presence, then
    voltage value. None means neither strategy found a signal.
    """
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

    return None


def is_battery_control_enabled(entry: Any) -> bool:
    """Return True if battery control is enabled by user options.

    If not explicitly set in options, defaults to False.
    """
    val = entry.options.get("enable_battery_control")
    if val is not None:
        return val

    return False


def is_control_capable_device_type(entry: Any, device_type: str) -> bool:
    """Return True if this device type can receive control commands on
    this entry's transport.

    hybrid_inverter and all_in_one are controllable on either transport.
    micro_ess (HALO) is controllable over local Modbus -- the register map
    has a working VPP dispatch block with no permission check -- but not
    over the cloud, where HYXI's API rejects the write outright (see
    MICRO_ESS_CONTROL_SUPPORTED's docstring, a few lines above this file's
    device-type table). This is the one place that distinction is made;
    every entity platform should call this rather than checking
    device_type or MICRO_ESS_CONTROL_SUPPORTED directly, so the two
    transports can never silently disagree about which devices are
    controllable.
    """
    if device_type in ("hybrid_inverter", "all_in_one"):
        return True
    if device_type == "micro_ess":
        return MICRO_ESS_CONTROL_SUPPORTED or is_modbus_entry(entry)
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

# Bounds for the adaptive avg_night_consumption parameter. Shared between
# number.py's EM_NUMBER_DEFS entry (the user-facing slider's declared
# range/step) and engine.py's _update_night_estimate (which clamps and
# quantizes its own EMA-computed value to the same bounds before writing
# it directly to the entity, bypassing the UI's normal input validation).
AVG_NIGHT_CONSUMPTION_MIN = 100
AVG_NIGHT_CONSUMPTION_MAX = 2000
AVG_NIGHT_CONSUMPTION_STEP = 50

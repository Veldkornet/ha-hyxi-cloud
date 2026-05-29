"""Tests for Energy Manager entity setup and definitions."""

import ast
import json
import re
from pathlib import Path

_CONST_PATH = Path(__file__).parent / "../custom_components/hyxi_cloud/const.py"


def _read_const() -> str:
    """Read const.py as text."""
    return _CONST_PATH.read_text(encoding="utf-8")


def _get_em_defaults() -> dict:
    """Parse EM_DEFAULTS dict from const.py using ast.literal_eval."""
    content = _read_const()
    match = re.search(
        r"EM_DEFAULTS:\s*dict\[.*?\]\s*=\s*(\{.*?\})",
        content,
        re.DOTALL,
    )
    assert match, "Could not find EM_DEFAULTS in const.py"
    return ast.literal_eval(match.group(1))


def _get_conf_keys() -> tuple[str, str, str]:
    """Parse CONF_EM_* string assignments from const.py."""
    content = _read_const()
    keys = {}
    for var in ("CONF_EM_ENABLED", "CONF_EM_INVERTER_SN", "CONF_EM_P1_ENTITY"):
        match = re.search(rf'^{var}\s*=\s*"([^"]+)"', content, re.MULTILINE)
        assert match, f"Could not find {var} in const.py"
        keys[var] = match.group(1)
    return (
        keys["CONF_EM_ENABLED"],
        keys["CONF_EM_INVERTER_SN"],
        keys["CONF_EM_P1_ENTITY"],
    )


def _parse_em_number_defs():
    """Parse EM_NUMBER_DEFS from number.py without importing (avoids HA metaclass issues).

    Returns list of dicts with keys: key, unit, min_val, max_val, step, icon.
    """
    path = Path(__file__).parent / "../custom_components/hyxi_cloud/number.py"
    content = path.read_text(encoding="utf-8")

    # Find the EM_NUMBER_DEFS list block
    match = re.search(
        r"EM_NUMBER_DEFS:\s*list\[EMNumberDef\]\s*=\s*\[(.*?)\]",
        content,
        re.DOTALL,
    )
    if not match:
        return []

    block = match.group(1)
    defs = []
    # Parse each EMNumberDef(...) call
    for m in re.finditer(
        r'EMNumberDef\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*([^,]+)\s*,'
        r"\s*([^,]+)\s*,\s*([^,]+)\s*,\s*\"([^\"]+)\"\s*\)",
        block,
    ):
        defs.append(
            {
                "key": m.group(1),
                "unit": m.group(2),
                "min_val": float(m.group(3)),
                "max_val": float(m.group(4)),
                "step": float(m.group(5)),
                "icon": m.group(6),
            }
        )
    return defs


# ═══════════════════════════════════════════════════════════════════════
# EM Constants
# ═══════════════════════════════════════════════════════════════════════


class TestEMConstants:
    """Test EM constant definitions."""

    def test_em_defaults_has_all_required_keys(self):
        """EM_DEFAULTS should have all 15 parameter keys (not soc_min/soc_max, not battery_capacity_wh)."""
        required_keys = [
            "night_buffer_pct",
            "high_load_threshold",
            "max_charge_power",
            "max_discharge_power",
            "min_solar_for_charge",
            "mode_switch_cooldown",
            "power_change_threshold",
            "power_adjust_cooldown",
            "avg_night_consumption",
            "charge_margin",
            "charge_entry_threshold",
            "charge_reentry_delay",
            "bottomout_cooldown",
            "p1_smoothing_period",
            "max_grid_export",
        ]
        em_defaults = _get_em_defaults()
        for key in required_keys:
            assert key in em_defaults, f"Missing EM_DEFAULTS key: {key}"

    def test_em_defaults_does_not_include_soc_limits(self):
        """EM_DEFAULTS must NOT include soc_min/soc_max — those come from protection."""
        em_defaults = _get_em_defaults()
        assert "soc_min" not in em_defaults
        assert "soc_max" not in em_defaults

    def test_em_defaults_does_not_include_battery_capacity(self):
        """EM_DEFAULTS must NOT include battery_capacity_wh — set in options flow."""
        em_defaults = _get_em_defaults()
        assert "battery_capacity_wh" not in em_defaults

    def test_em_defaults_values_are_reasonable(self):
        """EM_DEFAULTS values should be within sane ranges."""
        em_defaults = _get_em_defaults()
        assert em_defaults["max_charge_power"] > 0
        assert em_defaults["max_discharge_power"] > 0
        assert em_defaults["mode_switch_cooldown"] >= 10
        assert em_defaults["night_buffer_pct"] >= 0
        assert em_defaults["high_load_threshold"] > 0

    def test_conf_keys_are_strings(self):
        """Config option keys should be non-empty strings."""
        conf_em_enabled, conf_em_inverter_sn, conf_em_p1_entity = _get_conf_keys()
        assert isinstance(conf_em_enabled, str) and conf_em_enabled
        assert isinstance(conf_em_inverter_sn, str) and conf_em_inverter_sn
        assert isinstance(conf_em_p1_entity, str) and conf_em_p1_entity


# ═══════════════════════════════════════════════════════════════════════
# EM Number Definitions
# ═══════════════════════════════════════════════════════════════════════


class TestEMNumberDefinitions:
    """Test EM number entity definitions."""

    def test_em_number_defs_match_defaults(self):
        """Every EM number def key should have a matching EM_DEFAULTS entry."""
        em_defs = _parse_em_number_defs()
        em_defaults = _get_em_defaults()
        assert len(em_defs) > 0, "Could not parse EM_NUMBER_DEFS from number.py"

        for numdef in em_defs:
            assert numdef["key"] in em_defaults, (
                f"EM_NUMBER_DEFS key '{numdef['key']}' not in EM_DEFAULTS"
            )

    def test_em_number_defs_ranges_valid(self):
        """Min should be less than max, step should be positive."""
        em_defs = _parse_em_number_defs()
        em_defaults = _get_em_defaults()
        assert len(em_defs) > 0

        for numdef in em_defs:
            assert numdef["min_val"] < numdef["max_val"], (
                f"Invalid range for '{numdef['key']}': {numdef['min_val']} >= {numdef['max_val']}"
            )
            assert numdef["step"] > 0, (
                f"Invalid step for '{numdef['key']}': {numdef['step']}"
            )
            default = em_defaults[numdef["key"]]
            assert numdef["min_val"] <= default <= numdef["max_val"], (
                f"Default {default} outside range [{numdef['min_val']}, {numdef['max_val']}] for '{numdef['key']}'"
            )

    def test_em_number_defs_have_icons(self):
        """Every EM number should have an mdi icon."""
        em_defs = _parse_em_number_defs()
        assert len(em_defs) > 0

        for numdef in em_defs:
            assert numdef["icon"].startswith("mdi:"), (
                f"Invalid icon for '{numdef['key']}': {numdef['icon']}"
            )


# ═══════════════════════════════════════════════════════════════════════
# Translation Coverage for EM Entities
# ═══════════════════════════════════════════════════════════════════════


class TestEMTranslationCoverage:
    """Verify all EM entities have translations."""

    @staticmethod
    def _load_strings():
        path = Path(__file__).parent / "../custom_components/hyxi_cloud/strings.json"
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def test_em_number_keys_in_strings(self):
        """Every EM number translation_key should exist in strings.json."""
        em_defs = _parse_em_number_defs()
        assert len(em_defs) > 0

        strings = self._load_strings()
        number_translations = strings.get("entity", {}).get("number", {})

        # EM numbers use translation_key = f"em_{key}"
        for numdef in em_defs:
            tk = f"em_{numdef['key']}"
            assert tk in number_translations, (
                f"Number translation key '{tk}' missing from strings.json"
            )

    def test_em_switch_keys_in_strings(self):
        """EM switch translation keys should exist in strings.json."""
        strings = self._load_strings()
        switch_translations = strings.get("entity", {}).get("switch", {})

        for key in (
            "em_enabled",
            "em_grid_charge_allowed",
            "em_high_load_battery_assist",
            "em_night_mode",
            "em_export_limiting",
        ):
            assert key in switch_translations, (
                f"Switch translation key '{key}' missing from strings.json"
            )

    def test_em_sensor_keys_in_strings(self):
        """EM sensor translation keys should exist in strings.json."""
        strings = self._load_strings()
        sensor_translations = strings.get("entity", {}).get("sensor", {})

        em_keys = [
            "em_current_decision",
            "em_last_action",
            "em_battery_energy_available",
            "em_hours_until_sunrise",
            "em_hours_until_sunset",
            "em_p1_average",
        ]
        for key in em_keys:
            assert key in sensor_translations, (
                f"Sensor translation key '{key}' missing from strings.json"
            )

    def test_em_binary_sensor_keys_in_strings(self):
        """EM binary sensor translation keys should exist in strings.json."""
        strings = self._load_strings()
        bs_translations = strings.get("entity", {}).get("binary_sensor", {})

        for key in ("em_night_mode_active", "em_high_load_detected"):
            assert key in bs_translations, (
                f"Binary sensor translation key '{key}' missing from strings.json"
            )

    def test_em_options_step_in_strings(self):
        """Energy manager options step should exist in strings.json."""
        strings = self._load_strings()
        options_steps = strings.get("options", {}).get("step", {})
        assert "energy_manager" in options_steps, (
            "Missing 'energy_manager' options step in strings.json"
        )
        em_step = options_steps["energy_manager"]
        assert "data" in em_step
        assert "em_inverter_sn" in em_step["data"]
        assert "em_p1_entity" in em_step["data"]
        assert "em_battery_capacity_override" in em_step["data"]
        assert "em_battery_capacity_wh" in em_step["data"]
        assert "em_dry_run" in em_step["data"]


# ═══════════════════════════════════════════════════════════════════════
# Config Flow EM Step
# ═══════════════════════════════════════════════════════════════════════


class TestConfigFlowEM:
    """Test the config flow EM options step data keys."""

    def test_em_enabled_in_init_step_data(self):
        """The init step should include enable_energy_manager in its data translations."""
        strings = TestEMTranslationCoverage._load_strings()
        init_data = strings["options"]["step"]["init"]["data"]
        assert "enable_energy_manager" in init_data

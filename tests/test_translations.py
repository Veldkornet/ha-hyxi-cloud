"""Tests for HYXI Cloud translations."""

import json
import re
from pathlib import Path

import pytest


def get_translation_keys():
    """Extract used translation keys from the codebase."""
    keys: dict[str, set[str]] = {
        "sensor": set(),
        "binary_sensor": set(),
    }

    # 1. Sensors from sensor.py
    sensor_path = Path(__file__).parent / "../custom_components/hyxi_cloud/sensor.py"
    with sensor_path.open(encoding="utf-8") as f:
        content = f.read()

        # Regex to find SensorEntityDescription blocks and extract key and translation_key
        # This is a bit more robust than just finding all key= and translation_key=
        blocks = re.findall(r"SensorEntityDescription\((.*?)\)", content, re.DOTALL)
        for block in blocks:
            key_match = re.search(r'key="([^"]+)"', block)
            trans_match = re.search(r'translation_key="([^"]+)"', block)

            if trans_match:
                keys["sensor"].add(trans_match.group(1).lower())
            elif key_match:
                keys["sensor"].add(key_match.group(1).lower())

        # Find _attr_translation_key = "something" (for the class itself)
        attr_keys = re.findall(r'_attr_translation_key = "([^"]+)"', content)
        for k in attr_keys:
            keys["sensor"].add(k.lower())

    # 2. Binary Sensors from binary_sensor.py
    binary_path = (
        Path(__file__).parent / "../custom_components/hyxi_cloud/binary_sensor.py"
    )
    with binary_path.open(encoding="utf-8") as f:
        content = f.read()
        # Find _attr_translation_key = "something"
        binary_keys = re.findall(r'_attr_translation_key = "([^"]+)"', content)
        for k in binary_keys:
            keys["binary_sensor"].add(k.lower())

    return keys


def get_all_languages():
    """Get list of translation files."""
    translations_dir = (
        Path(__file__).parent / "../custom_components/hyxi_cloud/translations"
    )
    return [f.name for f in translations_dir.iterdir() if f.suffix == ".json"]


def load_translation(lang_file):
    """Load a translation JSON file."""
    path = (
        Path(__file__).parent
        / "../custom_components/hyxi_cloud/translations"
        / lang_file
    )
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("lang_file", get_all_languages())
def test_all_code_keys_are_translated(lang_file):
    """Verify that every key used in code exists in all translation files."""
    code_keys = get_translation_keys()
    translations = load_translation(lang_file)

    translated_sensors = translations.get("entity", {}).get("sensor", {}).keys()
    translated_binary_sensors = (
        translations.get("entity", {}).get("binary_sensor", {}).keys()
    )

    # Check regular sensors
    for key in code_keys["sensor"]:
        assert key in translated_sensors, (
            f"Sensor key '{key}' is missing from {lang_file}"
        )

    # Check binary sensors
    for key in code_keys["binary_sensor"]:
        assert key in translated_binary_sensors, (
            f"Binary sensor key '{key}' is missing from {lang_file}"
        )


def test_strings_json_is_synchronized():
    """Verify that strings.json is synchronized with code and en.json."""
    code_keys = get_translation_keys()

    path = Path(__file__).parent / "../custom_components/hyxi_cloud/strings.json"
    with path.open(encoding="utf-8") as f:
        strings_json = json.load(f)

    en_json = load_translation("en.json")

    # 1. Check entity section matches en.json exactly
    assert strings_json.get("entity") == en_json.get("entity"), (
        "strings.json 'entity' section does not match en.json"
    )

    # 2. Check no legacy keys in strings.json
    strings_sensors = set(strings_json.get("entity", {}).get("sensor", {}).keys())
    extra_sensors = strings_sensors - code_keys["sensor"]
    assert not extra_sensors, (
        f"strings.json contains legacy sensor keys: {extra_sensors}"
    )

    # 3. Check all code keys in strings.json
    for key in code_keys["sensor"]:
        assert key in strings_sensors, (
            f"Sensor key '{key}' is missing from strings.json"
        )


def test_language_consistency():
    """Verify that all language files have exactly the same keys as English."""
    en_json = load_translation("en.json")
    en_sensor_keys = set(en_json.get("entity", {}).get("sensor", {}).keys())

    for lang_file in get_all_languages():
        if lang_file == "en.json":
            continue

        lang_json = load_translation(lang_file)
        lang_sensor_keys = set(lang_json.get("entity", {}).get("sensor", {}).keys())

        # Check for missing keys (relative to English)
        missing_sensors = en_sensor_keys - lang_sensor_keys
        assert not missing_sensors, (
            f"{lang_file} is missing sensor translations: {missing_sensors}"
        )

        # Check for extra keys (not in English)
        extra_sensors = lang_sensor_keys - en_sensor_keys
        assert not extra_sensors, (
            f"{lang_file} has extra sensor translations: {extra_sensors}"
        )


def test_translation_values_not_empty():
    """Verify that all translation values are non-empty strings."""
    for lang_file in get_all_languages():
        translations = load_translation(lang_file)

        # Binary Sensors
        binary_translations = translations.get("entity", {}).get("binary_sensor", {})
        for key, val in binary_translations.items():
            name = val.get("name")
            assert name and name.strip(), (
                f"Empty binary sensor name for '{key}' in {lang_file}"
            )

        # Regular Sensors
        sensor_translations = translations.get("entity", {}).get("sensor", {})
        for key, val in sensor_translations.items():
            name = val.get("name")
            assert name and name.strip(), (
                f"Empty sensor name for '{key}' in {lang_file}"
            )

            # Check state enums if present
            if "state" in val:
                for state_key, state_val in val["state"].items():
                    assert state_val and state_val.strip(), (
                        f"Empty state translation for '{key}:{state_key}' in {lang_file}"
                    )


def test_non_english_translations_are_unique():
    """Verify non-English translations aren't just copies of English (where possible)."""
    en_json = load_translation("en.json")
    en_sensors = en_json.get("entity", {}).get("sensor", {})

    # We only check sensors that have clear translations in other languages
    # Technical terms like "PV1", "Wi-Fi", "AK/SK" might be identical
    technical_keys = {
        "pv1v",
        "pv2v",
        "pv1i",
        "pv2i",
        "vbus",
        "ph1v",
        "ph2v",
        "ph3v",
        "wifiver",
    }

    for lang_file in get_all_languages():
        if lang_file == "en.json":
            continue

        lang_json = load_translation(lang_file)
        lang_sensors = lang_json.get("entity", {}).get("sensor", {})

        identical_keys = []
        for key, val in lang_sensors.items():
            if key in technical_keys:
                continue

            en_val = en_sensors.get(key, {}).get("name")
            lang_val = val.get("name")

            if en_val == lang_val and en_val:
                identical_keys.append(key)

        # We allow a few identical ones, but if more than 20% are identical, it's a lazy copy
        if len(lang_sensors) > 0:
            ratio = len(identical_keys) / len(lang_sensors)
            assert ratio < 0.5, (
                f"{lang_file} seems to be a copy of English. "
                f"{len(identical_keys)} keys have identical values: {identical_keys}"
            )


def test_no_extra_keys_in_english():
    """Verify that en.json doesn't contain legacy keys not found in code."""
    code_keys = get_translation_keys()
    en_json = load_translation("en.json")

    en_sensor_keys = set(en_json.get("entity", {}).get("sensor", {}).keys())
    en_binary_keys = set(en_json.get("entity", {}).get("binary_sensor", {}).keys())

    # Check for legacy sensors
    extra_sensors = en_sensor_keys - code_keys["sensor"]
    assert not extra_sensors, (
        f"en.json contains legacy sensor keys not in code: {extra_sensors}"
    )

    # Check for legacy binary sensors
    extra_binary = en_binary_keys - code_keys["binary_sensor"]
    assert not extra_binary, (
        f"en.json contains legacy binary sensor keys not in code: {extra_binary}"
    )

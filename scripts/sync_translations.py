"""Sync translation keys from en.json to all other language files.

Adds any keys present in en.json but missing from a language file,
preserving existing translations. Run after adding new strings to en.json.
"""

import json
import pathlib


def sync_keys(source_dict, target_dict):
    """Recursively add missing keys from source_dict to target_dict."""
    updated = False
    for k, v in source_dict.items():
        if k not in target_dict:
            target_dict[k] = v
            updated = True
        elif isinstance(v, dict) and isinstance(target_dict[k], dict):
            if sync_keys(v, target_dict[k]):
                updated = True
    return updated


def main():
    """Sync translation keys from en.json to all other language files."""
    translations_dir = (
        pathlib.Path(__file__).parent.parent
        / "custom_components"
        / "hyxi_cloud"
        / "translations"
    )
    en_file = translations_dir / "en.json"

    with en_file.open(encoding="utf-8") as f:
        en_data = json.load(f)

    for path in translations_dir.glob("*.json"):
        if path.name == "en.json":
            continue

        with path.open(encoding="utf-8") as f:
            lang_data = json.load(f)

        if sync_keys(en_data, lang_data):
            print(f"Syncing keys to {path.name}")
            with path.open("w", encoding="utf-8") as f:
                json.dump(lang_data, f, indent=2, ensure_ascii=False)
                f.write("\n")


if __name__ == "__main__":
    main()

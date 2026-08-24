#!/usr/bin/env python3
"""Sync generated version/dependency fields from their sources of truth.

Sources of truth:
- manifest.json["version"]       -> propagated into pyproject.toml's version
- pyproject.toml["dependencies"] -> propagated into manifest.json["requirements"]

Rewrites either file in place if it drifts, mirroring the auto-fix
convention of the other pre-commit hooks (e.g. ruff --fix). Edits are
surgical text replacements rather than a full JSON/TOML re-serialization,
so unrelated formatting in either file is left untouched.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
MANIFEST_PATH = ROOT / "custom_components" / "hyxi_cloud" / "manifest.json"
PYPROJECT_PATH = ROOT / "pyproject.toml"


def _scan_bracketed_list(text: str, start_pattern: str, label: str) -> tuple[int, int]:
    """Return (body_start, body_end) for the list opened by `start_pattern`.

    A non-greedy regex to the first "]" is wrong here: a requirement may
    carry its own brackets, as "modbus-connection[tmodbus]>=4.8.1" does, and
    the match would stop inside it -- silently truncating the list there.
    Every "[" and "]" still comes in a matched pair regardless of whether
    it's structural or embedded in a requirement string, so counting depth
    across all of them finds the real closing bracket either way.
    """
    start = re.search(start_pattern, text)
    if not start:
        print(f"Error: could not find {label}")
        sys.exit(1)

    depth, index = 1, start.end()
    while index < len(text) and depth:
        depth += {"[": 1, "]": -1}.get(text[index], 0)
        index += 1
    if depth:
        print(f"Error: unterminated {label}")
        sys.exit(1)

    return start.end(), index - 1


def _extract_dependencies(pyproject_text: str) -> list[str]:
    """Return [project].dependencies, tolerating extras in the requirement."""
    body_start, body_end = _scan_bracketed_list(
        pyproject_text,
        r"(?m)^dependencies\s*=\s*\[",
        "[project].dependencies in pyproject.toml",
    )
    body = pyproject_text[body_start:body_end]
    # Requirements are quoted; comments in the list are not.
    return re.findall(r'"([^"]+)"', re.sub(r"(?m)#.*$", "", body))


def main() -> None:
    """Sync manifest.json requirements and pyproject.toml version in place."""
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    pyproject_text = PYPROJECT_PATH.read_text(encoding="utf-8")

    dependencies = _extract_dependencies(pyproject_text)

    version_match = re.search(r'(?m)^version\s*=\s*"(.*?)"', pyproject_text)
    if not version_match:
        print("Error: could not find version field in pyproject.toml")
        sys.exit(1)

    changed = False

    if manifest.get("requirements") != dependencies:
        body_start, body_end = _scan_bracketed_list(
            manifest_text,
            r'"requirements":\s*\[',
            '"requirements" array in manifest.json',
        )
        items = ",\n".join(f'    "{dep}"' for dep in dependencies)
        manifest_text = (
            f"{manifest_text[:body_start]}\n{items}\n  {manifest_text[body_end:]}"
        )
        changed = True

    if version_match.group(1) != manifest["version"]:
        pyproject_text = (
            pyproject_text[: version_match.start(1)]
            + manifest["version"]
            + pyproject_text[version_match.end(1) :]
        )
        changed = True

    if not changed:
        print("Version and dependency fields already in sync.")
        return

    MANIFEST_PATH.write_text(manifest_text, encoding="utf-8")
    PYPROJECT_PATH.write_text(pyproject_text, encoding="utf-8")
    print("Synced manifest.json requirements / pyproject.toml version.")
    sys.exit(1)


if __name__ == "__main__":
    main()

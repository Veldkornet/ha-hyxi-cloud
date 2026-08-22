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


def _extract_dependencies(pyproject_text: str) -> list[str]:
    """Return [project].dependencies, tolerating extras in the requirement.

    A non-greedy regex to the first "]" is wrong here: a requirement may
    carry its own brackets, as "modbus-connection[tmodbus]>=4.8.1" does, and
    the match would stop inside it -- silently dropping that requirement and
    every one after it, then reporting the manifest as already in sync.
    Scanning for the bracket that actually closes the list avoids that.
    """
    start = re.search(r"(?m)^dependencies\s*=\s*\[", pyproject_text)
    if not start:
        print("Error: could not find [project].dependencies in pyproject.toml")
        sys.exit(1)

    depth, index = 1, start.end()
    while index < len(pyproject_text) and depth:
        depth += {"[": 1, "]": -1}.get(pyproject_text[index], 0)
        index += 1
    if depth:
        print("Error: unterminated [project].dependencies list")
        sys.exit(1)

    body = pyproject_text[start.end() : index - 1]
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
        requirements_match = re.search(
            r'"requirements":\s*\[.*?\]', manifest_text, re.DOTALL
        )
        if not requirements_match:
            print('Error: could not find "requirements" array in manifest.json')
            sys.exit(1)
        items = ",\n".join(f'    "{dep}"' for dep in dependencies)
        manifest_text = (
            manifest_text[: requirements_match.start()]
            + f'"requirements": [\n{items}\n  ]'
            + manifest_text[requirements_match.end() :]
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

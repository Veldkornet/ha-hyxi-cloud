#!/usr/bin/env python3
"""Check that all version strings and dependency version requirements are in sync."""

import json
import re
import sys
from pathlib import Path


def main() -> None:
    """Verify integration versions and dependency declarations are synchronized."""
    root = Path(__file__).parent.parent

    # 1. Read manifest.json
    manifest_path = root / "custom_components" / "hyxi_cloud" / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: manifest.json not found at {manifest_path}")
        sys.exit(1)

    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    manifest_version = manifest.get("version")

    # Find hyxi-cloud-api version in manifest requirements
    manifest_api_version = None
    for req in manifest.get("requirements", []):
        if "hyxi-cloud-api" in req:
            manifest_api_version = req.strip()
            break

    # 2. Read pyproject.toml
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.exists():
        print(f"Error: pyproject.toml not found at {pyproject_path}")
        sys.exit(1)

    with pyproject_path.open("r", encoding="utf-8") as f:
        pyproject_content = f.read()

    # Extract version
    pyproject_version_match = re.search(
        r'^version\s*=\s*"(.*?)"', pyproject_content, re.MULTILINE
    )
    pyproject_version = (
        pyproject_version_match.group(1) if pyproject_version_match else None
    )

    # Extract hyxi-cloud-api dependency
    pyproject_api_match = re.search(r'"(hyxi-cloud-api[>=~<]*.*?)"', pyproject_content)
    pyproject_api_version = (
        pyproject_api_match.group(1) if pyproject_api_match else None
    )

    # 3. Read const.py
    const_path = root / "custom_components" / "hyxi_cloud" / "const.py"
    if not const_path.exists():
        print(f"Error: const.py not found at {const_path}")
        sys.exit(1)

    with const_path.open("r", encoding="utf-8") as f:
        const_content = f.read()

    # Extract VERSION
    const_version_match = re.search(
        r'^VERSION\s*=\s*"(.*?)"', const_content, re.MULTILINE
    )
    const_version = const_version_match.group(1) if const_version_match else None

    # 4. Read requirements.txt
    req_path = root / "requirements.txt"
    req_api_version = None
    if req_path.exists():
        with req_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("hyxi-cloud-api"):
                    req_api_version = line.strip()
                    break

    # 5. Read requirements_test.txt
    req_test_path = root / "requirements_test.txt"
    req_test_api_version = None
    if req_test_path.exists():
        with req_test_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("hyxi-cloud-api"):
                    req_test_api_version = line.strip()
                    break

    errors = []

    # Check integration versions
    integration_versions = {
        "manifest.json": manifest_version,
        "pyproject.toml": pyproject_version,
        "const.py": const_version,
    }

    if len(set(integration_versions.values())) > 1:
        details = "\n".join(f"  {k}: {v}" for k, v in integration_versions.items())
        errors.append(f"Integration version mismatch:\n{details}")

    # Check hyxi-cloud-api dependency versions
    api_versions = {
        "manifest.json": manifest_api_version,
        "pyproject.toml": pyproject_api_version,
        "requirements.txt": req_api_version,
        "requirements_test.txt": req_test_api_version,
    }

    # Clean version format comparison
    non_null_versions = {k: v for k, v in api_versions.items() if v is not None}
    if len(set(non_null_versions.values())) > 1:
        details = "\n".join(f"  {k}: {v}" for k, v in non_null_versions.items())
        errors.append(f"hyxi-cloud-api dependency version mismatch:\n{details}")

    if errors:
        for err in errors:
            print(err)
        sys.exit(1)

    print("All integration and dependency versions are in sync!")
    sys.exit(0)


if __name__ == "__main__":
    main()

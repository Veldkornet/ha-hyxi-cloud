"""Check our Modbus dependencies against what Core's `modbus` integration pins.

Reads Core's shipped manifest from the installed homeassistant distribution
(the version uv.lock resolves) without importing homeassistant, which the
unit tests replace with stubs.
"""

import json
import tomllib
from importlib.metadata import distribution
from itertools import chain
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).parent.parent
PYPROJECT_PATH = ROOT / "pyproject.toml"
MANIFEST_PATH = ROOT / "custom_components" / "hyxi_cloud" / "manifest.json"


def _matching(lines: list[str], package: str) -> list[Requirement]:
    """Return the requirements in `lines` that name `package`."""
    return [
        req for req in map(Requirement, lines) if canonicalize_name(req.name) == package
    ]


def _manifest_requirements(text: str) -> list[str]:
    """Return the `requirements` list from a manifest.json's text."""
    return json.loads(text)["requirements"]


def _core_pin(package: str) -> str:
    """Return the exact version Core's `modbus` manifest pins for `package`."""
    manifest = distribution("homeassistant").locate_file(
        "homeassistant/components/modbus/manifest.json"
    )
    matches = _matching(
        _manifest_requirements(manifest.read_text(encoding="utf-8")), package
    )
    assert matches, f"Core's modbus manifest no longer lists {package}"
    not_exact = f"Core's modbus manifest no longer pins {package} exactly: {matches[0]}"
    specs = list(matches[0].specifier)
    assert len(specs) == 1, not_exact
    assert specs[0].operator == "==", not_exact
    return specs[0].version


def _declared(package: str) -> list[Requirement]:
    """Return every requirement on `package` in pyproject.toml, extras included."""
    project = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]
    lines = [
        *project["dependencies"],
        *chain.from_iterable(project["optional-dependencies"].values()),
    ]
    return _matching(lines, package)


def test_pymodbus_pinned_to_core() -> None:
    """pymodbus is declared once, pinned to exactly Core's version."""
    declared = _declared("pymodbus")
    core = _core_pin("pymodbus")

    assert len(declared) == 1, (
        f"pymodbus is declared {len(declared)} times in pyproject.toml; declare it "
        "once (in the modbus extra) so Dependabot and uv can't disagree"
    )
    assert str(declared[0].specifier) == f"=={core}", (
        f"Core's modbus integration now pins pymodbus=={core}; set the modbus "
        f"extra in pyproject.toml to that and run `uv lock`"
    )


@pytest.mark.parametrize("package", ["modbus-connection", "tmodbus"])
def test_floor_satisfied_by_core(package: str) -> None:
    """Core's pin satisfies every requirement we declare or ship.

    Our manifest.json is what Home Assistant installs from; pyproject.toml is
    what the dev and test environments resolve.
    """
    core = _core_pin(package)
    declared = _declared(package)
    shipped = _matching(
        _manifest_requirements(MANIFEST_PATH.read_text(encoding="utf-8")), package
    )
    assert declared, f"pyproject.toml no longer declares {package}"
    assert shipped, f"manifest.json no longer requires {package}"
    for req in (*declared, *shipped):
        assert req.specifier.contains(core), (
            f"Core's modbus integration pins {package}=={core}, which doesn't "
            f"satisfy our requirement {req}; Home Assistant would install "
            "a second version over Core's"
        )

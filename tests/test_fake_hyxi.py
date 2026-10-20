"""Smoke test that tools/fake_hyxi.py builds its device on the pinned pymodbus.

The simulator only runs by hand, so without this a pymodbus pin change (see
tests/test_core_pins.py) could break it unnoticed.
"""

import argparse

import pytest

from tools import fake_hyxi


@pytest.mark.parametrize("profile", sorted(fake_hyxi.PROFILES))
def test_builds_device_for_profile(profile: str) -> None:
    """Every built-in profile, full 16-bit words included, becomes a SimDevice."""
    args = argparse.Namespace(snapshot=None, profile=profile, unit=1)
    device = fake_hyxi.build_device(args)

    assert device.id == 1

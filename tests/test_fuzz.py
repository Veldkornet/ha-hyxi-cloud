"""Fuzz testing for HYXI sensor logic."""

import math
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

try:
    from hypothesis import given
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False


# ==========================================
# 1. THE BULLETPROOF MOCK
# ==========================================
class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, context=None, **kwargs):  # pylint: disable=unused-argument
        self.coordinator = coordinator

    def _handle_coordinator_update(self) -> None:
        pass


class FakeSensorEntity(FakeBase):
    @property
    def native_value(self):
        return getattr(self, "_attr_native_value", None)


class FakeRestoreEntity(FakeBase):
    async def async_added_to_hass(self):
        pass


mock_ha = sys.modules.get("homeassistant")
if mock_ha is None:
    mock_ha = MagicMock()
    mock_ha.callback = lambda func: func
    mock_ha.__path__ = []
    sys.modules["homeassistant"] = mock_ha

if "homeassistant.components" not in sys.modules:
    sys.modules["homeassistant.components"] = MagicMock()

if "homeassistant.components.sensor" not in sys.modules:
    sys.modules["homeassistant.components.sensor"] = MagicMock()
sensor_mock: Any = sys.modules["homeassistant.components.sensor"]
sensor_mock.SensorEntity = FakeSensorEntity

if "homeassistant.helpers" not in sys.modules:
    sys.modules["homeassistant.helpers"] = mock_ha

if "homeassistant.helpers.restore_state" not in sys.modules:
    sys.modules["homeassistant.helpers.restore_state"] = MagicMock()
restore_mock: Any = sys.modules["homeassistant.helpers.restore_state"]
restore_mock.RestoreEntity = FakeRestoreEntity

if "homeassistant.helpers.update_coordinator" not in sys.modules:
    sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
coord_mock: Any = sys.modules["homeassistant.helpers.update_coordinator"]
coord_mock.CoordinatorEntity = FakeCoordinatorEntity

if "homeassistant.util" not in sys.modules:
    sys.modules["homeassistant.util"] = mock_ha


# Now it's safe to import the sensor
# pylint: disable-next=wrong-import-position
from custom_components.hyxi_cloud.sensor import HyxiSensor

# ==========================================
# 2. THE FUZZ TEST
# ==========================================


if HAS_HYPOTHESIS:

    @given(new_val=st.floats(allow_nan=True, allow_infinity=True))
    def test_fuzz_sensor_anti_dip_logic(new_val):
        """
        Fuzz the sensor's native_value property.
        This throws extreme floats, NaNs, and infinities to ensure it never crashes.
        """
        # 1. Setup baseline
        baseline_value = 2742.0

        coordinator = MagicMock()
        coordinator.data = {"SN123": {"metrics": {"totalE": baseline_value}}}

        description = MagicMock()
        description.key = "totalE"
        description.native_unit_of_measurement = "kWh"
        description.state_class = "total_increasing"

        # Initialize sensor
        sensor = HyxiSensor(coordinator, "SN123", description)
        sensor.hass = None

        # Verify the baseline initialized correctly
        assert sensor.native_value == baseline_value

        # 2. Inject the fuzzed/randomized value from Hypothesis
        coordinator.data["SN123"]["metrics"]["totalE"] = new_val
        sensor._handle_coordinator_update()

        # 3. Trigger the property getter
        result = None
        try:
            result = sensor.native_value
        except Exception as e:  # pylint: disable=broad-exception-caught
            pytest.fail(
                f"Sensor crashed when processing the value {new_val}. Error: {e}"
            )

        # 4. Check Invariants (The rules that must ALWAYS be true)

        # Invariant A: It should return a number or None
        assert result is None or isinstance(result, (float, int))

        # Invariant B: If it's a valid number, it shouldn't drop below the baseline
        # (unless your logic intentionally resets to 0 sometimes)
        if isinstance(result, (float, int)) and not isinstance(new_val, complex):
            # We handle math.isnan safely just in case it slipped through
            if not math.isnan(result):
                assert result >= baseline_value or (-0.1 <= result <= 0.1)
else:

    def test_fuzz_sensor_anti_dip_logic_skipped():
        pytest.skip("hypothesis not installed")

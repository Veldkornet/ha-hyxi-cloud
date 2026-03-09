"""Fuzz testing for HYXI sensor logic."""

import math
import sys
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


class FakeSensorEntity(FakeBase):
    pass


mock_ha = MagicMock()
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
mock_sensor = MagicMock()
mock_sensor.SensorEntity = FakeSensorEntity
mock_sensor.SensorStateClass = MagicMock()
mock_sensor.SensorDeviceClass = MagicMock()
sys.modules["homeassistant.components.sensor"] = mock_sensor
mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = FakeCoordinatorEntity
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator
sys.modules["homeassistant.util"] = mock_ha

# Now it's safe to import the sensor
# pylint: disable-next=wrong-import-position
from custom_components.hyxi_cloud.sensor import HyxiSensor  # noqa: E402

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

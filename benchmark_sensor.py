import sys
from unittest.mock import MagicMock

# Mock Home Assistant before any imports
mock_ha = MagicMock()
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.components.sensor"] = mock_ha
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.update_coordinator"] = mock_ha
sys.modules["homeassistant.util"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha
sys.modules["hyxi_cloud_api"] = mock_ha

# Now we can mock the specific classes and constants
class FakeBase:
    pass

class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator

class FakeSensorEntity(FakeBase):
    pass

mock_ha.SensorEntity = FakeSensorEntity
mock_ha.CoordinatorEntity = FakeCoordinatorEntity

import timeit
from custom_components.hyxi_cloud.sensor import HyxiSensor

def benchmark():
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"batSoc": "85.5", "other": "10.0"}}}

    # Case 1: Key is in the list
    description_in = MagicMock()
    description_in.key = "batSoc"
    description_in.native_unit_of_measurement = None # Avoid heavy block
    description_in.state_class = "measurement"
    sensor_in = HyxiSensor(coordinator, "SN123", description_in)
    sensor_in.hass = None

    # Case 2: Key is NOT in the list
    description_out = MagicMock()
    description_out.key = "other"
    description_out.native_unit_of_measurement = None # Avoid heavy block
    description_out.state_class = "measurement"
    sensor_out = HyxiSensor(coordinator, "SN123", description_out)
    sensor_out.hass = None

    iterations = 1000000

    print("Benchmarking key IN list...")
    t_in = timeit.timeit(lambda: sensor_in.native_value, number=iterations)
    print(f"Executed {iterations} times in {t_in:.4f} seconds")
    print(f"Average time: {t_in/iterations*1e6:.4f} microseconds")

    print("\\nBenchmarking key NOT IN list...")
    t_out = timeit.timeit(lambda: sensor_out.native_value, number=iterations)
    print(f"Executed {iterations} times in {t_out:.4f} seconds")
    print(f"Average time: {t_out/iterations*1e6:.4f} microseconds")

if __name__ == "__main__":
    benchmark()

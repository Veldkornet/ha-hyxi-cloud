"""Benchmark for HyxiSensor.device_info property."""

# pylint: disable=wrong-import-position

import sys
import time
from unittest.mock import MagicMock


# Simple mock classes
class MockCoordinatorEntity:
    def __init__(self, coordinator, *args, **kwargs):
        self.coordinator = coordinator


class MockSensorEntity:
    def __init__(self, *args, **kwargs):
        pass


class MockRestoreEntity:
    def __init__(self, *args, **kwargs):
        pass


# Mock HA and other dependencies
mock_ha = MagicMock()
mock_ha.__name__ = "homeassistant"
mock_ha.__path__ = []
mock_ha.__spec__ = None
mock_ha.callback = lambda func: func
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha

mock_api = MagicMock()
mock_api.__name__ = "hyxi_cloud_api"
mock_api.__version__ = "1.0.4"
mock_api.__spec__ = None
sys.modules["hyxi_cloud_api"] = mock_api

mock_sensor = MagicMock()
mock_sensor.SensorEntity = MockSensorEntity
mock_sensor.SensorEntityDescription = MagicMock
mock_sensor.__spec__ = None
sys.modules["homeassistant.components.sensor"] = mock_sensor

mock_coordinator = MagicMock()
mock_coordinator.CoordinatorEntity = MockCoordinatorEntity
mock_coordinator.__spec__ = None
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.restore_state"] = MagicMock()
sys.modules["homeassistant.helpers.restore_state"].RestoreEntity = MockRestoreEntity
sys.modules["homeassistant.helpers.update_coordinator"] = mock_coordinator
sys.modules["homeassistant.helpers.aiohttp_client"] = MagicMock()
sys.modules["homeassistant.util"] = mock_ha
sys.modules["aiohttp"] = MagicMock()

import hyxi_cloud.const as const_mod  # noqa: E402
import hyxi_cloud.sensor as sensor_mod  # noqa: E402

# Wire up real const.py functions
sensor_mod.normalize_device_type = const_mod.normalize_device_type
sensor_mod.get_raw_device_code = const_mod.get_raw_device_code
sensor_mod.get_software_version = const_mod.get_software_version
sensor_mod.mask_sn = const_mod.mask_sn
sensor_mod.MANUFACTURER = const_mod.MANUFACTURER
sensor_mod.DOMAIN = const_mod.DOMAIN


def benchmark():
    coordinator = MagicMock()
    dev_data = {
        "device_type_code": "COLLECTOR",
        "sw_version": "V1.0.0",
        "hw_version": "H1.0.0",
        "device_name": "Test Collector",
        "model": "Model X",
        "metrics": {"wifiVer": "W1.2.3"},
        "_sw_version_cached": "V1.0.0 / W1.2.3",  # Simulation of cached version
    }
    coordinator.data = {"SN123": dev_data}

    description = MagicMock()
    description.key = "signalVal"
    description.translation_key = None

    sensor = sensor_mod.HyxiSensor(coordinator, "SN123", description)

    iterations = 100000
    # Warm up
    for _ in range(100):
        _ = sensor.device_info

    start_time = time.perf_counter()
    for _ in range(iterations):
        _ = sensor.device_info
    end_time = time.perf_counter()

    total_time = end_time - start_time
    print(f"Total time for {iterations} iterations: {total_time:.4f} seconds")
    print(f"Average time per call: {total_time / iterations * 1e6:.4f} microseconds")


if __name__ == "__main__":
    benchmark()

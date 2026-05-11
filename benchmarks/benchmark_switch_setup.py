import os
import sys
import timeit
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

mock_ha = MagicMock()
mock_ha.__name__ = "mock_ha"
mock_ha.__path__ = []
mock_ha.__spec__ = None

sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.components"] = mock_ha
sys.modules["homeassistant.components.switch"] = mock_ha
sys.modules["homeassistant.config_entries"] = mock_ha
sys.modules["homeassistant.core"] = mock_ha
sys.modules["homeassistant.helpers"] = mock_ha
sys.modules["homeassistant.helpers.entity_platform"] = mock_ha
sys.modules["homeassistant.helpers.update_coordinator"] = mock_ha
sys.modules["homeassistant.exceptions"] = mock_ha
sys.modules["homeassistant.const"] = mock_ha
sys.modules["homeassistant.helpers.aiohttp_client"] = mock_ha
sys.modules["homeassistant.util"] = mock_ha
sys.modules["homeassistant.helpers.device_registry"] = mock_ha

mock_api = MagicMock()
mock_api.__version__ = "1.0.4"
mock_api.__spec__ = None
sys.modules["hyxi_cloud_api"] = mock_api


class FakeBase:
    pass


class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator


class FakeSwitchEntity(FakeBase):
    pass


mock_ha.CoordinatorEntity = FakeCoordinatorEntity
mock_ha.SwitchEntity = FakeSwitchEntity

# Ensure aiohttp mock
mock_aiohttp = MagicMock()
mock_aiohttp.__name__ = "aiohttp"
mock_aiohttp.__path__ = []
mock_aiohttp.__spec__ = None
sys.modules["aiohttp"] = mock_aiohttp

from custom_components.hyxi_cloud.const import (
    detect_phase_type,
    get_raw_device_code,
    normalize_device_type,
)
from custom_components.hyxi_cloud.switch import (
    HyxiFrequencyControlSwitch,
    HyxiMicroPowerSwitch,
)


def original_logic(coordinator):
    entities = []
    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if device_type not in ("hybrid_inverter", "all_in_one"):
            continue
        phase = detect_phase_type(dev_data)
        if phase == "single_phase":
            entities.append(HyxiFrequencyControlSwitch(coordinator, sn, dev_data))

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if device_type == "micro_inverter":
            entities.append(HyxiMicroPowerSwitch(coordinator, sn, dev_data))
    return entities


def optimized_logic(coordinator):
    entities = []
    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if device_type in ("hybrid_inverter", "all_in_one"):
            phase = detect_phase_type(dev_data)
            if phase == "single_phase":
                entities.append(HyxiFrequencyControlSwitch(coordinator, sn, dev_data))
        elif device_type == "micro_inverter":
            entities.append(HyxiMicroPowerSwitch(coordinator, sn, dev_data))
    return entities


def benchmark():
    coordinator = MagicMock()
    # Let's create some dummy data
    data = {}
    for i in range(100):
        # deviceCode: "5" -> micro_inverter
        # deviceCode: "1" -> hybrid_inverter
        data[f"hybrid_single_{i}"] = {
            "deviceCode": "1",
            "metrics": {"phase": "single"},
        }  # hybrid single
        data[f"hybrid_three_{i}"] = {
            "deviceCode": "1",
            "metrics": {"phase": "three"},
        }  # hybrid three
        data[f"micro_{i}"] = {"deviceCode": "5", "metrics": {}}  # microinverter
        data[f"unknown_{i}"] = {"deviceCode": "99", "metrics": {}}  # unknown

    coordinator.data = data

    print("Testing original logic...")
    t_orig = timeit.timeit(lambda: original_logic(coordinator), number=1000)
    print(f"Original logic: {t_orig:.4f} seconds")

    print("Testing optimized logic...")
    t_opt = timeit.timeit(lambda: optimized_logic(coordinator), number=1000)
    print(f"Optimized logic: {t_opt:.4f} seconds")
    print(f"Improvement: {(t_orig - t_opt) / t_orig * 100:.2f}%")


if __name__ == "__main__":
    benchmark()

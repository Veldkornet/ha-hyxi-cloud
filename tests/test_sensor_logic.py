import sys
from unittest.mock import MagicMock

# 1. THE BULLETPROOF MOCK
class FakeBase:
    pass

class FakeCoordinatorEntity(FakeBase):
    def __init__(self, coordinator, context=None, **kwargs):
        self.coordinator = coordinator

class FakeSensorEntity(FakeBase):
    pass

# 2. INJECT THEM INTO PYTHON'S BRAIN
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

# 3. NOW WE IMPORT THE LOGIC
from custom_components.hyxi_cloud.sensor import HyxiSensor

def test_energy_sensor_anti_dip():
    """Verify that 2731.90 -> 2726.30 is blocked, but 2731.90 -> 0.1 is a reset."""
    
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"totalE": 2731.90}}}
    
    description = MagicMock()
    description.key = "totalE"
    # 👇 THESE ARE THE MISSING LINES I FORGOT! 👇
    description.native_unit_of_measurement = "kWh"
    description.state_class = "total_increasing" 
    
    sensor = HyxiSensor(coordinator, "SN123", description)
    sensor.hass = None

    # --- TEST 1: Initial state ---
    assert sensor.native_value == 2731.90

    # --- TEST 2: The Glitch ---
    coordinator.data["SN123"]["metrics"]["totalE"] = 2726.30
    assert sensor.native_value == 2731.90

    # --- TEST 3: The Midnight Reset ---
    coordinator.data["SN123"]["metrics"]["totalE"] = 0.5
    assert sensor.native_value == 0.5
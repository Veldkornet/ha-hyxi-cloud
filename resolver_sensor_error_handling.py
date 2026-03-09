with open("tests/test_sensor_error_handling.py", "w") as f:
    f.write('''import pytest
from unittest.mock import MagicMock

# Create a local base_sensor fixture for this test file
@pytest.fixture
def base_sensor(monkeypatch):
    """Fixture to create a battery sensor without global sys.modules changes."""
    coordinator = MagicMock()
    coordinator.data = {"SN123": {"metrics": {"batSoc": "100"}}}
    description = MagicMock()
    description.key = "batSoc"
    description.native_unit_of_measurement = "%"
    description.state_class = "measurement"

    # We mock sensor components using mock.patch inside a fixture or directly in the test logic.
    # Actually, we can just use the fixture from test_sensor_logic if we want.
    # But since it's just tests, let's just create it directly with basic mocks.
    # A cleaner approach is to use the existing logic in test_sensor_logic.py
    # Since we are already in the branch, we can just append this test to `test_sensor_logic.py`.
    pass
''')

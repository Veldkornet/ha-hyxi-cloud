with open("tests/test_sensor_logic.py", "r") as f:
    content = f.read()

# remove MockDesc as it's not used
content = content.replace("""    # We create a dummy description
    class MockDesc:
        def __init__(self):
            self.key = "test"
            self.translation_key = "test"
            self.native_unit_of_measurement = None

""", "")

with open("tests/test_sensor_logic.py", "w") as f:
    f.write(content)

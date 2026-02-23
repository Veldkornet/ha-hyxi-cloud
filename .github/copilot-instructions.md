# GitHub Copilot Instructions: HYXi Cloud Home Assistant Integration

When generating code, suggesting autocompletions, or answering questions for this repository, you must strictly adhere to the Home Assistant custom integration developer standards and the specific architecture of this project.

## 1. Asynchronous Programming is Mandatory
* Home Assistant operates on an asynchronous event loop. 
* **Never** use synchronous HTTP clients like `requests`. **Always** use `aiohttp` for cloud API calls.
* **Never** use `time.sleep()`. **Always** use `asyncio.sleep()`.
* Ensure lifecycle methods (e.g., `async_setup_entry`, `async_unload_entry`) are strictly `async`.

## 2. The Coordinator Pattern
* This integration relies heavily on the `DataUpdateCoordinator` to fetch data from the HYXi API.
* Entities should inherit from `CoordinatorEntity`.
* Access device metrics via `self.coordinator.data`. Do not make independent API calls inside the Entity classes.

## 3. Entity Naming and Translation Keys
* Always set `self._attr_has_entity_name = True` in Entity classes.
* Do not hardcode `self._attr_name`. Instead, use `self._attr_translation_key` to map the entity name to the `translations/en.json` file.
* `unique_id` must be truly unique. For HYXi devices, construct it using the device serial number and the sensor key (e.g., `f"{serial_number}_{sensor_key}"`).

## 4. Logging Standards
* Do not use `print()`. 
* Use the standard Python logging module configured for Home Assistant:
  ```python
  import logging
  _LOGGER = logging.getLogger(__name__)
* Use _LOGGER.debug() extensively to log raw API payloads for easier user troubleshooting. Do not log sensitive API keys or passwords.

## 5. Code Quality and Typing
* This project enforces ruff for linting and formatting. Keep code PEP 8 compliant.
* Use strict Python type hints (-> None, : dict[str, Any], : str) wherever possible to maintain a robust codebase.
* Categorize sensors properly using SensorDeviceClass and SensorStateClass (e.g., SensorStateClass.TOTAL_INCREASING for energy yields).

---

"""Constants for the HYXi Cloud integration."""

from homeassistant.const import Platform

DOMAIN = "hyxi_cloud"
CONF_ACCESS_KEY = "access_key"
CONF_SECRET_KEY = "secret_key"
BASE_URL = "https://open.hyxicloud.com"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

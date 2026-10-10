"""Whether HYXI's cloud can reach this Home Assistant for push."""

from urllib.parse import urlparse

from homeassistant.core import HomeAssistant
from homeassistant.helpers import network


def cloud_subscription_active(hass: HomeAssistant) -> bool:
    """Whether Home Assistant Cloud is set up with an active subscription."""
    if "cloud" not in hass.config.components:
        return False
    # pylint: disable-next=consider-using-from-import
    import homeassistant.components.cloud as cloud

    return bool(cloud.async_active_subscription(hass))


def external_https_url(hass: HomeAssistant) -> str | None:
    """Home Assistant's external HTTPS base URL, if it has one; an internal
    or plain-HTTP address is no use, since HYXI's cloud can't reach it."""
    try:
        return network.get_url(hass, allow_internal=False, require_ssl=True)
    except network.NoURLAvailableError:
        return None


def public_url_available(hass: HomeAssistant) -> bool:
    """Whether HYXI's cloud could reach Home Assistant: through Home
    Assistant Cloud, or an external HTTPS URL."""
    return cloud_subscription_active(hass) or external_https_url(hass) is not None


def is_https_url(url: str) -> bool:
    """Whether url is an HTTPS URL with a host, as a custom callback URL
    must be."""
    parsed = urlparse(url)
    return parsed.scheme.lower() == "https" and bool(parsed.hostname)

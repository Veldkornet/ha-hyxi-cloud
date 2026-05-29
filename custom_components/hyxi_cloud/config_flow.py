"""Config flow for HYXI Cloud integration."""

import logging

import voluptuous as vol
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from hyxi_cloud_api import HyxiApiClient

from .const import (
    BASE_URL_DEFAULT,
    CONF_ACCESS_KEY,
    CONF_BACK_DISCOVERY,
    CONF_SECRET_KEY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _build_user_schema() -> vol.Schema:
    """Build the user/reauth schema."""
    return vol.Schema(
        {
            vol.Required(CONF_ACCESS_KEY): str,
            vol.Required(CONF_SECRET_KEY): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


class HyxiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for HYXI Cloud."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return HyxiOptionsFlowHandler(config_entry)

    def __init__(self):
        """Initialize the flow."""
        self.reauth_entry = None

    async def _validate_input(self, data):
        """Validate the user input allows us to connect."""
        session = async_get_clientsession(self.hass)

        client = HyxiApiClient(
            data[CONF_ACCESS_KEY],
            data[CONF_SECRET_KEY],
            BASE_URL_DEFAULT,
            session,
        )

        try:
            # Attempt a token refresh to verify AK/SK
            success = await client._refresh_token()
            if not success:
                return "invalid_auth"

            # Check if there are any devices/plants
            device_data = await client.get_all_device_data()
            if device_data is None:
                return "cannot_connect"

            if not device_data.get("data"):
                return "no_devices"
        except (TimeoutError, ClientError) as e:
            _LOGGER.error("Connection error during validation: %s", e)
            return "cannot_connect"

        return None

    async def async_step_user(self, user_input=None):
        """Handle the initial setup step."""
        errors = {}

        if user_input is not None:
            # Prevent duplicate entries by using the Access Key as a Unique ID
            await self.async_set_unique_id(user_input[CONF_ACCESS_KEY])
            self._abort_if_unique_id_configured()

            error = await self._validate_input(user_input)
            if not error:
                return self.async_create_entry(
                    title="HYXI Cloud",
                    data={
                        **user_input,
                        "base_url": BASE_URL_DEFAULT,
                    },
                )

            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=_build_user_schema(),
            errors=errors,
            description_placeholders={"link": BASE_URL_DEFAULT},
        )

    async def async_step_reauth(self, entry_data):
        """Trigger reauth flow when authentication fails."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Handle reauth confirmation."""
        errors = {}

        if user_input is not None:
            error = await self._validate_input(user_input)
            if not error:
                entry = self.reauth_entry
                if entry is None:
                    raise ValueError("reauth_entry is not set")
                return self.async_update_reload_and_abort(entry, data=user_input)
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_build_user_schema(),
            errors=errors,
            description_placeholders={"link": BASE_URL_DEFAULT},
        )


class HyxiOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle HYXI optional settings (The Slider)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Pull current values or defaults
        current_interval = self._config_entry.options.get("update_interval", 5)

        from .const import is_battery_control_enabled

        coordinator = None
        if hasattr(self, "hass") and self.hass:
            coordinator = self.hass.data.get(DOMAIN, {}).get(
                self._config_entry.entry_id
            )
        default_control = is_battery_control_enabled(self._config_entry, coordinator)

        options_schema = vol.Schema(
            {
                # Slider for Interval
                vol.Required("update_interval", default=current_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=60)
                ),
                # Toggle for Alarm-based discovery
                vol.Optional(
                    CONF_BACK_DISCOVERY,
                    default=self._config_entry.options.get(CONF_BACK_DISCOVERY, False),
                ): selector.BooleanSelector(),
                # Toggle for Battery Control & Protection
                vol.Optional(
                    "enable_battery_control",
                    default=default_control,
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)

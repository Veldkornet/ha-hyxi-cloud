import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HyxiApiClient
from .const import BASE_URL
from .const import CONF_ACCESS_KEY
from .const import CONF_SECRET_KEY
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Schema for User Setup and Re-auth
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCESS_KEY): str,
        vol.Required(CONF_SECRET_KEY): str,
    }
)


class HyxiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HYXi Cloud."""

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
            BASE_URL,
            session,
        )

        try:
            # Attempt a token refresh to verify AK/SK
            success = await client._refresh_token()
            if not success:
                return "invalid_auth"
        except Exception:
            _LOGGER.exception("Connection error during validation")
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
                return self.async_create_entry(title="HYXi Cloud", data=user_input)

            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"link": BASE_URL},
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
                return self.async_update_reload_and_abort(
                    self.reauth_entry, data=user_input
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"link": BASE_URL},
        )


class HyxiOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle HYXi optional settings (The Slider)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Pull current values or defaults
        current_battery = self._config_entry.options.get(
            "enable_virtual_battery", False
        )
        current_interval = self._config_entry.options.get("update_interval", 5)

        options_schema = vol.Schema(
            {
                # Slider for Interval
                vol.Required("update_interval", default=current_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=60)
                ),
                # Checkbox for Virtual Battery
                vol.Optional("enable_virtual_battery", default=current_battery): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)

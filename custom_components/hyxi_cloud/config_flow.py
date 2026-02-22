import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HyxiApiClient
from .const import BASE_URL
from .const import CONF_ACCESS_KEY
from .const import CONF_SECRET_KEY
from .const import DOMAIN


class HyxiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            # 1. Prevent duplicate entries
            await self.async_set_unique_id(user_input[CONF_ACCESS_KEY])
            self._abort_if_unique_id_configured()

            # Get the HA session and initialize the NEW async client
            session = async_get_clientsession(self.hass)
            client = HyxiApiClient(
                user_input[CONF_ACCESS_KEY],
                user_input[CONF_SECRET_KEY],
                BASE_URL,
                session,
            )

            try:
                # 2. Validate credentials directly
                success = await client._refresh_token()

                if success:
                    return self.async_create_entry(title="HYXi Cloud", data=user_input)
                else:
                    errors["base"] = "invalid_auth"

            except Exception:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_KEY): str,
                    vol.Required(CONF_SECRET_KEY): str,
                }
            ),
            errors=errors,
            description_placeholders={"link": BASE_URL},
        )

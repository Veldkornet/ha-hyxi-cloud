import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN, CONF_ACCESS_KEY, CONF_SECRET_KEY

class HyxiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            # Here you could add a quick API call to verify the keys
            return self.async_create_entry(title="HYXi Cloud", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ACCESS_KEY): str,
                vol.Required(CONF_SECRET_KEY): str,
            }),
            errors=errors,
        )
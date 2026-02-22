import voluptuous as vol
import requests
from homeassistant import config_entries
from .const import DOMAIN, CONF_ACCESS_KEY, CONF_SECRET_KEY, BASE_URL
from .api import HyxiApiClient

class HyxiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        
        if user_input is not None:
            # 1. Prevent duplicate entries
            await self.async_set_unique_id(user_input[CONF_ACCESS_KEY])
            self._abort_if_unique_id_configured()

            client = HyxiApiClient(user_input[CONF_ACCESS_KEY], user_input[CONF_SECRET_KEY], BASE_URL)
            
            try:
                # 2. Validate credentials via executor (blocking requests)
                success = await self.hass.async_add_executor_job(client._refresh_token)
                
                if success:
                    return self.async_create_entry(title="HYXi Cloud", data=user_input)
                else:
                    # This maps to "invalid_auth" in your .json files
                    errors["base"] = "invalid_auth"
                    
            except requests.exceptions.ConnectionError:
                # This maps to "cannot_connect" in your .json files
                errors["base"] = "cannot_connect"
            except Exception:
                # This maps to "unknown" in your .json files
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ACCESS_KEY): str,
                vol.Required(CONF_SECRET_KEY): str,
            }),
            errors=errors,
        )
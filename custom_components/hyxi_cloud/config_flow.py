"""Config flow for HYXI Cloud integration."""

import logging
from collections.abc import Mapping
from typing import Any

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
    CONF_EM_BATTERY_CAPACITY,
    CONF_EM_BATTERY_OVERRIDE,
    CONF_EM_DRY_RUN,
    CONF_EM_ENABLED,
    CONF_EM_FORECAST_ENTITY,
    CONF_EM_FORECAST_POWER_ENTITY,
    CONF_EM_INVERTER_SN,
    CONF_EM_LOOP_INTERVAL,
    CONF_EM_P1_ENTITY,
    CONF_ENABLE_PUSH,
    CONF_PUSH_RATE,
    CONF_PUSH_URL,
    CONF_REGION,
    CONF_SECRET_KEY,
    DEFAULT_PUSH_RATE,
    DEFAULT_REGION,
    DOMAIN,
    default_region_for_country,
    get_raw_device_code,
    normalize_device_type,
    region_for_base_url,
    resolve_base_url,
)

REGION_OPTIONS: list[selector.SelectOptionDict] = [
    {"value": "eu", "label": "Europe"},
    {"value": "na", "label": "North America"},
    {"value": "cn", "label": "China"},
]

# Values are seconds (stored as int, displayed as-is); SDK call converts to ms
PUSH_RATE_OPTIONS: list[selector.SelectOptionDict] = [
    {"value": "5", "label": "5 seconds"},
    {"value": "10", "label": "10 seconds"},
    {"value": "30", "label": "30 seconds"},
    {"value": "60", "label": "1 minute"},
    {"value": "300", "label": "5 minutes"},
]

_LOGGER = logging.getLogger(__name__)


def _build_user_schema(default_region: str = DEFAULT_REGION) -> vol.Schema:
    """Build the initial setup schema, including the server region."""
    return vol.Schema(
        {
            vol.Required(CONF_ACCESS_KEY): str,
            vol.Required(CONF_SECRET_KEY): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_REGION, default=default_region): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=REGION_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _build_em_schema(
    options: Mapping[str, Any], sn_options: list[str], current_sn: str
) -> vol.Schema:
    """Build the Energy Manager schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_EM_P1_ENTITY,
                default=options.get(CONF_EM_P1_ENTITY, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_EM_FORECAST_ENTITY,
                default=options.get(CONF_EM_FORECAST_ENTITY, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_EM_FORECAST_POWER_ENTITY,
                default=options.get(CONF_EM_FORECAST_POWER_ENTITY, ""),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_EM_INVERTER_SN, default=current_sn
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sn_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_EM_BATTERY_OVERRIDE,
                default=options.get(CONF_EM_BATTERY_OVERRIDE, False),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_EM_BATTERY_CAPACITY,
                default=options.get(CONF_EM_BATTERY_CAPACITY, 2000),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000,
                    max=50000,
                    step=100,
                    unit_of_measurement="Wh",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_EM_LOOP_INTERVAL,
                default=options.get(CONF_EM_LOOP_INTERVAL, 15),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5,
                    max=60,
                    step=1,
                    unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_EM_DRY_RUN,
                default=options.get(CONF_EM_DRY_RUN, False),
            ): selector.BooleanSelector(),
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

    async def _validate_input(self, data, base_url: str = BASE_URL_DEFAULT):
        """Validate the user input allows us to connect."""
        session = async_get_clientsession(self.hass)

        client = HyxiApiClient(
            data[CONF_ACCESS_KEY],
            data[CONF_SECRET_KEY],
            base_url,
            session,
        )

        _LOGGER.debug("Validating HYXI credentials against %s", base_url)
        try:
            # Attempt a token refresh to verify AK/SK. The client returns
            # None for network/connection failures and False for an explicit
            # credential rejection -- report them differently so a user with
            # valid keys and a flaky connection isn't told their keys are bad.
            success = await client._refresh_token()
            if success is None:
                return "cannot_connect"
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
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Unexpected error during HYXI credential validation")
            return "unknown"

        return None

    async def async_step_user(self, user_input=None):
        """Handle the initial setup step."""
        _LOGGER.debug(
            "Config flow: entering step_user (input provided=%s)",
            user_input is not None,
        )
        errors = {}
        default_region = default_region_for_country(self.hass.config.country)

        if user_input is not None:
            # Prevent duplicate entries by using the Access Key as a Unique ID
            await self.async_set_unique_id(user_input[CONF_ACCESS_KEY])
            self._abort_if_unique_id_configured()

            base_url = resolve_base_url(user_input.get(CONF_REGION))
            error = await self._validate_input(user_input, base_url)
            if not error:
                return self.async_create_entry(
                    title="HYXI Cloud",
                    data={
                        **user_input,
                        "base_url": base_url,
                    },
                )

            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=_build_user_schema(default_region),
            errors=errors,
            description_placeholders={"link": BASE_URL_DEFAULT},
        )

    async def async_step_reauth(self, entry_data):
        """Trigger reauth flow when authentication fails."""
        _LOGGER.debug(
            "Config flow: entering step_reauth for entry %s",
            self.context.get("entry_id"),
        )
        self.reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Handle reauth confirmation.

        Also lets the user correct the server region -- useful if it was
        picked wrong at install time, or the account was migrated to a
        different HYXI regional server. Defaults to the entry's current
        region so most users can just re-enter credentials unchanged.
        """
        _LOGGER.debug(
            "Config flow: entering step_reauth_confirm (input provided=%s)",
            user_input is not None,
        )
        errors = {}
        entry = self.reauth_entry
        default_region = (
            (
                entry.data.get(CONF_REGION)
                or region_for_base_url(entry.data.get("base_url"))
            )
            if entry is not None
            else DEFAULT_REGION
        )

        if user_input is not None:
            if entry is None:
                raise ValueError("reauth_entry is not set")
            base_url = resolve_base_url(user_input.get(CONF_REGION))
            error = await self._validate_input(user_input, base_url)
            if not error:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, **user_input, "base_url": base_url}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_build_user_schema(default_region),
            errors=errors,
            description_placeholders={"link": BASE_URL_DEFAULT},
        )


class HyxiOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle HYXI optional settings (The Slider)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._options: dict = {}

    async def async_step_init(self, user_input=None):
        """Manage the options form."""
        if user_input is not None:
            # Preserve all existing options, update with new values
            self._options = (
                dict(self._options)
                if self._options
                else dict(self._config_entry.options)
            )
            self._options["update_interval"] = user_input["update_interval"]
            self._options[CONF_BACK_DISCOVERY] = user_input.get(
                CONF_BACK_DISCOVERY, False
            )

            was_battery_control_enabled = self._options.get(
                "enable_battery_control", False
            )
            was_push_enabled = self._options.get(CONF_ENABLE_PUSH, False)

            if "enable_battery_control" in user_input:
                self._options["enable_battery_control"] = user_input[
                    "enable_battery_control"
                ]

            if CONF_ENABLE_PUSH in user_input:
                self._options[CONF_ENABLE_PUSH] = user_input[CONF_ENABLE_PUSH]
            if CONF_PUSH_RATE in user_input:
                # SelectSelector always returns strings; coerce back to int for SDK
                self._options[CONF_PUSH_RATE] = int(user_input[CONF_PUSH_RATE])
            if CONF_PUSH_URL in user_input:
                self._options[CONF_PUSH_URL] = user_input[CONF_PUSH_URL]

            enable_em = self._options.get(CONF_EM_ENABLED, False)
            if "enable_energy_manager" in user_input:
                enable_em = user_input["enable_energy_manager"]

            # EM requires battery control — auto-enable if user turned on EM
            if enable_em and not self._options.get("enable_battery_control"):
                self._options["enable_battery_control"] = True

            # If user just enabled battery_control, but enable_energy_manager wasn't in user_input,
            # reload the step to reveal it (only if controllable inverters exist).
            if (
                self._has_controllable_inverter()
                and self._options.get("enable_battery_control", False)
                and not was_battery_control_enabled
                and "enable_energy_manager" not in user_input
            ):
                return await self.async_step_init()

            # If user just enabled push, reload step to reveal rate/url input fields
            if (
                self._options.get(CONF_ENABLE_PUSH, False)
                and not was_push_enabled
                and CONF_PUSH_RATE not in user_input
            ):
                return await self.async_step_init()

            if enable_em:
                self._options[CONF_EM_ENABLED] = True
                return await self.async_step_energy_manager()

            # EM disabled — remove EM keys if they were previously set
            self._options.pop(CONF_EM_ENABLED, None)
            for key in (
                CONF_EM_INVERTER_SN,
                CONF_EM_P1_ENTITY,
                CONF_EM_FORECAST_ENTITY,
                CONF_EM_FORECAST_POWER_ENTITY,
                CONF_EM_BATTERY_OVERRIDE,
                CONF_EM_BATTERY_CAPACITY,
                CONF_EM_LOOP_INTERVAL,
                CONF_EM_DRY_RUN,
            ):
                self._options.pop(key, None)

            # Push disabled — remove push keys if they were previously set
            if not self._options.get(CONF_ENABLE_PUSH, False):
                self._options.pop(CONF_PUSH_RATE, None)
                self._options.pop(CONF_PUSH_URL, None)

            return self.async_create_entry(title="", data=self._options)

        # Pull current values or defaults
        options = self._options if self._options else self._config_entry.options
        current_interval = options.get("update_interval", 5)
        em_enabled = options.get(CONF_EM_ENABLED, False)
        has_controllable = self._has_controllable_inverter()

        schema_dict = {
            # Slider for Interval
            vol.Required("update_interval", default=current_interval): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=60)
            ),
            # Toggle for Alarm-based discovery
            vol.Optional(
                CONF_BACK_DISCOVERY,
                default=options.get(CONF_BACK_DISCOVERY, False),
            ): selector.BooleanSelector(),
            # Toggle for Real-Time Push
            vol.Optional(
                CONF_ENABLE_PUSH,
                default=options.get(CONF_ENABLE_PUSH, False),
            ): selector.BooleanSelector(),
        }

        # If push is enabled, show the rate and url inputs
        if options.get(CONF_ENABLE_PUSH, False):
            schema_dict[
                vol.Required(
                    CONF_PUSH_RATE,
                    # default must be str to match SelectSelector string option values
                    default=str(options.get(CONF_PUSH_RATE, DEFAULT_PUSH_RATE)),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=PUSH_RATE_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[
                vol.Optional(
                    CONF_PUSH_URL,
                    default=options.get(CONF_PUSH_URL, ""),
                )
            ] = selector.TextSelector()

        # Only show control/EM toggles if controllable inverters exist
        if has_controllable:
            battery_control_on = options.get("enable_battery_control", False)
            schema_dict[
                vol.Optional(
                    "enable_battery_control",
                    default=battery_control_on,
                )
            ] = selector.BooleanSelector()
            # EM toggle only visible when battery control is already enabled
            if battery_control_on:
                schema_dict[
                    vol.Optional("enable_energy_manager", default=em_enabled)
                ] = selector.BooleanSelector()

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))

    def _save_energy_manager_input(self, user_input: dict) -> None:
        """Save the energy manager user input."""
        self._options[CONF_EM_P1_ENTITY] = user_input[CONF_EM_P1_ENTITY]
        self._options[CONF_EM_INVERTER_SN] = user_input[CONF_EM_INVERTER_SN]
        self._options[CONF_EM_BATTERY_OVERRIDE] = user_input.get(
            CONF_EM_BATTERY_OVERRIDE, False
        )
        if user_input.get(CONF_EM_BATTERY_OVERRIDE):
            self._options[CONF_EM_BATTERY_CAPACITY] = user_input.get(
                CONF_EM_BATTERY_CAPACITY, 2000
            )
        else:
            self._options.pop(CONF_EM_BATTERY_CAPACITY, None)
        if user_input.get(CONF_EM_FORECAST_ENTITY):
            self._options[CONF_EM_FORECAST_ENTITY] = user_input[CONF_EM_FORECAST_ENTITY]
        if user_input.get(CONF_EM_FORECAST_POWER_ENTITY):
            self._options[CONF_EM_FORECAST_POWER_ENTITY] = user_input[
                CONF_EM_FORECAST_POWER_ENTITY
            ]
        self._options[CONF_EM_LOOP_INTERVAL] = user_input.get(CONF_EM_LOOP_INTERVAL, 15)
        self._options[CONF_EM_DRY_RUN] = user_input.get(CONF_EM_DRY_RUN, False)

    async def async_step_energy_manager(self, user_input=None):
        """Configure the Energy Manager -- P1 entity, forecast, inverter SN."""
        _LOGGER.debug(
            "Config flow: entering step_energy_manager (input provided=%s)",
            user_input is not None,
        )
        if user_input is not None:
            self._save_energy_manager_input(user_input)
            return self.async_create_entry(title="", data=self._options)

        # Build inverter SN options from coordinator data
        sn_options = self._get_controllable_sns()
        current_sn = self._config_entry.options.get(CONF_EM_INVERTER_SN, "")
        if not current_sn and len(sn_options) == 1:
            current_sn = sn_options[0]

        schema = _build_em_schema(self._config_entry.options, sn_options, current_sn)

        return self.async_show_form(step_id="energy_manager", data_schema=schema)

    def _get_controllable_sns(self) -> list[str]:
        """Get serial numbers of controllable inverters from coordinator data."""
        if not hasattr(self, "hass") or self.hass is None:
            return []
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)
        if not coordinator or not coordinator.data:
            return []
        sns = []
        for sn, dev_data in coordinator.data.items():
            device_type = normalize_device_type(get_raw_device_code(dev_data))
            if device_type in ("hybrid_inverter", "all_in_one"):
                sns.append(sn)
        return sns

    def _has_controllable_inverter(self) -> bool:
        """Check if any controllable inverter exists."""
        return len(self._get_controllable_sns()) > 0

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
    CONF_MODBUS_BAUDRATE,
    CONF_MODBUS_DEVICE,
    CONF_MODBUS_HOST,
    CONF_MODBUS_PORT,
    CONF_MODBUS_TYPE,
    CONF_MODBUS_UNIT,
    CONF_PUSH_RATE,
    CONF_PUSH_URL,
    CONF_REGION,
    CONF_SECRET_KEY,
    CONF_TRANSPORT,
    DEFAULT_MODBUS_BAUDRATE,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_UNIT,
    DEFAULT_PUSH_RATE,
    DEFAULT_REGION,
    DEFAULT_TRANSPORT,
    DOMAIN,
    MICRO_ESS_CONTROL_SUPPORTED,
    MODBUS_MESSAGE_SPACING,
    MODBUS_PROBE_POINTS,
    MODBUS_TIMEOUT,
    MODBUS_TYPE_SERIAL,
    MODBUS_TYPE_TCP,
    TRANSPORT_CLOUD,
    TRANSPORT_MODBUS,
    default_region_for_country,
    get_raw_device_code,
    is_modbus_entry,
    normalize_device_type,
    region_for_base_url,
    resolve_base_url,
)

TRANSPORT_OPTIONS: list[selector.SelectOptionDict] = [
    {"value": TRANSPORT_CLOUD, "label": "HYXI Cloud (online account)"},
    {"value": TRANSPORT_MODBUS, "label": "Local Modbus (RS485, no account)"},
]

MODBUS_TYPE_OPTIONS: list[selector.SelectOptionDict] = [
    {"value": MODBUS_TYPE_TCP, "label": "Modbus TCP gateway (RS485-to-Ethernet)"},
    {"value": MODBUS_TYPE_SERIAL, "label": "Serial port (USB RS485 adapter)"},
]

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


def _build_transport_schema() -> vol.Schema:
    """Build the transport chooser shown before anything else.

    A select rather than a menu, because the cloud path is what almost every
    existing user wants and a menu cannot express a default.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_TRANSPORT, default=DEFAULT_TRANSPORT
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=TRANSPORT_OPTIONS,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


def _build_modbus_type_schema() -> vol.Schema:
    """Build the Modbus connection-type chooser."""
    return vol.Schema(
        {
            vol.Required(
                CONF_MODBUS_TYPE, default=MODBUS_TYPE_TCP
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=MODBUS_TYPE_OPTIONS,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


def _build_unit_field() -> dict:
    """Build the slave-address field shared by both Modbus connection types."""
    return {
        vol.Required(
            CONF_MODBUS_UNIT, default=DEFAULT_MODBUS_UNIT
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=247, step=1, mode=selector.NumberSelectorMode.BOX
            )
        )
    }


def _build_modbus_tcp_schema() -> vol.Schema:
    """Build the schema for an RS485-to-Ethernet gateway."""
    return vol.Schema(
        {
            vol.Required(CONF_MODBUS_HOST): str,
            vol.Required(
                CONF_MODBUS_PORT, default=DEFAULT_MODBUS_PORT
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            **_build_unit_field(),
        }
    )


def _build_modbus_serial_schema() -> vol.Schema:
    """Build the schema for a directly attached USB RS485 adapter."""
    return vol.Schema(
        {
            vol.Required(CONF_MODBUS_DEVICE, default="/dev/ttyUSB0"): str,
            vol.Required(
                CONF_MODBUS_BAUDRATE, default=DEFAULT_MODBUS_BAUDRATE
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["9600", "19200", "38400", "57600", "115200"],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            **_build_unit_field(),
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
        self._modbus_type = MODBUS_TYPE_TCP

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

    async def _probe_modbus(self, params, unit_id: int) -> str | None:
        """Return an error key, or None if a device answered on the bus.

        A Modbus exception response counts as success. It means the device
        replied and simply does not carry that register, which already
        proves the wiring, framing and slave address are right -- and this
        runs before any register map is known, so a value is not required.
        """
        try:
            from modbus_connection import ModbusExceptionError, ModbusTimeoutError
            from modbus_connection.tmodbus import ModbusConnection
        except ImportError:
            _LOGGER.error("modbus-connection is not installed")
            return "modbus_unavailable"

        connection = ModbusConnection(
            params, timeout=MODBUS_TIMEOUT, message_spacing=MODBUS_MESSAGE_SPACING
        )
        unit = connection.for_unit(unit_id)
        try:
            for space, address in MODBUS_PROBE_POINTS:
                read = (
                    unit.read_input_registers
                    if space == "input"
                    else unit.read_holding_registers
                )
                try:
                    await read(address, 1)
                except ModbusExceptionError:
                    return None
                except ModbusTimeoutError:
                    continue
                return None
            return "no_device"
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Could not reach the Modbus device")
            return "cannot_connect"
        finally:
            await connection.close()

    async def _validate_modbus(self, user_input) -> tuple[str | None, dict]:
        """Validate a Modbus connection and return (error, entry data)."""
        from modbus_connection import ModbusSerialParams, ModbusTcpParams

        unit_id = int(user_input[CONF_MODBUS_UNIT])
        if self._modbus_type == MODBUS_TYPE_SERIAL:
            device = user_input[CONF_MODBUS_DEVICE]
            baudrate = int(user_input[CONF_MODBUS_BAUDRATE])
            params = ModbusSerialParams(
                device=device, baudrate=baudrate, bytesize=8, parity="N", stopbits=1
            )
            unique_id = f"{device}:{unit_id}"
            title = f"HYXI Modbus ({device})"
            data = {
                CONF_MODBUS_DEVICE: device,
                CONF_MODBUS_BAUDRATE: baudrate,
            }
        else:
            host = user_input[CONF_MODBUS_HOST]
            port = int(user_input[CONF_MODBUS_PORT])
            # RS485-to-Ethernet gateways almost always tunnel RTU frames
            # rather than speaking native Modbus TCP framing.
            params = ModbusTcpParams(host=host, port=port, framer="rtu")
            unique_id = f"{host}:{port}:{unit_id}"
            title = f"HYXI Modbus ({host})"
            data = {CONF_MODBUS_HOST: host, CONF_MODBUS_PORT: port}

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        error = await self._probe_modbus(params, unit_id)
        return error, {
            **data,
            CONF_TRANSPORT: TRANSPORT_MODBUS,
            CONF_MODBUS_TYPE: self._modbus_type,
            CONF_MODBUS_UNIT: unit_id,
            "_title": title,
        }

    async def async_step_user(self, user_input=None):
        """Choose between the cloud API and a local Modbus link."""
        _LOGGER.debug(
            "Config flow: entering step_user (input provided=%s)",
            user_input is not None,
        )
        if user_input is not None:
            if user_input[CONF_TRANSPORT] == TRANSPORT_MODBUS:
                return await self.async_step_modbus()
            return await self.async_step_cloud()

        return self.async_show_form(
            step_id="user", data_schema=_build_transport_schema()
        )

    async def async_step_modbus(self, user_input=None):
        """Choose how the RS485 bus is attached."""
        _LOGGER.debug(
            "Config flow: entering step_modbus (input provided=%s)",
            user_input is not None,
        )
        if user_input is not None:
            self._modbus_type = user_input[CONF_MODBUS_TYPE]
            if self._modbus_type == MODBUS_TYPE_SERIAL:
                return await self.async_step_modbus_serial()
            return await self.async_step_modbus_tcp()

        return self.async_show_form(
            step_id="modbus", data_schema=_build_modbus_type_schema()
        )

    async def _async_modbus_connection_step(self, step_id, schema, user_input):
        """Validate and create an entry for either Modbus connection type."""
        errors = {}
        if user_input is not None:
            error, data = await self._validate_modbus(user_input)
            if not error:
                return self.async_create_entry(title=data.pop("_title"), data=data)
            errors["base"] = error

        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    async def async_step_modbus_tcp(self, user_input=None):
        """Configure an RS485-to-Ethernet gateway."""
        return await self._async_modbus_connection_step(
            "modbus_tcp", _build_modbus_tcp_schema(), user_input
        )

    async def async_step_modbus_serial(self, user_input=None):
        """Configure a directly attached USB RS485 adapter."""
        return await self._async_modbus_connection_step(
            "modbus_serial", _build_modbus_serial_schema(), user_input
        )

    async def async_step_cloud(self, user_input=None):
        """Handle credentials for the HYXI Cloud API."""
        _LOGGER.debug(
            "Config flow: entering step_cloud (input provided=%s)",
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
                        CONF_TRANSPORT: TRANSPORT_CLOUD,
                    },
                )

            errors["base"] = error

        return self.async_show_form(
            step_id="cloud",
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
        has_em_capable = self._has_controllable_inverter()
        has_control_capable = self._has_control_capable_device()

        schema_dict = {
            # Slider for Interval
            vol.Required("update_interval", default=current_interval): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=60)
            ),
        }

        # Alarm-based discovery and real-time push are both cloud services:
        # discovery walks the account's alarm records, and push is a webhook
        # subscription registered with HYXI's servers. A point-to-point RS485
        # link has neither, so a Modbus entry never shows them.
        if not is_modbus_entry(self._config_entry):
            schema_dict[
                vol.Optional(
                    CONF_BACK_DISCOVERY,
                    default=options.get(CONF_BACK_DISCOVERY, False),
                )
            ] = selector.BooleanSelector()
            schema_dict[
                vol.Optional(
                    CONF_ENABLE_PUSH,
                    default=options.get(CONF_ENABLE_PUSH, False),
                )
            ] = selector.BooleanSelector()

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

        # Show the device control toggle for any control-capable device
        # (hybrid inverter, all-in-one; also micro_ess/HALO once
        # MICRO_ESS_CONTROL_SUPPORTED is enabled — see const.py)
        if has_control_capable:
            battery_control_on = options.get("enable_battery_control", False)
            schema_dict[
                vol.Optional(
                    "enable_battery_control",
                    default=battery_control_on,
                )
            ] = selector.BooleanSelector()
            # EM toggle only visible when battery control is enabled AND an
            # EM-eligible inverter (hybrid_inverter/all_in_one) is present
            if battery_control_on and has_em_capable:
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

    def _get_sns_by_device_type(self, allowed_types: tuple[str, ...]) -> list[str]:
        """Get serial numbers of coordinator devices whose normalized type
        is one of `allowed_types`."""
        if not hasattr(self, "hass") or self.hass is None:
            return []
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)
        if not coordinator or not coordinator.data:
            return []
        return [
            sn
            for sn, dev_data in coordinator.data.items()
            if normalize_device_type(get_raw_device_code(dev_data)) in allowed_types
        ]

    def _get_controllable_sns(self) -> list[str]:
        """Get serial numbers of EM-eligible inverters (hybrid_inverter/all_in_one)."""
        return self._get_sns_by_device_type(("hybrid_inverter", "all_in_one"))

    def _has_controllable_inverter(self) -> bool:
        """Check if any EM-eligible inverter (hybrid_inverter/all_in_one) exists."""
        return len(self._get_controllable_sns()) > 0

    def _get_control_capable_sns(self) -> list[str]:
        """Get serial numbers of any device control (not just EM) supports.

        Includes EM-eligible inverters plus, when MICRO_ESS_CONTROL_SUPPORTED
        is enabled, micro_ess/HALO devices (Power On/Off, controlId 1011).
        """
        allowed_types: tuple[str, ...] = ("hybrid_inverter", "all_in_one")
        if MICRO_ESS_CONTROL_SUPPORTED:
            allowed_types += ("micro_ess",)
        return self._get_sns_by_device_type(allowed_types)

    def _has_control_capable_device(self) -> bool:
        """Check if any control-capable device exists.

        See _get_control_capable_sns — micro_ess only counts when
        MICRO_ESS_CONTROL_SUPPORTED is enabled.
        """
        return len(self._get_control_capable_sns()) > 0

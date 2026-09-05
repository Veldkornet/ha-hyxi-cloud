"""Shared battery-control primitives.

The mode-command surface (idle / charge / discharge / self-consume), the
SOC-protection guards around it, and the paired power-number lookup live
here rather than in button.py so both the mode buttons and the
hyxi_cloud.set_battery_mode service can use them without a platform module
(and its entity classes) being pulled into the import graph.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from hyxi_cloud_api import HyxiApiClient

from .const import DOMAIN, mask_sn

_LOGGER = logging.getLogger(__name__)


def _get_power_value(hass: HomeAssistant, sn: str, direction: str) -> int:
    """Read the wattage from the paired number entity.

    Looks up the entity by unique_id via the entity registry, since HA-assigned
    entity_ids don't follow a predictable pattern.
    Falls back to 100W if the number entity has not been set yet.
    """
    unique_id = f"hyxi_{sn}_{direction}_power"
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("number", DOMAIN, unique_id)
    if entity_id is None:
        # unique_id contains unmasked sn, so we should mask it here for logs or just avoid logging unmasked unique_id
        masked_unique_id = f"hyxi_{mask_sn(sn)}_{direction}_power"
        _LOGGER.warning(
            "Power number entity (unique_id=%s) not found in registry, using 100W default",
            masked_unique_id,
        )
        return 100
    state = hass.states.get(entity_id)
    if state is not None and state.state not in ("unknown", "unavailable"):
        try:
            return int(float(state.state))
        except ValueError, TypeError, OverflowError:
            _LOGGER.debug(
                "Power entity %s has non-numeric state %r, using 100W default",
                entity_id,
                state.state,
            )
    _LOGGER.warning(
        "Power number entity %s not available, using 100W default", entity_id
    )
    return 100


def _get_protection_controller(coordinator, sn: str):
    """Return the battery protection controller for a device."""
    return getattr(coordinator, "protection_controllers", {}).get(sn)


def _note_manual_mode(coordinator, sn: str, mode: str) -> None:
    """Track the last user-sent inverter mode for battery protection telemetry."""
    if controller := _get_protection_controller(coordinator, sn):
        controller.note_manual_mode(mode)


def _block_manual_discharge_if_needed(coordinator, sn: str) -> None:
    """Reject manual discharge when SOC protection says discharge is unsafe."""
    if (
        controller := _get_protection_controller(coordinator, sn)
    ) is not None and controller.should_block_manual_discharge():
        raise HomeAssistantError(
            "Discharge blocked because battery SOC is at or below SOC Minimum"
        )


def _block_manual_charge_if_needed(coordinator, sn: str) -> None:
    """Reject manual charge when SOC protection says charging is unsafe."""
    if (
        controller := _get_protection_controller(coordinator, sn)
    ) is not None and controller.should_block_manual_charge():
        raise HomeAssistantError(
            "Charge blocked because battery SOC is at or above SOC Maximum"
        )


def _block_manual_peak_shaving_if_needed(coordinator, sn: str, option: str) -> None:
    """Reject unsafe peak-shaving actions when SOC protection is active."""
    controller = _get_protection_controller(coordinator, sn)
    if controller is None:
        return

    if option == "discharge" and controller.should_block_manual_discharge():
        raise HomeAssistantError(
            "Peak shaving discharge blocked because battery SOC is at or below SOC Minimum"
        )
    if option == "charge" and controller.should_block_manual_charge():
        raise HomeAssistantError(
            "Peak shaving charge blocked because battery SOC is at or above SOC Maximum"
        )


def preflight_battery_mode(coordinator, sn: str, mode: str) -> None:
    """Raise if `mode` would be rejected for `sn`, without sending anything.

    Lets a multi-target caller check every inverter before it switches the
    first one. Only charge / discharge have a guard (SOC protection); idle
    and self-consume always pass.
    """
    if mode == "charge":
        _block_manual_charge_if_needed(coordinator, sn)
    elif mode == "discharge":
        _block_manual_discharge_if_needed(coordinator, sn)


async def async_send_battery_mode(
    hass: HomeAssistant,
    coordinator,
    sn: str,
    mode: str,
    *,
    power: int | None = None,
) -> None:
    """Send one battery operating-mode command for `sn`.

    Applies the same SOC-protection guards and protection-controller
    bookkeeping as the mode buttons, then requests a refresh. Shared by
    HyxiModeButton and the hyxi_cloud.set_battery_mode service (which layers
    its own Energy-Manager check on top).

    `power` (watts) applies to charge / discharge only; when omitted it
    falls back to the paired power number entity, then 100 W.
    """
    client = coordinator.client
    try:
        if mode == "idle":
            await client.set_mode_idle(sn)
        elif mode == "charge":
            _block_manual_charge_if_needed(coordinator, sn)
            watts = power if power is not None else _get_power_value(hass, sn, "charge")
            _LOGGER.debug("Setting %s to CHARGE at %dW", mask_sn(sn), watts)
            await client.set_mode_charge(sn, watts)
        elif mode == "discharge":
            _block_manual_discharge_if_needed(coordinator, sn)
            watts = (
                power if power is not None else _get_power_value(hass, sn, "discharge")
            )
            _LOGGER.debug("Setting %s to DISCHARGE at %dW", mask_sn(sn), watts)
            await client.set_mode_discharge(sn, watts)
        elif mode == "self_consume":
            await client.set_mode_self_consume(sn)
        _note_manual_mode(coordinator, sn, mode)
        _LOGGER.info("Mode '%s' command sent to %s", mode, mask_sn(sn))
        await coordinator.async_request_refresh()
    except HyxiApiClient.ControlError as err:
        _LOGGER.exception("Failed to set mode '%s' for %s: %s", mode, mask_sn(sn), err)
        raise HomeAssistantError(f"Failed to set mode '{mode}': {err}") from err

"""Integration tests for battery control on cloud entries: HYXI's cloud
doesn't control batteries with an owner's API keys, so cloud entries offer
no battery controls, while microinverter power control stays."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from custom_components.hyxi_cloud.const import (
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    CONF_EM_P1_ENTITY,
    DOMAIN,
)
from custom_components.hyxi_cloud.migrations import _cloud_battery_control_issue_id
from tests.integration.entries import cloud_entry, lookup_entity_id, serve_devices

HYBRID = {
    "device_name": "Inverter",
    "model": "HYX-H10K-HT",
    "device_type_code": "HYBRID_INVERTER",
    "metrics": {"batSoc": "50"},
}
MICRO = {
    "device_name": "Microinverter",
    "device_type_code": "MICRO_INVERTER",
    "metrics": {},
}
CONTROL_AND_EM = {
    "enable_battery_control": True,
    CONF_EM_ENABLED: True,
    CONF_EM_INVERTER_SN: "SN_HYB",
    CONF_EM_P1_ENTITY: "sensor.p1_meter",
}


@pytest.fixture
def client():
    """The mocked HyxiApiClient the entry gets."""
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as client_class:
        mock_client = AsyncMock()
        mock_client.compute_derived_metrics = MagicMock(return_value={})
        mock_client.list_subscriptions.return_value = []
        client_class.return_value = mock_client
        yield mock_client


async def _setup(hass: HomeAssistant, client, devices: dict, options: dict, entry=None):
    entry = entry or cloud_entry(hass)
    hass.config_entries.async_update_entry(entry, options=options)
    serve_devices(client, devices)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _issue(hass: HomeAssistant, entry):
    return ir.async_get(hass).async_get_issue(
        DOMAIN, _cloud_battery_control_issue_id(entry)
    )


@pytest.mark.asyncio
async def test_a_cloud_entry_offers_no_battery_control(hass: HomeAssistant, client):
    """A cloud entry with device control and the Energy Manager on gets no
    battery controls, protection or Energy Manager: device control is
    switched off, the Energy Manager's options and device are removed, and
    a repair issue explains why."""
    entry = cloud_entry(hass)
    em_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN_HYB_energy_manager")},
        name="Energy Manager",
    )
    entry = await _setup(hass, client, {"SN_HYB": HYBRID}, CONTROL_AND_EM, entry)
    coordinator = entry.runtime_data

    assert lookup_entity_id(hass, "button", "SN_HYB", "mode_idle") is None
    assert lookup_entity_id(hass, "number", "SN_HYB", "charge_power") is None
    assert not coordinator.protection_controllers
    assert coordinator.engine is None
    assert entry.options["enable_battery_control"] is False
    assert CONF_EM_ENABLED not in entry.options
    assert CONF_EM_INVERTER_SN not in entry.options
    assert dr.async_get(hass).async_get(em_device.id) is None
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "cloud_battery_control"
    assert issue.translation_placeholders == {"entry": entry.title}


@pytest.mark.asyncio
async def test_microinverter_power_control_stays_on_the_cloud(
    hass: HomeAssistant, client
):
    """With microinverters, device control stays on for their power switch,
    while the hybrid inverter's battery controls are not offered."""
    entry = await _setup(
        hass,
        client,
        {"SN_HYB": HYBRID, "SN_MIC": MICRO},
        {"enable_battery_control": True},
    )

    assert lookup_entity_id(hass, "switch", "SN_MIC", "micro_power") is not None
    assert lookup_entity_id(hass, "button", "SN_HYB", "mode_idle") is None
    assert entry.options["enable_battery_control"] is True
    assert _issue(hass, entry) is not None


@pytest.mark.asyncio
async def test_the_issue_is_raised_once(hass: HomeAssistant, client):
    """Once raised, the repair issue isn't raised again for the entry, even
    while microinverters keep device control on."""
    devices = {"SN_HYB": HYBRID, "SN_MIC": MICRO}
    entry = await _setup(hass, client, devices, {"enable_battery_control": True})
    ir.async_delete_issue(hass, DOMAIN, _cloud_battery_control_issue_id(entry))

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass, entry) is None


@pytest.mark.asyncio
async def test_removing_the_entry_removes_the_issue(hass: HomeAssistant, client):
    """The repair issue goes with the entry it names."""
    entry = await _setup(hass, client, {"SN_HYB": HYBRID}, CONTROL_AND_EM)

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass, entry) is None


@pytest.mark.asyncio
async def test_no_issue_without_battery_control(hass: HomeAssistant, client):
    """A cloud entry that never turned on device control gets no issue."""
    entry = await _setup(hass, client, {"SN_HYB": HYBRID}, {})

    assert _issue(hass, entry) is None


@pytest.mark.asyncio
async def test_set_battery_mode_refuses_a_cloud_inverter(hass: HomeAssistant, client):
    """set_battery_mode refuses a cloud inverter, pointing to Modbus."""
    entry = await _setup(hass, client, {"SN_MIC": MICRO, "SN_HYB": HYBRID}, {})
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "SN_HYB"), entry.entry_id
    )

    with pytest.raises(ServiceValidationError, match="local Modbus entry"):
        await hass.services.async_call(
            DOMAIN,
            "set_battery_mode",
            {"device_id": device.id, "mode": "idle"},
            blocking=True,
        )
    client.set_mode_idle.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.usefixtures("cloud_battery_control")
async def test_cloud_battery_control_returns_with_the_flag(hass: HomeAssistant, client):
    """With CLOUD_BATTERY_CONTROL_SUPPORTED, a cloud entry offers battery
    controls again and gets no issue."""
    entry = await _setup(
        hass, client, {"SN_HYB": HYBRID}, {"enable_battery_control": True}
    )

    assert lookup_entity_id(hass, "button", "SN_HYB", "mode_idle") is not None
    assert entry.options["enable_battery_control"] is True
    assert _issue(hass, entry) is None


async def _option_fields(hass: HomeAssistant, entry) -> set[str]:
    form = await hass.config_entries.options.async_init(entry.entry_id)
    return {str(key) for key in form["data_schema"].schema}


@pytest.mark.asyncio
async def test_a_cloud_entry_with_only_batteries_offers_no_device_control(
    hass: HomeAssistant, client
):
    """The options of a cloud entry with only energy-storage devices don't
    offer device control or the Energy Manager."""
    entry = await _setup(hass, client, {"SN_HYB": HYBRID}, {})

    fields = await _option_fields(hass, entry)

    assert "enable_battery_control" not in fields
    assert "enable_energy_manager" not in fields


@pytest.mark.asyncio
async def test_a_cloud_entry_with_microinverters_offers_device_control(
    hass: HomeAssistant, client
):
    """With microinverters, a cloud entry offers device control for their
    power switch, but not the Energy Manager."""
    entry = await _setup(
        hass,
        client,
        {"SN_HYB": HYBRID, "SN_MIC": MICRO},
        {"enable_battery_control": True},
    )

    fields = await _option_fields(hass, entry)

    assert "enable_battery_control" in fields
    assert "enable_energy_manager" not in fields


@pytest.mark.asyncio
@pytest.mark.usefixtures("cloud_battery_control")
async def test_the_energy_manager_is_offered_with_a_controllable_battery(
    hass: HomeAssistant, client
):
    """With a controllable battery and device control on, the options offer
    the Energy Manager."""
    entry = await _setup(
        hass, client, {"SN_HYB": HYBRID}, {"enable_battery_control": True}
    )

    fields = await _option_fields(hass, entry)

    assert {"enable_battery_control", "enable_energy_manager"} <= fields

"""Integration tests for the parent/child device links carried in entity
``DeviceInfo``.

Home Assistant dropped the ``via_device`` identifier tuple from
``DeviceInfo``; entities now resolve the parent to its registry id and pass
``via_device_id``. HA raises ``DeviceInfoError`` (and drops the entity) if
that id doesn't resolve, so these run the real setup and check both the
entities and the resulting device hierarchy.
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hyxi_cloud.const import (
    CONF_ACCESS_KEY,
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    CONF_SECRET_KEY,
    DOMAIN,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, data: dict) -> None:
    with patch("custom_components.hyxi_cloud.HyxiApiClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client._refresh_token.return_value = True
        mock_client.get_all_device_data.return_value = {"data": data, "attempts": 1}
        mock_client_class.return_value = mock_client
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_energy_manager_device_links_to_its_inverter(hass: HomeAssistant):
    """The Energy Manager virtual device hangs off the inverter it manages,
    and its sensors are added (i.e. HA accepted the resolved via_device_id)."""
    inv_sn = "INV_EM"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"},
        options={CONF_EM_ENABLED: True, CONF_EM_INVERTER_SN: inv_sn},
        unique_id="ak",
    )
    entry.add_to_hass(hass)

    await _setup(
        hass,
        entry,
        {
            inv_sn: {
                "device_name": "Inverter",
                "device_type_code": "HYBRID_INVERTER",
                "metrics": {"batSoc": "50", "deviceSn": inv_sn},
            }
        },
    )

    device_registry = dr.async_get(hass)
    inverter = device_registry.async_get_device_by_identifier(
        (DOMAIN, inv_sn), entry.entry_id
    )
    em_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{inv_sn}_energy_manager"), entry.entry_id
    )
    assert inverter is not None
    assert em_device is not None
    assert em_device.via_device_id == inverter.id

    entity_registry = er.async_get(hass)
    em_entities = [
        e
        for e in er.async_entries_for_device(entity_registry, em_device.id)
        if e.domain in ("sensor", "binary_sensor")
    ]
    assert em_entities, "EM sensors were dropped (via_device_id rejected)"


@pytest.mark.asyncio
async def test_child_device_links_to_parent_collector(hass: HomeAssistant):
    """A device reporting a parentSn is registered under the parent collector."""
    parent_sn, child_sn = "COLLECTOR_1", "MICRO_CHILD_1"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ACCESS_KEY: "ak", CONF_SECRET_KEY: "sk"},
        options={},
        unique_id="ak",
    )
    entry.add_to_hass(hass)

    await _setup(
        hass,
        entry,
        {
            parent_sn: {
                "device_name": "Collector",
                "device_type_code": "COLLECTOR",
                "metrics": {"deviceSn": parent_sn},
            },
            child_sn: {
                "device_name": "Micro",
                "device_type_code": "MICRO_INVERTER",
                "metrics": {
                    "parentSn": parent_sn,
                    "acP": "120.0",
                    "deviceSn": child_sn,
                },
            },
        },
    )

    device_registry = dr.async_get(hass)
    parent = device_registry.async_get_device_by_identifier(
        (DOMAIN, parent_sn), entry.entry_id
    )
    child = device_registry.async_get_device_by_identifier(
        (DOMAIN, child_sn), entry.entry_id
    )
    assert parent is not None
    assert child is not None
    assert child.via_device_id == parent.id

    # The child's alarm sensor sets via_device_id in its own DeviceInfo; a
    # bad id there raises DeviceInfoError and the entity is silently dropped.
    entity_registry = er.async_get(hass)
    assert entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_{child_sn}_device_alarm"
    )

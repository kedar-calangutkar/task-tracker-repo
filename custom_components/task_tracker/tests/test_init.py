"""Tests for the Task Tracker component initialization/lifecycle."""
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.task_tracker.const import (
    DOMAIN, CONF_NAME, CONF_TYPE, CONF_INTERVAL, CONF_ICON, TYPE_SLIDING
)
from custom_components.task_tracker import async_remove_config_entry_device

from pytest_homeassistant_custom_component.common import MockConfigEntry


def _basic_entry_data(name="Setup Task"):
    return {
        CONF_NAME: name,
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_ICON: "mdi:checkbox-marked-circle-outline",
    }


async def test_setup_and_unload_entry(hass: HomeAssistant):
    """Setting up a config entry forwards to the sensor platform, and
    unloading it cleanly tears the entity down."""
    entry = MockConfigEntry(domain=DOMAIN, data=_basic_entry_data())
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert DOMAIN in hass.config.components
    assert hass.states.get("sensor.setup_task") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    # RestoreEntity keeps a state row around (for the next restore) rather
    # than removing it outright - unloading marks it unavailable instead.
    assert hass.states.get("sensor.setup_task").state == "unavailable"


async def test_options_update_triggers_reload(hass: HomeAssistant):
    """Changing a config entry's options reloads the integration via the
    registered update listener, so options-flow edits take effect
    immediately without a manual restart."""
    entry = MockConfigEntry(domain=DOMAIN, data=_basic_entry_data())
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as mock_reload:
        hass.config_entries.async_update_entry(entry, options={"interval_days": 3})
        await hass.async_block_till_done()

        mock_reload.assert_called_once_with(entry.entry_id)


async def test_remove_orphaned_device_allowed(hass: HomeAssistant):
    """An orphaned device (no entities left registered under it - e.g.
    left behind by an old integration version that didn't set
    device_info) can be removed."""
    entry = MockConfigEntry(domain=DOMAIN, data=_basic_entry_data("Device Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    orphan_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "orphaned-device-id")},
    )

    result = await async_remove_config_entry_device(hass, entry, orphan_device)
    assert result is True


async def test_remove_device_blocked_when_entity_still_attached(hass: HomeAssistant):
    """The device backing a live entity must not be removable - only
    orphaned devices with no entities left."""
    entry = MockConfigEntry(domain=DOMAIN, data=_basic_entry_data("Live Device Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    live_device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert live_device is not None

    result = await async_remove_config_entry_device(hass, entry, live_device)
    assert result is False

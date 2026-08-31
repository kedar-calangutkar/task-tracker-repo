"""The Task Tracker integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.util import dt as dt_util
from .const import DOMAIN

SERVICE_COMPLETE_TASK = "complete_task"
SERVICE_RESET_HISTORY = "reset_history"
SERVICE_SNOOZE_TASK = "snooze_task"
SERVICE_UNSNOOZE_TASK = "unsnooze_task"

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Task Tracker services."""
    
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Task Tracker from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    # Register entity services
    # This requires adding methods to TaskSensor
    # For now, this is a placeholder to show the direction.
    
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])

async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow removing a device manually from the UI, as long as it has no entities left.

    Older versions of this integration didn't set device_info on the sensor,
    so HA auto-created a generic device per config entry. Once device_info
    was added, entities moved to a new, properly named device, leaving the
    old one orphaned. This lets users clean those up without risking removal
    of a device that still holds a live entity.
    """
    entity_registry = er.async_get(hass)
    entities = er.async_entries_for_device(
        entity_registry, device_entry.id, include_disabled_entities=True
    )
    return len(entities) == 0

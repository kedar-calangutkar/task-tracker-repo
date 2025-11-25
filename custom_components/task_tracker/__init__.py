"""The Task Tracker integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.util import dt as dt_util
from .const import DOMAIN

SERVICE_COMPLETE_TASK = "complete_task"
SERVICE_RESET_HISTORY = "reset_history"

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Task Tracker services."""
    
    async def handle_complete_task(call: ServiceCall):
        entity_ids = call.data.get("entity_id")
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        
        custom_date_str = call.data.get("last_done")
        custom_date = None
        if custom_date_str:
            custom_date = dt_util.parse_datetime(custom_date_str)

        platforms = async_get_platforms(hass, DOMAIN)
        for platform in platforms:
            for entity in platform.entities.values():
                if entity.entity_id in entity_ids:
                    if hasattr(entity, "mark_as_done"):
                        await entity.mark_as_done(custom_date)

    async def handle_reset_history(call: ServiceCall):
        entity_ids = call.data.get("entity_id")
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
            
        platforms = async_get_platforms(hass, DOMAIN)
        for platform in platforms:
            for entity in platform.entities.values():
                if entity.entity_id in entity_ids:
                    if hasattr(entity, "reset_history"):
                        await entity.reset_history()

    hass.services.async_register(DOMAIN, SERVICE_COMPLETE_TASK, handle_complete_task)
    hass.services.async_register(DOMAIN, SERVICE_RESET_HISTORY, handle_reset_history)
    
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Task Tracker from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    # Listen for option updates (The Configure Button)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])

async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
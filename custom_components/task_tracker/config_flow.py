"""Config flow for Task Tracker integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SelectOptionDict,
    TimeSelector,
    IconSelector,
    NumberSelector,
    NumberSelectorConfig,
)

from .const import (
    DOMAIN, CONF_NAME, CONF_TYPE, CONF_INTERVAL,
    CONF_TIME, CONF_DAYS, CONF_TAGS, CONF_ASSIGNEES, CONF_ICON,
    TYPE_FIXED, TYPE_SLIDING, TYPE_PREDICTIVE
)

_LOGGER = logging.getLogger(__name__)

TYPE_OPTIONS = [
    SelectOptionDict(value=TYPE_FIXED, label="Fixed Schedule"),
    SelectOptionDict(value=TYPE_SLIDING, label="Sliding Interval"),
    SelectOptionDict(value=TYPE_PREDICTIVE, label="Predictive"),
]

DAY_OPTIONS = [
    SelectOptionDict(value="mon", label="Monday"),
    SelectOptionDict(value="tue", label="Tuesday"),
    SelectOptionDict(value="wed", label="Wednesday"),
    SelectOptionDict(value="thu", label="Thursday"),
    SelectOptionDict(value="fri", label="Friday"),
    SelectOptionDict(value="sat", label="Saturday"),
    SelectOptionDict(value="sun", label="Sunday"),
]

class TaskTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Task Tracker."""

    VERSION = 1

    def __init__(self):
        """Initialize the flow state."""
        self.task_info = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return TaskTrackerOptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Get Name and Type."""
        errors = {}
        if user_input is not None:
            self.task_info = user_input
            return await self.async_step_details()

        data_schema = vol.Schema({
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_TYPE, default=TYPE_SLIDING): SelectSelector(
                SelectSelectorConfig(options=TYPE_OPTIONS)
            ),
        })

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_details(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Get details based on Type."""
        errors = {}
        if user_input is not None:
            final_data = {**self.task_info, **user_input}
            return self.async_create_entry(
                title=self.task_info[CONF_NAME], 
                data=final_data
            )

        # Helpers
        user_options = self._get_person_options()
        tag_options = self._get_tag_options()

        schema = self._build_schema(self.task_info[CONF_TYPE], user_options, tag_options)

        return self.async_show_form(
            step_id="details", data_schema=vol.Schema(schema), errors=errors
        )
    
    # --- HELPER FUNCTIONS TO SHARE LOGIC WITH OPTIONS FLOW ---
    def _get_person_options(self):
        user_options = []
        persons = self.hass.states.async_all("person")
        for person in persons:
            user_id = person.attributes.get("user_id")
            if user_id:
                friendly_name = person.attributes.get("friendly_name", person.entity_id)
                user_options.append(
                    SelectOptionDict(value=user_id, label=friendly_name)
                )
        if not user_options:
            user_options = [SelectOptionDict(value="none", label="No Persons Found")]
        return user_options

    def _get_tag_options(self):
        existing_tags = set()
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            tags = entry.data.get(CONF_TAGS, [])
            # Also check options since that is where edits live!
            if not tags: 
                 tags = entry.options.get(CONF_TAGS, [])

            if isinstance(tags, list):
                existing_tags.update(tags)
            elif isinstance(tags, str) and tags.strip():
                existing_tags.update([t.strip() for t in tags.split(",") if t.strip()])
        return sorted(list(existing_tags))

    def _build_schema(self, task_type, user_options, tag_options, defaults=None):
        if defaults is None:
            defaults = {}
            
        schema = {}

        if task_type in [TYPE_SLIDING, TYPE_PREDICTIVE]:
            default_interval = defaults.get(CONF_INTERVAL, 7)
            schema[vol.Optional(CONF_INTERVAL, default=default_interval)] = NumberSelector(
                NumberSelectorConfig(min=1, mode="box", unit_of_measurement="days")
            )
        
        if task_type == TYPE_FIXED:
            default_days = defaults.get(CONF_DAYS, [])
            default_time = defaults.get(CONF_TIME, "00:00:00")
            
            schema[vol.Optional(CONF_DAYS, default=default_days)] = SelectSelector(
                SelectSelectorConfig(options=DAY_OPTIONS, multiple=True)
            )
            schema[vol.Optional(CONF_TIME, default=default_time)] = TimeSelector()

        default_icon = defaults.get(CONF_ICON, "mdi:checkbox-marked-circle-outline")
        schema[vol.Optional(CONF_ICON, default=default_icon)] = IconSelector()
        
        default_assignees = defaults.get(CONF_ASSIGNEES, [])
        schema[vol.Optional(CONF_ASSIGNEES, default=default_assignees)] = SelectSelector(
            SelectSelectorConfig(options=user_options, multiple=True)
        )
        
        default_tags = defaults.get(CONF_TAGS, [])
        schema[vol.Optional(CONF_TAGS, default=default_tags)] = SelectSelector(
            SelectSelectorConfig(
                options=tag_options, 
                multiple=True, 
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN
            )
        )
        return schema


class TaskTrackerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for editing tasks."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.task_info = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Edit Name and Type."""
        errors = {}
        
        # Load current values (prefer options, fall back to data)
        current_config = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            self.task_info = user_input
            return await self.async_step_details()

        data_schema = vol.Schema({
            vol.Required(CONF_NAME, default=current_config.get(CONF_NAME)): str,
            vol.Required(CONF_TYPE, default=current_config.get(CONF_TYPE)): SelectSelector(
                SelectSelectorConfig(options=TYPE_OPTIONS)
            ),
        })

        return self.async_show_form(
            step_id="init", data_schema=data_schema, errors=errors
        )

    async def async_step_details(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Edit details."""
        errors = {}
        
        if user_input is not None:
            # Merge and Save
            final_data = {**self.task_info, **user_input}
            return self.async_create_entry(title="", data=final_data)

        # Helpers (using methods from the main Flow class is tricky, 
        # so we instantiate helper logic or duplicate it. 
        # For simplicity in this file structure, we duplicate the helper calls or access them if static.
        # Actually, python lets us use the ConfigFlow class methods if we make them static or standalone functions.
        # BUT, simpler: just duplicate the lookups inside this method since we have self.hass
        
        # 1. Persons
        user_options = []
        persons = self.hass.states.async_all("person")
        for person in persons:
            uid = person.attributes.get("user_id")
            if uid:
                fname = person.attributes.get("friendly_name", person.entity_id)
                user_options.append(SelectOptionDict(value=uid, label=fname))
        if not user_options:
             user_options = [SelectOptionDict(value="none", label="No Persons Found")]

        # 2. Tags
        existing_tags = set()
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            tags = entry.data.get(CONF_TAGS, [])
            if not tags: tags = entry.options.get(CONF_TAGS, [])
            if isinstance(tags, list): existing_tags.update(tags)
            elif isinstance(tags, str) and tags.strip(): existing_tags.update([t.strip() for t in tags.split(",")])
        tag_options = sorted(list(existing_tags))

        # Build Schema with Defaults
        current_config = {**self.config_entry.data, **self.config_entry.options}
        
        # We need to manually construct the schema because we need to inject defaults
        # We can't reuse _build_schema easily unless it's a standalone function.
        # Let's just write it out:
        
        schema = {}
        task_type = self.task_info[CONF_TYPE]
        defaults = current_config

        if task_type in [TYPE_SLIDING, TYPE_PREDICTIVE]:
            val = defaults.get(CONF_INTERVAL, 7)
            schema[vol.Optional(CONF_INTERVAL, default=val)] = NumberSelector(
                NumberSelectorConfig(min=1, mode="box", unit_of_measurement="days")
            )
        
        if task_type == TYPE_FIXED:
            val_days = defaults.get(CONF_DAYS, [])
            val_time = defaults.get(CONF_TIME, "00:00:00")
            schema[vol.Optional(CONF_DAYS, default=val_days)] = SelectSelector(
                SelectSelectorConfig(options=DAY_OPTIONS, multiple=True)
            )
            schema[vol.Optional(CONF_TIME, default=val_time)] = TimeSelector()

        val_icon = defaults.get(CONF_ICON, "mdi:checkbox-marked-circle-outline")
        schema[vol.Optional(CONF_ICON, default=val_icon)] = IconSelector()
        
        val_assignees = defaults.get(CONF_ASSIGNEES, [])
        schema[vol.Optional(CONF_ASSIGNEES, default=val_assignees)] = SelectSelector(
            SelectSelectorConfig(options=user_options, multiple=True)
        )
        
        val_tags = defaults.get(CONF_TAGS, [])
        schema[vol.Optional(CONF_TAGS, default=val_tags)] = SelectSelector(
            SelectSelectorConfig(
                options=tag_options, 
                multiple=True, 
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN
            )
        )

        return self.async_show_form(
            step_id="details", data_schema=vol.Schema(schema), errors=errors
        )
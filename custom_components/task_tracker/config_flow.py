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
    TimeSelector,
    IconSelector,
    NumberSelector,
    NumberSelectorConfig,
)

from .const import (
    DOMAIN, CONF_NAME, CONF_TYPE, CONF_INTERVAL,
    CONF_TIME, CONF_DAYS, CONF_TAGS, CONF_NOTIFY_ENTITY, CONF_ICON,
    TYPE_FIXED, TYPE_SLIDING, TYPE_PREDICTIVE
)

_LOGGER = logging.getLogger(__name__)

# Replaced SelectOptionDict with plain dicts for maximum HA version compatibility
TYPE_OPTIONS = [
    {"value": TYPE_FIXED, "label": "Fixed Schedule"},
    {"value": TYPE_SLIDING, "label": "Sliding Interval"},
    {"value": TYPE_PREDICTIVE, "label": "Predictive"},
]

DAY_OPTIONS = [
    {"value": "mon", "label": "Monday"},
    {"value": "tue", "label": "Tuesday"},
    {"value": "wed", "label": "Wednesday"},
    {"value": "thu", "label": "Thursday"},
    {"value": "fri", "label": "Friday"},
    {"value": "sat", "label": "Saturday"},
    {"value": "sun", "label": "Sunday"},
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
                title=self.task_info.get(CONF_NAME, "Task"), 
                data=final_data
            )

        # Helpers
        notify_options = self._get_notify_options(self.hass)
        tag_options = self._get_tag_options(self.hass)

        schema = self._build_schema(self.hass, self.task_info[CONF_TYPE], notify_options, tag_options)

        return self.async_show_form(
            step_id="details", data_schema=vol.Schema(schema), errors=errors
        )

    # --- HELPER FUNCTIONS TO SHARE LOGIC WITH OPTIONS FLOW ---
    @staticmethod
    def _get_notify_options(hass):
        """Dynamically get all registered notify services (including YAML groups)."""
        services = hass.services.async_services().get("notify", {})
        options = [
            {"value": f"notify.{service}", "label": f"notify.{service}"}
            for service in services
        ]
        if not options:
            options = [{"value": "notify.notify", "label": "notify.notify"}]
        return options

    @staticmethod
    def _get_tag_options(hass):
        existing_tags = set()
        for entry in hass.config_entries.async_entries(DOMAIN):
            # Prioritize options (current state of edits) over data (initial creation state)
            tags = entry.options.get(CONF_TAGS) or entry.data.get(CONF_TAGS) or []

            if isinstance(tags, list):
                existing_tags.update([str(t) for t in tags if t]) # Protect against None elements
            elif isinstance(tags, str) and tags.strip():
                existing_tags.update([t.strip() for t in tags.split(",") if t.strip()])
        return sorted(list(existing_tags))

    @staticmethod
    def _build_schema(hass, task_type, notify_options, tag_options, defaults=None):
        if defaults is None:
            defaults = {}
            
        schema = {}

        if task_type in [TYPE_SLIDING, TYPE_PREDICTIVE]:
            default_interval = defaults.get(CONF_INTERVAL) or 7
            schema[vol.Optional(CONF_INTERVAL, default=default_interval)] = NumberSelector(
                NumberSelectorConfig(min=1, mode="box", unit_of_measurement="days")
            )
            
        if task_type in [TYPE_FIXED, TYPE_SLIDING]:
            default_time = defaults.get(CONF_TIME) or "00:00:00"
            schema[vol.Optional(CONF_TIME, default=default_time)] = TimeSelector()
        
        if task_type == TYPE_FIXED:
            default_days = defaults.get(CONF_DAYS) or []
            schema[vol.Optional(CONF_DAYS, default=default_days)] = SelectSelector(
                SelectSelectorConfig(options=DAY_OPTIONS, multiple=True)
            )

        default_icon = defaults.get(CONF_ICON) or "mdi:checkbox-marked-circle-outline"
        schema[vol.Optional(CONF_ICON, default=default_icon)] = IconSelector()
        
        default_notify_entity = defaults.get(CONF_NOTIFY_ENTITY) or ""
        schema[vol.Optional(CONF_NOTIFY_ENTITY, default=default_notify_entity)] = SelectSelector(
            SelectSelectorConfig(
                options=notify_options,
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True
            )
        )
        
        default_tags = defaults.get(CONF_TAGS) or []
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
        # Use _config_entry to avoid conflict with HA's native read-only property
        self._config_entry = config_entry
        self.task_info = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Edit Name and Type."""
        errors = {}
        
        # Load current values (prefer options, fall back to data)
        current_config = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            self.task_info = user_input
            return await self.async_step_details()

        # The 'or' statements prevent 'None' values from breaking the schema.
        data_schema = vol.Schema({
            vol.Required(CONF_NAME, default=current_config.get(CONF_NAME) or "Task"): str,
            vol.Required(CONF_TYPE, default=current_config.get(CONF_TYPE) or TYPE_SLIDING): SelectSelector(
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
        
        # Helpers
        notify_options = TaskTrackerConfigFlow._get_notify_options(self.hass)
        tag_options = TaskTrackerConfigFlow._get_tag_options(self.hass)

        # Build Schema with Defaults
        current_config = {**self._config_entry.data, **self._config_entry.options}

        schema = TaskTrackerConfigFlow._build_schema(self.hass, self.task_info.get(CONF_TYPE, TYPE_SLIDING), notify_options, tag_options, current_config)

        return self.async_show_form(
            step_id="details", data_schema=vol.Schema(schema), errors=errors
        )
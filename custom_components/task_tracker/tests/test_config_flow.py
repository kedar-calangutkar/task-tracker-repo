"""Tests for the Task Tracker config and options flows."""
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.task_tracker.config_flow import TaskTrackerConfigFlow
from custom_components.task_tracker.const import (
    DOMAIN, CONF_NAME, CONF_TYPE, CONF_INTERVAL, CONF_TIME, CONF_DAYS,
    CONF_TAGS, CONF_NOTIFY_ENTITY, CONF_ICON,
    TYPE_FIXED, TYPE_SLIDING, TYPE_PREDICTIVE
)

from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_user_flow_creates_sliding_task(hass: HomeAssistant):
    """Full user flow for a Sliding task: name/type, then interval+time."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Water Plants", CONF_TYPE: TYPE_SLIDING},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "details"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_INTERVAL: 5,
            CONF_TIME: "09:00:00",
            CONF_ICON: "mdi:watering-can",
            CONF_NOTIFY_ENTITY: "notify.mobile_app_test",
            CONF_TAGS: ["chores"],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Water Plants"
    assert result["data"][CONF_NAME] == "Water Plants"
    assert result["data"][CONF_TYPE] == TYPE_SLIDING
    assert result["data"][CONF_INTERVAL] == 5
    assert result["data"][CONF_TIME] == "09:00:00"
    assert result["data"][CONF_TAGS] == ["chores"]


async def test_user_flow_creates_fixed_task(hass: HomeAssistant):
    """Full user flow for a Fixed task: name/type, then days+time (no interval)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Take Out Trash", CONF_TYPE: TYPE_FIXED},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "details"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_TIME: "07:30:00",
            CONF_DAYS: ["mon", "thu"],
            CONF_ICON: "mdi:trash-can",
            CONF_NOTIFY_ENTITY: "notify.notify",
            CONF_TAGS: [],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TYPE] == TYPE_FIXED
    assert result["data"][CONF_DAYS] == ["mon", "thu"]
    assert CONF_INTERVAL not in result["data"]


async def test_user_flow_creates_predictive_task(hass: HomeAssistant):
    """Full user flow for a Predictive task: name/type, then interval only
    (no time/days field, since predictive tasks don't have a clock schedule)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Refill Soap", CONF_TYPE: TYPE_PREDICTIVE},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "details"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_INTERVAL: 14,
            CONF_ICON: "mdi:soap",
            CONF_NOTIFY_ENTITY: "notify.notify",
            CONF_TAGS: [],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TYPE] == TYPE_PREDICTIVE
    assert result["data"][CONF_INTERVAL] == 14
    assert CONF_TIME not in result["data"]
    assert CONF_DAYS not in result["data"]


async def test_user_flow_defaults_title_to_task_when_name_missing(hass: HomeAssistant):
    """self.task_info.get(CONF_NAME, "Task") falls back to a generic title
    if somehow no name made it into task_info."""
    flow = TaskTrackerConfigFlow()
    flow.hass = hass
    flow.task_info = {CONF_TYPE: TYPE_SLIDING}

    result = await flow.async_step_details(
        {CONF_INTERVAL: 3, CONF_ICON: "mdi:pill", CONF_NOTIFY_ENTITY: "", CONF_TAGS: []}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Task"


async def test_get_notify_options_fallback_when_none_registered(hass: HomeAssistant):
    """With no notify.* services registered, the dropdown falls back to a
    single notify.notify placeholder instead of an empty list."""
    options = TaskTrackerConfigFlow._get_notify_options(hass)
    assert options == [{"value": "notify.notify", "label": "notify.notify"}]


async def test_get_notify_options_lists_registered_services(hass: HomeAssistant):
    """Registered notify.* services (including YAML-defined groups) show
    up as selectable options."""
    async def _noop(call):
        pass

    hass.services.async_register("notify", "mobile_app_test", _noop)
    hass.services.async_register("notify", "family_group", _noop)

    options = TaskTrackerConfigFlow._get_notify_options(hass)
    values = {opt["value"] for opt in options}
    assert values == {"notify.mobile_app_test", "notify.family_group"}


async def test_get_tag_options_aggregates_across_entries_and_formats(hass: HomeAssistant):
    """Tags are aggregated across every existing task entry, supporting
    both the list format and the legacy comma-separated string format,
    while ignoring falsy elements."""
    entry_list_tags = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "A", CONF_TYPE: TYPE_SLIDING, CONF_TAGS: ["kitchen", "chores", None]},
    )
    entry_list_tags.add_to_hass(hass)

    entry_string_tags = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "B", CONF_TYPE: TYPE_SLIDING, CONF_TAGS: "outdoor, chores"},
    )
    entry_string_tags.add_to_hass(hass)

    # Options (edits) take priority over the original data for the same entry.
    entry_with_options = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: "C", CONF_TYPE: TYPE_SLIDING, CONF_TAGS: ["stale"]},
        options={CONF_TAGS: ["fresh"]},
    )
    entry_with_options.add_to_hass(hass)

    tag_options = TaskTrackerConfigFlow._get_tag_options(hass)
    assert tag_options == sorted(["kitchen", "chores", "outdoor", "fresh"])


async def test_options_flow_edit_task_prefills_and_updates(hass: HomeAssistant):
    """Editing an existing task via the options flow prefills the current
    values and writes the merged result back as entry.options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_NAME: "Old Name",
            CONF_TYPE: TYPE_SLIDING,
            CONF_INTERVAL: 7,
            CONF_TIME: "00:00:00",
            CONF_ICON: "mdi:pill",
            CONF_NOTIFY_ENTITY: "",
            CONF_TAGS: [],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_NAME: "New Name", CONF_TYPE: TYPE_SLIDING},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "details"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_INTERVAL: 10,
            CONF_TIME: "12:00:00",
            CONF_ICON: "mdi:pill",
            CONF_NOTIFY_ENTITY: "notify.notify",
            CONF_TAGS: ["chores"],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == ""
    assert result["data"][CONF_NAME] == "New Name"
    assert result["data"][CONF_INTERVAL] == 10

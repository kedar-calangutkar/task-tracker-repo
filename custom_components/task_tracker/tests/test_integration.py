"""Integration-level tests: real config entry setup, entity services,
state restoration across restarts, and the due/snooze timers.

Unlike test_sensor.py (which unit-tests TaskSensor directly with a mocked
hass), these tests go through the real config entry + entity platform +
service-registration machinery, so they exercise async_setup_entry,
the registered task_tracker.* services (including their voluptuous
schemas), RestoreEntity wiring, and the async_track_point_in_time timers.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.util import dt as dt_util

from custom_components.task_tracker.const import (
    DOMAIN, CONF_NAME, CONF_TYPE, CONF_INTERVAL, CONF_ICON, CONF_TIME,
    CONF_DAYS, CONF_TAGS, TYPE_SLIDING, TYPE_FIXED
)

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry, async_fire_time_changed, async_capture_events,
    mock_restore_cache,
)


def _sliding_entry_data(name="Integration Task", interval=7):
    return {
        CONF_NAME: name,
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: interval,
        CONF_ICON: "mdi:checkbox-marked-circle-outline",
    }


async def test_complete_task_service(hass: HomeAssistant):
    """The registered task_tracker.complete_task entity service marks the
    task done and records history, via the real service-call path."""
    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Service Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "sensor.service_task"
    await hass.services.async_call(
        DOMAIN, "complete_task", {"entity_id": entity_id}, blocking=True
    )

    state = hass.states.get(entity_id)
    assert len(state.attributes["history"]) == 1
    assert "last_done" in state.attributes


async def test_complete_task_service_rejects_bad_last_done(hass: HomeAssistant):
    """An unparseable last_done passed through the real service call path
    is rejected, matching the direct-call regression test in test_sensor.py."""
    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Bad Input Service Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "sensor.bad_input_service_task"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "complete_task",
            {"entity_id": entity_id, "last_done": "not-a-real-datetime"},
            blocking=True,
        )

    state = hass.states.get(entity_id)
    assert "history" not in state.attributes


async def test_reset_history_service(hass: HomeAssistant):
    """task_tracker.reset_history clears history via the real service path."""
    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Reset Service Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "sensor.reset_service_task"
    await hass.services.async_call(DOMAIN, "complete_task", {"entity_id": entity_id}, blocking=True)
    assert len(hass.states.get(entity_id).attributes["history"]) == 1

    await hass.services.async_call(DOMAIN, "reset_history", {"entity_id": entity_id}, blocking=True)
    assert "history" not in hass.states.get(entity_id).attributes


async def test_snooze_and_unsnooze_task_services(hass: HomeAssistant):
    """task_tracker.snooze_task and unsnooze_task work via the real
    service path, including the required 'until' field validation."""
    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Snooze Service Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "sensor.snooze_service_task"
    snooze_until = (dt_util.now() + timedelta(days=2)).isoformat()

    await hass.services.async_call(
        DOMAIN, "snooze_task",
        {"entity_id": entity_id, "until": snooze_until},
        blocking=True,
    )
    state = hass.states.get(entity_id)
    assert state.state == "Snoozed"
    assert state.attributes["snoozed_until"] == snooze_until

    await hass.services.async_call(
        DOMAIN, "unsnooze_task", {"entity_id": entity_id}, blocking=True
    )
    state = hass.states.get(entity_id)
    assert state.state != "Snoozed"
    assert "snoozed_until" not in state.attributes


async def test_snooze_task_service_requires_until(hass: HomeAssistant):
    """The 'until' field is vol.Required on the snooze_task service schema,
    so calling it without one is rejected before it ever reaches the entity."""
    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Snooze Required Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "snooze_task",
            {"entity_id": "sensor.snooze_required_task"},
            blocking=True,
        )


async def test_device_info_groups_entity_under_task_device(hass: HomeAssistant):
    """Each task sensor reports device_info so it appears as its own
    device (not lumped under a single generic integration device)."""
    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Device Info Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.name == "Device Info Task"
    assert device.manufacturer == "Task Tracker"


async def test_restore_history_and_last_done_across_restart(hass: HomeAssistant):
    """History, last_done, and snoozed_until from a previous run are
    restored on startup, so completions survive a Home Assistant restart."""
    entity_id = "sensor.restore_task"
    past_done = (dt_util.now() - timedelta(days=3)).isoformat()
    past_due = (dt_util.now() - timedelta(days=4)).isoformat()

    mock_restore_cache(hass, [
        State(
            entity_id,
            "Due in 4 days",
            {
                "last_done": past_done,
                "history": [{"done": past_done, "due": past_due}],
            },
        )
    ])

    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Restore Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["last_done"] == past_done
    assert state.attributes["history"] == [{"done": past_done, "due": past_due}]


async def test_restore_legacy_flat_history_format(hass: HomeAssistant):
    """A restored history attribute in the legacy flat-string format (no
    due info) is upgraded to the current {"done", "due"} dict format."""
    entity_id = "sensor.legacy_restore_task"
    past_done = (dt_util.now() - timedelta(days=3)).isoformat()

    mock_restore_cache(hass, [
        State(entity_id, "Due in 4 days", {"history": [past_done]})
    ])

    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Legacy Restore Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.attributes["history"] == [{"done": past_done, "due": None}]


async def test_restore_snoozed_until(hass: HomeAssistant):
    """A restored snoozed_until keeps the task Snoozed after a restart,
    and re-arms the snooze-expiration timer."""
    entity_id = "sensor.restore_snooze_task"
    future_snooze = (dt_util.now() + timedelta(days=1)).isoformat()

    mock_restore_cache(hass, [
        State(entity_id, "Snoozed", {"snoozed_until": future_snooze})
    ])

    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Restore Snooze Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "Snoozed"
    assert state.attributes["snoozed_until"] == future_snooze


async def test_snooze_expiration_timer_clears_snooze(hass: HomeAssistant):
    """_schedule_snooze_expiration() wires a real timer that automatically
    clears the Snoozed state the moment the snooze period ends, without
    needing an external poll."""
    fixed_now = datetime(2024, 1, 1, 7, 0, 0, tzinfo=dt_util.UTC)

    with patch("homeassistant.util.dt.utcnow", return_value=dt_util.as_utc(fixed_now)), \
         patch("homeassistant.util.dt.now", return_value=fixed_now):
        entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Snooze Timer Task"))
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.snooze_timer_task"
        snooze_until = fixed_now + timedelta(hours=1)
        await hass.services.async_call(
            DOMAIN, "snooze_task",
            {"entity_id": entity_id, "until": snooze_until.isoformat()},
            blocking=True,
        )
        assert hass.states.get(entity_id).state == "Snoozed"

    later = fixed_now + timedelta(hours=1, seconds=1)
    with patch("homeassistant.util.dt.utcnow", return_value=dt_util.as_utc(later)), \
         patch("homeassistant.util.dt.now", return_value=later):
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state != "Snoozed"
    assert "snoozed_until" not in state.attributes


async def test_due_timer_transitions_to_overdue_and_fires_event(hass: HomeAssistant):
    """_schedule_due_update() wires a real timer that flips a completed
    task straight to Overdue the moment its next_due passes, without
    waiting for an external poll or state request."""
    fixed_now = datetime(2024, 1, 1, 7, 0, 0, tzinfo=dt_util.UTC)

    with patch("homeassistant.util.dt.utcnow", return_value=dt_util.as_utc(fixed_now)), \
         patch("homeassistant.util.dt.now", return_value=fixed_now):
        entry = MockConfigEntry(
            domain=DOMAIN, data=_sliding_entry_data("Due Timer Task", interval=1)
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.due_timer_task"
        # Anchor last_done to fixed_now so next_due is a fixed point
        # (fixed_now + 1 day) that won't drift as "now" advances - unlike
        # a never-done task, whose countdown is always relative to "now".
        await hass.services.async_call(
            DOMAIN, "complete_task", {"entity_id": entity_id}, blocking=True
        )
        state = hass.states.get(entity_id)
        assert state.attributes["next_due"] == (fixed_now + timedelta(days=1)).isoformat()

        events = async_capture_events(hass, "task_tracker_task_due")

    later = fixed_now + timedelta(days=1, seconds=1)
    with patch("homeassistant.util.dt.utcnow", return_value=dt_util.as_utc(later)), \
         patch("homeassistant.util.dt.now", return_value=later):
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "Overdue"
    assert len(events) == 1
    assert events[0].data["entity_id"] == entity_id
    assert events[0].data["state"] == "Overdue"


async def test_unload_cancels_pending_timers(hass: HomeAssistant):
    """async_will_remove_from_hass() unsubscribes both the due and snooze
    timers on unload, so no callbacks fire against a torn-down entity."""
    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Unload Timer Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "sensor.unload_timer_task"
    snooze_until = (dt_util.now() + timedelta(days=1)).isoformat()
    await hass.services.async_call(
        DOMAIN, "snooze_task",
        {"entity_id": entity_id, "until": snooze_until},
        blocking=True,
    )

    entity = None
    for platform in async_get_platforms(hass, DOMAIN):
        for e in platform.entities.values():
            if e.entity_id == entity_id:
                entity = e
    assert entity is not None
    assert entity._snooze_unsub is not None
    assert entity._due_unsub is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entity._snooze_unsub is None
    assert entity._due_unsub is None


async def test_setup_entry_parses_string_format_tags(hass: HomeAssistant):
    """Tags stored as a legacy comma-separated string (rather than a
    list) are parsed correctly during sensor setup."""
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_NAME: "String Tags Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_ICON: "mdi:pill",
        CONF_TAGS: "chores, outdoor",
    })
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.string_tags_task")
    assert state.attributes["tags"] == ["chores", "outdoor"]


async def test_setup_entry_invalid_time_string_falls_back_to_midnight(hass: HomeAssistant):
    """A malformed CONF_TIME string in stored config must not crash
    setup - it falls back to the time(0, 0) "unset" default."""
    entry = MockConfigEntry(domain=DOMAIN, data={
        CONF_NAME: "Bad Time Task",
        CONF_TYPE: TYPE_FIXED,
        CONF_TIME: "not-a-time",
        CONF_DAYS: ["mon"],
        CONF_ICON: "mdi:pill",
    })
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.bad_time_task")
    assert state is not None
    # No "at HH:MM" suffix, since the fallback midnight is treated as unset.
    assert "at" not in state.attributes["schedule"]


async def test_restore_corrupted_last_done_does_not_crash(hass: HomeAssistant):
    """A malformed restored 'last_done' attribute must not crash setup."""
    entity_id = "sensor.corrupt_last_done_task"
    mock_restore_cache(hass, [State(entity_id, "Unknown", {"last_done": 12345})])

    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Corrupt Last Done Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != "Error"
    assert "last_done" not in state.attributes


async def test_restore_corrupted_snoozed_until_does_not_crash(hass: HomeAssistant):
    """A malformed restored 'snoozed_until' attribute must not crash setup."""
    entity_id = "sensor.corrupt_snooze_task"
    mock_restore_cache(hass, [State(entity_id, "Unknown", {"snoozed_until": 12345})])

    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Corrupt Snooze Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != "Error"
    assert "snoozed_until" not in state.attributes


async def test_restore_corrupted_history_does_not_crash(hass: HomeAssistant):
    """A malformed restored 'history' attribute (not even a list) must
    not crash setup - the entity should just start with empty history."""
    entity_id = "sensor.corrupt_history_task"
    mock_restore_cache(hass, [State(entity_id, "Unknown", {"history": 12345})])

    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Corrupt History Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != "Error"
    assert "history" not in state.attributes


async def test_snooze_task_service_with_naive_until_applies_default_timezone(hass: HomeAssistant):
    """A naive (no offset) 'until' value passed to the snooze_task service
    is normalized with the default timezone rather than crashing."""
    entry = MockConfigEntry(domain=DOMAIN, data=_sliding_entry_data("Naive Snooze Task"))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "sensor.naive_snooze_task"
    naive_until = "2030-01-01T09:00:00"

    await hass.services.async_call(
        DOMAIN, "snooze_task",
        {"entity_id": entity_id, "until": naive_until},
        blocking=True,
    )

    expected = dt_util.parse_datetime(naive_until).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    state = hass.states.get(entity_id)
    assert state.attributes["snoozed_until"] == expected.isoformat()

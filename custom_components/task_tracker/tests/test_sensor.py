"""Unit tests for the Task Tracker sensor platform."""
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from dateutil import rrule
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

# Import your component
# Note: In a real HA dev environment, this path might need adjustment 
# depending on where you run pytest from.
from custom_components.task_tracker.sensor import (
    TaskSensor, 
    TYPE_SLIDING, 
    TYPE_FIXED, 
    TYPE_PREDICTIVE,
    CONF_NAME, CONF_TYPE, CONF_INTERVAL, CONF_SCHEDULE,
    CONF_TAGS, CONF_NOTIFY_ENTITY, CONF_ICON, CONF_TIME, CONF_DAYS
)

# Constants for testing
DEFAULT_ICON = "mdi:checkbox-marked-circle-outline"

@pytest.fixture
def mock_now():
    """Return a fixed point in time for consistent testing.

    dt_util.now() always returns a timezone-aware datetime in real Home
    Assistant, so the mock must match that contract.
    """
    # Monday, Jan 1st 2024, 12:00:00 UTC
    return datetime(2024, 1, 1, 12, 0, 0, tzinfo=dt_util.UTC)

@pytest.fixture
def mock_hass():
    """Mock the Home Assistant core object."""
    hass = MagicMock()
    hass.data = {}
    # Satisfies Entity.async_write_ha_state()'s thread-safety check.
    hass.loop_thread_id = threading.get_ident()
    return hass


def _attach_to_hass(sensor, hass, entity_id="sensor.test_task"):
    """Wire up a sensor enough to call async_write_ha_state() like a real platform would."""
    sensor.hass = hass
    sensor.entity_id = entity_id
    sensor.platform = MagicMock()

# --- TEST SLIDING LOGIC ---
async def test_sliding_task_logic(mock_hass, mock_now):
    """Test that sliding tasks calculate due dates relative to last_done."""
    config = {
        CONF_NAME: "Sliding Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7, # 7 Days
        CONF_ICON: DEFAULT_ICON
    }
    
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)

        # 1. New Sensor (Never done) -> Due in 7 days from NOW
        sensor._update_state()
        expected_due = mock_now + timedelta(days=7)
        assert sensor.extra_state_attributes["next_due"] == expected_due.isoformat()
        assert "Due in 7 days" in sensor.native_value

        # 2. Mark as done TODAY
        await sensor.mark_as_done()

        # Logic: Last Done (Today) + 7 Days, preserving the time-of-day it
        # was actually completed at (no explicit CONF_TIME was configured,
        # so the config flow's midnight default is treated as "no override").
        expected_next = mock_now + timedelta(days=7)
        assert sensor._last_done == mock_now
        assert sensor.extra_state_attributes["next_due"] == expected_next.isoformat()

async def test_sliding_task_with_explicit_time(mock_hass, mock_now):
    """A sliding task with a real (non-midnight) configured time still applies it."""
    config = {
        CONF_NAME: "Sliding Task With Time",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_SCHEDULE: {CONF_TIME: datetime.strptime("18:00", "%H:%M").time()},
        CONF_ICON: DEFAULT_ICON
    }

    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        await sensor.mark_as_done()

        expected_next = (mock_now + timedelta(days=7)).replace(hour=18, minute=0, second=0, microsecond=0)
        assert sensor._last_done == mock_now
        assert sensor.extra_state_attributes["next_due"] == expected_next.isoformat()

async def test_sliding_task_never_done_with_explicit_time(mock_hass, mock_now):
    """A never-done sliding task with a real configured time applies it too,
    not just the already-done path."""
    config = {
        CONF_NAME: "Fresh Sliding Task With Time",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_SCHEDULE: {CONF_TIME: datetime.strptime("18:00", "%H:%M").time()},
        CONF_ICON: DEFAULT_ICON
    }

    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._update_state()

        expected_due = (mock_now + timedelta(days=7)).replace(hour=18, minute=0, second=0, microsecond=0)
        assert sensor._next_due == expected_due

async def test_mark_as_done_fires_completed_event(mock_hass, mock_now):
    """mark_as_done() fires a task_tracker_task_completed event with the entity_id."""
    config = {
        CONF_NAME: "Event Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_ICON: DEFAULT_ICON
    }

    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass, entity_id="sensor.event_task")
        # Never done before, so it was due 7 days from creation/first update.
        sensor._update_state()
        due_before_completion = sensor._next_due

        await sensor.mark_as_done()

        mock_hass.bus.fire.assert_called_once_with(
            "task_tracker_task_completed",
            {
                "entity_id": "sensor.event_task",
                "name": "Event Task",
                "last_done": mock_now.isoformat(),
                "due": due_before_completion.isoformat(),
                "next_due": sensor._next_due.isoformat(),
            },
        )

async def test_due_today_to_overdue_transition_also_fires_event(mock_hass, mock_now):
    """"Due Today" -> "Overdue" is a real, distinct transition (a task
    that was merely due today has now actually been missed) and must
    fire task_tracker_task_due too, not just the very first not-due ->
    due crossing. Regression test: the old fire condition only checked
    whether the *old* state was entirely outside the due bucket, so once
    a task was caught as "Due Today" it could never fire again later the
    same cycle when it actually became "Overdue"."""
    config = {
        CONF_NAME: "Slipping Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 1,
        CONF_SCHEDULE: {CONF_TIME: datetime.strptime("18:00", "%H:%M").time()},
        CONF_ICON: DEFAULT_ICON
    }
    sensor = TaskSensor(config)
    _attach_to_hass(sensor, mock_hass, entity_id="sensor.slipping_task")
    sensor._last_done = mock_now - timedelta(days=1)  # next_due = today 18:00, stable

    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor._update_state()
    assert sensor.native_value == "Due Today"
    mock_hass.bus.fire.assert_not_called()  # first-ever calculation, from "Unknown"

    later_same_day = mock_now.replace(hour=19, minute=0, second=0, microsecond=0)
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=later_same_day):
        sensor._update_state()

    assert sensor.native_value == "Overdue"
    mock_hass.bus.fire.assert_called_once_with(
        "task_tracker_task_due",
        {
            "entity_id": "sensor.slipping_task",
            "name": "Slipping Task",
            "state": "Overdue",
            "next_due": sensor._next_due.isoformat(),
        },
    )

async def test_history_records_due_datetime(mock_hass, mock_now):
    """mark_as_done() records the due datetime this completion satisfied,
    so dashboards can show it alongside the completion date/time."""
    config = {
        CONF_NAME: "History Due Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_ICON: DEFAULT_ICON
    }

    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._update_state()
        due_before_completion = sensor._next_due

        await sensor.mark_as_done()

        history = sensor.extra_state_attributes["history"]
        assert len(history) == 1
        assert history[0]["done"] == mock_now.isoformat()
        assert history[0]["due"] == due_before_completion.isoformat()

async def test_mark_as_done_rejects_unparseable_last_done(mock_hass, mock_now):
    """An unparseable last_done string must be rejected up front instead of
    landing in history as None, which would break sorting and attribute
    serialization on every later update (including a valid completion)."""
    config = {
        CONF_NAME: "Bad Input Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_ICON: DEFAULT_ICON
    }

    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._update_state()

        with pytest.raises(ServiceValidationError):
            await sensor.mark_as_done(last_done="not-a-real-datetime")

        # The bad call must not have mutated history/last_done at all.
        assert sensor._history == []
        assert sensor._last_done is None

        # A subsequent valid completion still works normally.
        await sensor.mark_as_done()
        assert sensor._last_done == mock_now
        assert sensor.extra_state_attributes["history"][0]["done"] == mock_now.isoformat()

def test_parse_history_handles_legacy_and_current_formats():
    """_parse_history() upgrades old flat-string history (no due info)
    alongside the current {"done", "due"} dict format, so existing tasks'
    history isn't lost after this integration update."""
    legacy_done = datetime(2024, 1, 1, 8, 0, 0, tzinfo=dt_util.UTC)
    current_done = datetime(2024, 1, 8, 8, 0, 0, tzinfo=dt_util.UTC)
    current_due = datetime(2024, 1, 8, 0, 0, 0, tzinfo=dt_util.UTC)

    raw_history = [
        legacy_done.isoformat(),
        {"done": current_done.isoformat(), "due": current_due.isoformat()},
        {"done": current_done.isoformat(), "due": None},
    ]

    parsed = TaskSensor._parse_history(raw_history)

    assert parsed == [
        {"done": legacy_done, "due": None},
        {"done": current_done, "due": current_due},
        {"done": current_done, "due": None},
    ]

# --- TEST FIXED SCHEDULE LOGIC ---
async def test_fixed_schedule_logic(mock_hass, mock_now):
    """Test that fixed tasks stick to specific days (e.g. Wednesday)."""
    # mock_now is Monday, Jan 1st 2024
    
    config = {
        CONF_NAME: "Fixed Task",
        CONF_TYPE: TYPE_FIXED,
        CONF_SCHEDULE: {
            CONF_DAYS: ["wed"], # Next Wed is Jan 3rd
            CONF_TIME: datetime.strptime("09:00", "%H:%M").time()
        },
        CONF_ICON: DEFAULT_ICON
    }

    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)

        # 1. Initial State -> Should find next Wednesday
        sensor._update_state()

        expected_due = datetime(2024, 1, 3, 9, 0, 0, tzinfo=dt_util.UTC) # Wed Jan 3rd
        assert sensor._next_due == expected_due
        assert "Due in 2 days" in sensor.native_value # Mon -> Wed is 2 days

        # 2. Simulate User completing it EARLY (on Tuesday Jan 2nd)
        tuesday = datetime(2024, 1, 2, 10, 0, 0, tzinfo=dt_util.UTC)
        with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=tuesday):
            await sensor.mark_as_done()

            # 3. Next due date should NOT shift. It should still be looking for Wednesdays.
            # Since we just did it Jan 2nd, the next Wed is Jan 3rd (Tomorrow).
            # Wait, standard logic: It looks for next occurrence AFTER last_done.
            # Last Done = Jan 2nd. Next Wed = Jan 3rd.

            expected_next = datetime(2024, 1, 3, 9, 0, 0, tzinfo=dt_util.UTC)
            assert sensor._next_due == expected_next
            assert "Due in 1 day" in sensor.native_value

# --- TEST PREDICTIVE LOGIC ---
async def test_predictive_logic(mock_hass, mock_now):
    """Test that history is averaged to find the next date."""
    config = {
        CONF_NAME: "Predictive Task",
        CONF_TYPE: TYPE_PREDICTIVE,
        CONF_INTERVAL: 10, # Initial guess
        CONF_ICON: DEFAULT_ICON
    }

    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        sensor.hass = mock_hass
        
        # Inject Fake History: Done 10 days ago, and 20 days ago.
        # Interval is exactly 10 days.
        day_1 = mock_now - timedelta(days=20)
        day_2 = mock_now - timedelta(days=10)
        
        sensor._history = [{"done": day_1, "due": None}, {"done": day_2, "due": None}]
        sensor._last_done = day_2 # Most recent
        
        sensor._update_state()
        
        # Prediction: Last Done (day_2) + Average (10 days) = Today (mock_now)
        expected_due = day_2 + timedelta(days=10)
        
        assert sensor._next_due == expected_due
        assert sensor.native_value == "Due Today"

# --- TEST METADATA ---
async def test_metadata_attributes(mock_hass):
    """Ensure tags and notify_entity are passed to attributes."""
    config = {
        CONF_NAME: "Meta Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 1,
        CONF_ICON: DEFAULT_ICON,
        CONF_TAGS: ["chores", "kitchen"],
        CONF_NOTIFY_ENTITY: "notify.mobile_app_kedars_phone"
    }

    sensor = TaskSensor(config)
    attrs = sensor.extra_state_attributes

    assert attrs["tags"] == ["chores", "kitchen"]
    assert attrs["notify_entity"] == "notify.mobile_app_kedars_phone"

# --- TEST SNOOZE LOGIC ---
async def test_snooze_task_logic(mock_hass, mock_now):
    """Test that snoozing updates state but preserves next_due."""
    config = {
        CONF_NAME: "Snooze Test Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_ICON: DEFAULT_ICON
    }
    
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._update_state()

        expected_due = mock_now + timedelta(days=7)
        assert sensor._next_due == expected_due
        assert "Due in 7 days" in sensor.native_value
        
        # Snooze for 2 days
        snooze_until = mock_now + timedelta(days=2)
        await sensor.snooze_task(snooze_until)
        
        # Check state is Snoozed, but next_due is unchanged!
        assert sensor.native_value == "Snoozed"
        assert sensor.extra_state_attributes["snoozed_until"] == snooze_until.isoformat()
        assert sensor.extra_state_attributes["next_due"] == expected_due.isoformat()
        
        # Unsnooze manually
        await sensor.unsnooze_task()
        assert "Due in 7 days" in sensor.native_value
        assert "snoozed_until" not in sensor.extra_state_attributes

# --- TEST SCHEDULE ATTRIBUTE EDGE CASES ---
async def test_fixed_schedule_attribute_daily_when_no_days_configured(mock_hass, mock_now):
    """A Fixed task with no specific days configured is "Daily", not tied
    to any particular weekday list."""
    config = {
        CONF_NAME: "Daily Fixed Task",
        CONF_TYPE: TYPE_FIXED,
        CONF_SCHEDULE: {
            CONF_DAYS: [],
            CONF_TIME: datetime.strptime("09:00", "%H:%M").time()
        },
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._update_state()
        assert sensor.extra_state_attributes["schedule"] == "Daily at 09:00 AM"

async def test_unknown_task_type_schedule_attribute_is_none(mock_hass):
    """A task with an unrecognized type (e.g. from a future/rolled-back
    config version) reports "None" instead of crashing on attribute access."""
    config = {
        CONF_NAME: "Weird Type Task",
        CONF_TYPE: "not_a_real_type",
        CONF_ICON: DEFAULT_ICON
    }
    sensor = TaskSensor(config)
    assert sensor.extra_state_attributes["schedule"] == "None"

# --- TEST NEVER-DONE FALLBACK/CORRECTION PATHS ---
async def test_fixed_never_done_corrects_when_todays_slot_already_passed(mock_hass, mock_now):
    """A never-done Fixed task whose scheduled time today has already
    passed skips to next week's occurrence instead of reporting a
    same-day due time that's actually already in the past."""
    # mock_now is Monday, Jan 1st 2024, 12:00:00 - after the 9 AM slot.
    config = {
        CONF_NAME: "Passed Slot Task",
        CONF_TYPE: TYPE_FIXED,
        CONF_SCHEDULE: {
            CONF_DAYS: ["mon"],
            CONF_TIME: datetime.strptime("09:00", "%H:%M").time()
        },
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._update_state()

        expected = (mock_now + timedelta(days=7)).replace(hour=9, minute=0, second=0, microsecond=0)
        assert sensor._next_due == expected

async def test_never_done_fixed_task_reaches_due_today_and_fires_event(mock_hass, mock_now):
    """A never-done Fixed task must still classify as "Due Today"/"Overdue"
    (not just "Due in N days") once its scheduled slot is today, and must
    fire task_tracker_task_due on that transition like an already-done task
    does. Regression test: the never-done branch used to always format
    "Due in N days" regardless of how close next_due actually was, so the
    event never fired for tasks that had never been marked done."""
    config = {
        CONF_NAME: "Fresh Fixed Task",
        CONF_TYPE: TYPE_FIXED,
        CONF_SCHEDULE: {CONF_TIME: datetime.strptime("18:00", "%H:%M").time()},
        CONF_ICON: DEFAULT_ICON
    }
    sensor = TaskSensor(config)
    _attach_to_hass(sensor, mock_hass, entity_id="sensor.fresh_fixed_task")

    # Day before, after the slot already passed for that day -> due tomorrow.
    yesterday_evening = mock_now - timedelta(days=1, hours=-8)  # Dec 31, 20:00
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=yesterday_evening):
        sensor._update_state()
    assert "Due in 1 day" in sensor.native_value
    mock_hass.bus.fire.assert_not_called()  # first-ever calculation, from "Unknown"

    # Same day, before the 18:00 slot -> should now read "Due Today" and fire.
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor._update_state()

    assert sensor.native_value == "Due Today"
    mock_hass.bus.fire.assert_called_once_with(
        "task_tracker_task_due",
        {
            "entity_id": "sensor.fresh_fixed_task",
            "name": "Fresh Fixed Task",
            "state": "Due Today",
            "next_due": sensor._next_due.isoformat(),
        },
    )

async def test_never_done_fixed_task_due_moment_does_not_roll_to_next_cycle(mock_hass, mock_now):
    """The scheduled point-in-time timer calls _update_state() again right
    at (or a hair after) the moment a never-done task becomes due. Before
    this fix, the never-done branch re-derived _next_due from "now" on
    every call; since "now" at that instant is already at-or-past today's
    slot, dtstart=now made the rrule skip straight to *tomorrow's*
    occurrence, so the task silently reverted from "Due Today" back to
    "Due in 1 day" and could never reach "Overdue" - a never-completed
    recurring task could never fire task_tracker_task_due at all, no
    matter how many timer cycles passed. _next_due must stay fixed once
    established until the task is actually completed."""
    config = {
        CONF_NAME: "Midnight Fixed Task",
        CONF_TYPE: TYPE_FIXED,
        CONF_SCHEDULE: {CONF_TIME: datetime.strptime("00:01", "%H:%M").time()},
        CONF_ICON: DEFAULT_ICON
    }
    sensor = TaskSensor(config)
    _attach_to_hass(sensor, mock_hass, entity_id="sensor.midnight_fixed_task")

    # Config reloaded/created shortly before the due slot -> due later today.
    just_before_due = mock_now.replace(hour=0, minute=0, second=20, microsecond=0)
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=just_before_due):
        sensor._update_state()
    due_at = sensor._next_due
    assert sensor.native_value == "Due Today"

    # The point-in-time timer fires right at (a hair past) that due moment.
    just_after_due = due_at + timedelta(microseconds=1)
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=just_after_due):
        sensor._update_state()

    assert sensor._next_due == due_at  # must not have jumped to tomorrow
    assert sensor.native_value == "Overdue"

async def test_never_done_fallback_to_one_day_when_no_schedule_or_interval(mock_hass, mock_now):
    """A never-done task with neither a fixed schedule nor an interval
    configured still gets a sane default (due tomorrow) instead of None."""
    config = {
        CONF_NAME: "No Schedule Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._update_state()
        assert sensor._next_due == mock_now + timedelta(days=1)

# --- TEST DONE-BRANCH FALLBACK PATHS ---
async def test_predictive_falls_back_to_interval_with_insufficient_history(mock_hass, mock_now):
    """A Predictive task with fewer than 2 history entries can't compute
    an average, so it falls back to the configured interval_days."""
    config = {
        CONF_NAME: "New Predictive Task",
        CONF_TYPE: TYPE_PREDICTIVE,
        CONF_INTERVAL: 10,
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        sensor.hass = mock_hass
        sensor._last_done = mock_now - timedelta(days=2)
        sensor._history = [{"done": sensor._last_done, "due": None}]

        sensor._update_state()
        assert sensor._next_due == sensor._last_done + timedelta(days=10)

async def test_sliding_done_fallback_to_one_day_when_no_interval(mock_hass, mock_now):
    """A completed Sliding task with no interval_days configured still
    gets a sane default (due tomorrow) instead of crashing."""
    config = {
        CONF_NAME: "No Interval Sliding Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._last_done = mock_now
        sensor._update_state()
        assert sensor._next_due == mock_now + timedelta(days=1)

async def test_fixed_done_normalizes_naive_last_done(mock_hass, mock_now):
    """A restored/legacy last_done value without tzinfo is normalized
    to the default timezone instead of crashing the rrule calculation."""
    config = {
        CONF_NAME: "Naive Last Done Fixed Task",
        CONF_TYPE: TYPE_FIXED,
        CONF_SCHEDULE: {
            CONF_DAYS: ["wed"],
            CONF_TIME: datetime.strptime("09:00", "%H:%M").time()
        },
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._last_done = datetime(2024, 1, 1, 8, 0, 0)  # naive, no tzinfo
        sensor._update_state()

        assert sensor._state != "Error"
        assert sensor._next_due.tzinfo is not None

# --- TEST "UNKNOWN"/"NEED MORE HISTORY" FALLBACK STATES ---
async def test_predictive_needs_more_history_state(mock_hass, mock_now):
    """A Predictive task with insufficient history AND no configured
    interval_days can't compute a next_due at all, so it reports
    "Need more history" rather than a bogus date."""
    config = {
        CONF_NAME: "Sparse Predictive Task",
        CONF_TYPE: TYPE_PREDICTIVE,
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._last_done = mock_now - timedelta(days=1)
        sensor._history = [{"done": sensor._last_done, "due": None}]
        sensor._update_state()

        assert sensor._state == "Need more history"
        assert sensor._next_due is None

async def test_fixed_done_unknown_state_when_no_schedule_or_interval(mock_hass, mock_now):
    """A completed Fixed task with neither a schedule nor interval_days
    configured can't compute a next_due, so it reports "Unknown"."""
    config = {
        CONF_NAME: "Broken Fixed Task",
        CONF_TYPE: TYPE_FIXED,
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._last_done = mock_now - timedelta(days=1)
        sensor._update_state()

        assert sensor._state == "Unknown"
        assert sensor._next_due is None

# --- TEST ERROR STATE ---
async def test_update_state_sets_error_on_exception(mock_hass, mock_now):
    """An unexpected exception while calculating state must be caught and
    surfaced as an Error state/icon rather than crashing the update."""
    config = {
        CONF_NAME: "Error Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        sensor._last_done = "not-a-datetime"  # corrupt internal state to force a crash
        sensor._update_state()

        assert sensor._state == "Error"
        assert sensor._icon == "mdi:alert"

# --- TEST mark_as_done WITH A DATETIME OBJECT (NOT A STRING) ---
async def test_mark_as_done_accepts_datetime_object_and_normalizes_naive_tz(mock_hass, mock_now):
    """mark_as_done() accepts a real datetime object directly (as called
    from Python code, not just a service's string argument), and
    normalizes a naive one to the default timezone."""
    config = {
        CONF_NAME: "Datetime Arg Task",
        CONF_TYPE: TYPE_SLIDING,
        CONF_INTERVAL: 7,
        CONF_ICON: DEFAULT_ICON
    }
    with patch("custom_components.task_tracker.sensor.dt_util.now", return_value=mock_now):
        sensor = TaskSensor(config)
        _attach_to_hass(sensor, mock_hass)
        naive_dt = datetime(2024, 1, 1, 9, 0, 0)  # no tzinfo, and not a string

        await sensor.mark_as_done(last_done=naive_dt)

        assert sensor._last_done == naive_dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
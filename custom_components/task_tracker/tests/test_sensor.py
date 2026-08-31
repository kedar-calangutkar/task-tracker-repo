"""Unit tests for the Task Tracker sensor platform."""
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from dateutil import rrule
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

        # Logic: Last Done (Today) + 7 Days. Sliding tasks always carry a
        # CONF_TIME from the config flow (defaulting to midnight when the
        # user hasn't set one), so the time-of-day is normalized rather
        # than inherited from when the task happened to be completed.
        expected_next = (mock_now + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        assert sensor._last_done == mock_now
        assert sensor.extra_state_attributes["next_due"] == expected_next.isoformat()

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
        
        sensor._history = [day_1, day_2]
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
"""Platform for sensor integration."""
from __future__ import annotations

from datetime import datetime, timedelta, time
import logging
import voluptuous as vol
from dateutil import rrule

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN, CONF_NAME, CONF_ICON, CONF_TYPE, CONF_INTERVAL,
    CONF_SCHEDULE, CONF_DAYS, CONF_TIME, CONF_TAGS, CONF_NOTIFY_ENTITY,
    TYPE_FIXED, TYPE_SLIDING, TYPE_PREDICTIVE
)

_LOGGER = logging.getLogger(__name__)

WEEKDAY_MAP = {
    "mon": rrule.MO, "tue": rrule.TU, "wed": rrule.WE,
    "thu": rrule.TH, "fri": rrule.FR, "sat": rrule.SA,
    "sun": rrule.SU
}

DAY_NAMES = {
    "mon": "Mondays", "tue": "Tuesdays", "wed": "Wednesdays",
    "thu": "Thursdays", "fri": "Fridays", "sat": "Saturdays",
    "sun": "Sundays"
}

from homeassistant.helpers import entity_platform

# ... (imports)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform from UI Config Entry."""
    config = {**entry.data, **entry.options}
    
    tags_raw = config.get(CONF_TAGS)
    tags = []
    if isinstance(tags_raw, list):
        tags = tags_raw
    elif isinstance(tags_raw, str) and tags_raw.strip():
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    
    notify_entity = config.get(CONF_NOTIFY_ENTITY)

    time_str = config.get(CONF_TIME)
    time_obj = time(0,0)
    if time_str:
        try:
            time_obj = datetime.strptime(time_str, "%H:%M:%S").time()
        except ValueError:
            pass 

    task_data = {
        CONF_NAME: config.get(CONF_NAME),
        CONF_ICON: config.get(CONF_ICON),
        CONF_TYPE: config.get(CONF_TYPE),
        CONF_INTERVAL: config.get(CONF_INTERVAL),
        CONF_TAGS: tags,
        CONF_NOTIFY_ENTITY: notify_entity,
        CONF_SCHEDULE: {
            CONF_TIME: time_obj,
            CONF_DAYS: config.get(CONF_DAYS, [])
        }
    }
    
    sensor = TaskSensor(task_data, unique_id=entry.entry_id)
    async_add_entities([sensor])
    
    # Register services
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "complete_task",
        {
            vol.Optional("last_done"): str,
        },
        "mark_as_done",
    )
    platform.async_register_entity_service(
        "reset_history",
        {},
        "reset_history",
    )
    platform.async_register_entity_service(
        "snooze_task",
        {
            vol.Required("until"): str,
        },
        "snooze_task",
    )
    platform.async_register_entity_service(
        "unsnooze_task",
        {},
        "unsnooze_task",
    )


class TaskSensor(SensorEntity, RestoreEntity):
    """Representation of a Task Tracker Sensor."""

    def __init__(self, task_config, unique_id=None):
        """Initialize the sensor."""
        self._attr_unique_id = unique_id
        self._name = task_config[CONF_NAME]
        self._calc_type = task_config[CONF_TYPE]
        self._icon_default = task_config[CONF_ICON]
        self._icon = self._icon_default
        
        self._interval_days = task_config.get(CONF_INTERVAL)
        self._schedule = task_config.get(CONF_SCHEDULE)
        self._tags = task_config.get(CONF_TAGS, [])
        self._notify_entity = task_config.get(CONF_NOTIFY_ENTITY)

        self._state = "Unknown"
        self._last_done = None
        self._next_due = None
        self._days_remaining = None
        self._snoozed_until = None
        self._history = [] 
        self._snooze_unsub = None
        self._due_unsub = None
        # Track when the sensor was first created in this session
        self._created_at = dt_util.now()

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._attr_unique_id)},
            "name": self._name,
            "manufacturer": "Task Tracker",
            "model": "Task Tracker",
        }

    @property
    def name(self):
        return self._name

    @property
    def native_value(self):
        return self._state

    @property
    def icon(self):
        return self._icon

    @property
    def extra_state_attributes(self):
        attributes = {
            "type": self._calc_type,
            "tags": self._tags,
            "notify_entity": self._notify_entity,
        }
        if self._calc_type == TYPE_FIXED and self._schedule:
            time_obj = self._schedule.get(CONF_TIME)
            days = self._schedule.get(CONF_DAYS, [])
            
            time_part = ""
            if time_obj and (time_obj.hour != 0 or time_obj.minute != 0):
                time_part = f" at {time_obj.strftime('%I:%M %p')}"
            
            if not days:
                schedule_str = f"Daily{time_part}"
            else:
                day_names = [DAY_NAMES.get(d, d) for d in days]
                schedule_str = f"Every {', '.join(day_names)}{time_part}"
            attributes["schedule"] = schedule_str
            
        elif self._calc_type == TYPE_PREDICTIVE and self._interval_days:
            attributes["schedule"] = f"Predictive (Average {int(self._interval_days)} days)"
            
        elif self._calc_type == TYPE_SLIDING:
            time_part = ""
            if self._schedule and self._schedule.get(CONF_TIME):
                time_obj = self._schedule.get(CONF_TIME)
                if time_obj.hour != 0 or time_obj.minute != 0:
                    time_part = f" at {time_obj.strftime('%I:%M %p')}"
            attributes["schedule"] = f"Every {int(self._interval_days)} days{time_part}"
            
        else:
            attributes["schedule"] = "None"
            
        if self._last_done:
            attributes["last_done"] = self._last_done.isoformat()
        if self._next_due:
            attributes["next_due"] = self._next_due.isoformat()
        if self._days_remaining is not None:
            attributes["days_remaining"] = self._days_remaining
        if self._snoozed_until:
            attributes["snoozed_until"] = self._snoozed_until.isoformat()
            
        if self._history:
            attributes["history"] = [
                {
                    "done": entry["done"].isoformat(),
                    "due": entry["due"].isoformat() if entry.get("due") else None,
                }
                for entry in self._history
            ]

        return attributes

    @staticmethod
    def _parse_history(raw_history):
        """Parse a restored history attribute into {"done", "due"} entries.

        Handles both the current format (a list of {"done", "due"} dicts)
        and the legacy format (a flat list of ISO completion timestamps,
        with no due info) so existing tasks' history survives an upgrade.
        """
        parsed_history = []
        for item in raw_history:
            if isinstance(item, dict):
                done = dt_util.parse_datetime(item.get("done"))
                due_raw = item.get("due")
                due = dt_util.parse_datetime(due_raw) if due_raw else None
            else:
                done = dt_util.parse_datetime(item)
                due = None
            if done:
                parsed_history.append({"done": done, "due": due})
        return parsed_history

    async def async_added_to_hass(self):
        """Restore state."""
        await super().async_added_to_hass()

        # Restore previous state
        last_state = await self.async_get_last_state()
        if last_state:
            if last_state.attributes.get("last_done"):
                try:
                    self._last_done = dt_util.parse_datetime(last_state.attributes["last_done"])
                except Exception:
                    pass

            if last_state.attributes.get("snoozed_until"):
                try:
                    self._snoozed_until = dt_util.parse_datetime(last_state.attributes["snoozed_until"])
                except Exception:
                    pass

            if last_state.attributes.get("history"):
                try:
                    self._history = self._parse_history(last_state.attributes["history"])
                except Exception:
                    pass
        
        self._update_state()
        self._schedule_snooze_expiration()

    async def async_will_remove_from_hass(self):
        """Clean up when entity is removed."""
        if self._snooze_unsub:
            self._snooze_unsub()
            self._snooze_unsub = None
        if self._due_unsub:
            self._due_unsub()
            self._due_unsub = None

    def _schedule_due_update(self):
        """Set a timer to update the sensor when it becomes due."""
        if self._due_unsub:
            self._due_unsub()
            self._due_unsub = None

        if self._next_due and self._next_due > dt_util.now():
            self._due_unsub = async_track_point_in_time(
                self.hass, self._async_due_reached, self._next_due
            )

    @callback
    def _async_due_reached(self, now):
        """Handle the exact moment a task becomes due."""
        self._due_unsub = None
        self._update_state()
        self.async_write_ha_state()

    def _schedule_snooze_expiration(self):
        """Set a timer to wake up the sensor when snooze expires."""
        if self._snooze_unsub:
            self._snooze_unsub()
            self._snooze_unsub = None

        if self._snoozed_until:
            now = dt_util.now()
            if self._snoozed_until > now:
                self._snooze_unsub = async_track_point_in_time(
                    self.hass, self._async_snooze_expired, self._snoozed_until
                )

    @callback
    def _async_snooze_expired(self, now):
        """Handle the exact moment a snooze expires."""
        self._snooze_unsub = None
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self):
        """Calculate next due date."""
        old_state = self._state
        try:
            now = dt_util.now()
            calculated_next = None

            # If the task has never been done (newly created or history reset),
            # make it due based on the interval/schedule from now, not overdue.
            if self._last_done is None:
                if self._calc_type == TYPE_FIXED and self._schedule:
                    # Respect the fixed weekday/time schedule instead of a flat "+1 day".
                    target_time = self._schedule.get(CONF_TIME) or time(0, 0)
                    days_list = self._schedule.get(CONF_DAYS) or []

                    freq = rrule.DAILY
                    byweekday = None
                    if days_list:
                        freq = rrule.WEEKLY
                        byweekday = [WEEKDAY_MAP[d] for d in days_list if d in WEEKDAY_MAP]

                    rule = rrule.rrule(freq, byweekday=byweekday, dtstart=now)
                    next_occurrence = rule.after(now, inc=True)
                    candidate = next_occurrence.replace(
                        hour=target_time.hour,
                        minute=target_time.minute,
                        second=0,
                        microsecond=0
                    )
                    if candidate < now:
                        next_occurrence = rule.after(now)
                        candidate = next_occurrence.replace(
                            hour=target_time.hour,
                            minute=target_time.minute,
                            second=0,
                            microsecond=0
                        )
                    self._next_due = candidate
                elif self._interval_days:
                    self._next_due = now + timedelta(days=self._interval_days)

                    # Same "midnight means no explicit override" rule as the
                    # already-done TYPE_SLIDING branch, so a never-done task
                    # with a real configured time isn't stuck inheriting
                    # whatever time it happened to be created/viewed at.
                    configured_time = None
                    if self._schedule and self._schedule.get(CONF_TIME):
                        configured_time = self._schedule.get(CONF_TIME)

                    if configured_time and configured_time != time(0, 0):
                        self._next_due = self._next_due.replace(
                            hour=configured_time.hour,
                            minute=configured_time.minute,
                            second=0,
                            microsecond=0
                        )
                else:
                    self._next_due = now + timedelta(days=1)

                delta = self._next_due - now
                self._days_remaining = delta.days + (1 if delta.seconds > 0 else 0)
                day_str = "day" if self._days_remaining == 1 else "days"
                self._state = f"Due in {self._days_remaining} {day_str}"
                self._icon = self._icon_default
            else:
                if self._calc_type == TYPE_PREDICTIVE:
                    if len(self._history) >= 2:
                        deltas = []
                        sorted_hist = sorted(entry["done"] for entry in self._history)
                        for i in range(1, len(sorted_hist)):
                            deltas.append(sorted_hist[i] - sorted_hist[i-1])
                        
                        if deltas:
                            avg_seconds = sum(d.total_seconds() for d in deltas) / len(deltas)
                            avg_interval = timedelta(seconds=avg_seconds)
                            calculated_next = self._last_done + avg_interval

                    if not calculated_next and self._interval_days:
                        calculated_next = self._last_done + timedelta(days=self._interval_days)

                elif self._calc_type == TYPE_SLIDING:
                    if self._interval_days:
                        # Base next due date, preserving the time-of-day the
                        # task was actually completed at.
                        next_date = self._last_done + timedelta(days=self._interval_days)

                        # Only override the time if the user explicitly chose
                        # one. The config flow defaults CONF_TIME to midnight
                        # when left untouched, so treat midnight as "no
                        # override" rather than silently snapping every
                        # sliding task's due time to 00:00.
                        configured_time = None
                        if self._schedule and self._schedule.get(CONF_TIME):
                            configured_time = self._schedule.get(CONF_TIME)

                        if configured_time and configured_time != time(0, 0):
                            calculated_next = next_date.replace(
                                hour=configured_time.hour,
                                minute=configured_time.minute,
                                second=0,
                                microsecond=0
                            )
                        else:
                            calculated_next = next_date
                    else:
                        calculated_next = self._last_done + timedelta(days=1)

                elif self._calc_type == TYPE_FIXED:
                    if self._schedule:
                        target_time = self._schedule.get(CONF_TIME) or time(0,0)
                        days_list = self._schedule.get(CONF_DAYS) or []
                        
                        freq = rrule.DAILY
                        byweekday = None
                        
                        if days_list:
                            freq = rrule.WEEKLY
                            parsed_days = []
                            for d in days_list:
                                if d in WEEKDAY_MAP:
                                    parsed_days.append(WEEKDAY_MAP[d])
                            byweekday = parsed_days

                        start_point = self._last_done
                        if start_point.tzinfo is None:
                            start_point = start_point.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)

                        rule = rrule.rrule(freq, byweekday=byweekday, dtstart=start_point)
                        next_occurrence = rule.after(start_point)
                        
                        if next_occurrence:
                            calculated_next = next_occurrence.replace(
                                hour=target_time.hour, 
                                minute=target_time.minute, 
                                second=0, 
                                microsecond=0,
                                tzinfo=start_point.tzinfo
                            )
                            # Ensure we aren't suggesting a time that is actually in the past 
                            # relative to the completion event.
                            if calculated_next <= start_point:
                                next_occurrence = rule.after(start_point + timedelta(days=1))
                                calculated_next = next_occurrence.replace(
                                    hour=target_time.hour, 
                                    minute=target_time.minute, 
                                    second=0, 
                                    microsecond=0,
                                    tzinfo=start_point.tzinfo
                                )
                    elif self._interval_days:
                        calculated_next = self._last_done + timedelta(days=self._interval_days)

                self._next_due = calculated_next
                
                if self._next_due:
                    delta = self._next_due - now
                    self._days_remaining = delta.days + (1 if delta.seconds > 0 else 0)
                    is_today = (self._next_due.date() == now.date())

                    if self._next_due < now:
                        self._state = "Overdue"
                        self._icon = self._icon_default
                    elif is_today:
                        self._state = "Due Today"
                        self._icon = self._icon_default
                    else:
                        day_str = "day" if self._days_remaining == 1 else "days"
                        self._state = f"Due in {self._days_remaining} {day_str}"
                        self._icon = self._icon_default
                else:
                    self._state = "Need more history" if self._calc_type == TYPE_PREDICTIVE else "Unknown"
                    self._days_remaining = None
                    self._icon = "mdi:help-circle-outline"

            # Check snooze state last, which can override 'Overdue'/'Due Today'
            if self._snoozed_until:
                if now < self._snoozed_until:
                    self._state = "Snoozed"
                    self._icon = "mdi:alarm-snooze"
                else:
                    self._snoozed_until = None
            
            self._check_and_fire_due_event(old_state)
            self._schedule_due_update()

        except Exception as e:
            _LOGGER.error(f"Error updating task {self._name}: {e}")
            self._state = "Error"
            self._icon = "mdi:alert"

    async def mark_as_done(self, last_done=None):
        """Action: Mark the task as complete."""
        if last_done:
            # Service calls pass arguments as strings, we need to parse them
            if isinstance(last_done, str):
                done_time = dt_util.parse_datetime(last_done)
            else:
                done_time = last_done

            if done_time is None:
                # Reject unparseable input here rather than letting a None
                # slip into history, where it would break sorting and
                # attribute serialization on every future update.
                raise ServiceValidationError(
                    f"Could not parse last_done value '{last_done}' as a datetime"
                )

            if done_time.tzinfo is None:
                done_time = done_time.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        else:
            done_time = dt_util.now()

        # Capture what was due before _update_state() recalculates it for
        # the next cycle, so history remembers what this completion satisfied.
        due_at_completion = self._next_due

        self._history.append({"done": done_time, "due": due_at_completion})
        self._history.sort(key=lambda entry: entry["done"])
        self._history = self._history[-10:]

        if self._history:
            self._last_done = self._history[-1]["done"]

        self._snoozed_until = None
        self._update_state()
        self._schedule_snooze_expiration()

        self.hass.bus.fire(f"{DOMAIN}_task_completed", {
            "entity_id": self.entity_id,
            "name": self._name,
            "last_done": done_time.isoformat(),
            "due": due_at_completion.isoformat() if due_at_completion else None,
            "next_due": self._next_due.isoformat() if self._next_due else None
        })

        self.async_write_ha_state()

    async def reset_history(self):
        """Action: Clear history and reset state."""
        self._history = []
        self._last_done = None
        self._snoozed_until = None
        # Set creation time to now so it becomes due immediately upon reset
        self._created_at = dt_util.now()
        self._update_state()
        self._schedule_snooze_expiration()
        self.async_write_ha_state()

    async def snooze_task(self, until=None):
        """Action: Snooze the task until a specific date."""
        if until:
            snooze_time = until
            if isinstance(snooze_time, str):
                snooze_time = dt_util.parse_datetime(snooze_time)
            
            if snooze_time and snooze_time.tzinfo is None:
                snooze_time = snooze_time.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            self._snoozed_until = snooze_time
            self._update_state()
            self._schedule_snooze_expiration()
            self.async_write_ha_state()

    async def unsnooze_task(self):
        """Action: Clear the snooze state."""
        self._snoozed_until = None
        self._update_state()
        self._schedule_snooze_expiration()
        self.async_write_ha_state()

    def _check_and_fire_due_event(self, old_state):
        # Don't fire event on initial load (Unknown -> Something)
        if old_state == "Unknown":
            return
            
        if self._state in ["Overdue", "Due Today"] and old_state not in ["Overdue", "Due Today"]:
            self.hass.bus.fire(f"{DOMAIN}_task_due", {
                "entity_id": self.entity_id,
                "name": self._name,
                "state": self._state,
                "next_due": self._next_due.isoformat() if self._next_due else None
            })
"""Platform for sensor integration."""
from __future__ import annotations

from datetime import datetime, timedelta, time
import logging
from dateutil import rrule

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN, CONF_NAME, CONF_ICON, CONF_TYPE, CONF_INTERVAL,
    CONF_SCHEDULE, CONF_DAYS, CONF_TIME, CONF_TAGS, CONF_ASSIGNEES,
    TYPE_FIXED, TYPE_SLIDING, TYPE_PREDICTIVE
)

_LOGGER = logging.getLogger(__name__)

WEEKDAY_MAP = {
    "mon": rrule.MO, "tue": rrule.TU, "wed": rrule.WE,
    "thu": rrule.TH, "fri": rrule.FR, "sat": rrule.SA,
    "sun": rrule.SU
}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform from UI Config Entry."""
    # MERGE: Data (Initial) + Options (Edits)
    # Options take priority.
    config = {**entry.data, **entry.options}
    
    tags_raw = config.get(CONF_TAGS)
    tags = []
    if isinstance(tags_raw, list):
        tags = tags_raw
    elif isinstance(tags_raw, str) and tags_raw.strip():
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    
    assignees_raw = config.get(CONF_ASSIGNEES)
    assignees = []
    if isinstance(assignees_raw, list):
        assignees = assignees_raw
    elif isinstance(assignees_raw, str) and assignees_raw.strip():
        assignees = [a.strip() for a in assignees_raw.split(",") if a.strip()]
        
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
        CONF_ASSIGNEES: assignees,
        CONF_SCHEDULE: {
            CONF_TIME: time_obj,
            CONF_DAYS: config.get(CONF_DAYS, [])
        }
    }
    
    sensor = TaskSensor(task_data, unique_id=entry.entry_id)
    async_add_entities([sensor])


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
        self._assignee_ids = task_config.get(CONF_ASSIGNEES, [])
        self._assignee_names = [] 
        
        self._state = "Unknown"
        self._last_done = None
        self._next_due = None
        self._days_remaining = None
        self._history = [] 

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
            "assignees": self._assignee_names,
            "assignee_ids": self._assignee_ids,
        }
        if self._last_done:
            attributes["last_done"] = self._last_done.isoformat()
        if self._next_due:
            attributes["next_due"] = self._next_due.isoformat()
        if self._days_remaining is not None:
            attributes["days_remaining"] = self._days_remaining
        
        if self._history:
            attributes["history"] = [d.isoformat() for d in self._history]
            
        return attributes

    async def async_added_to_hass(self):
        """Restore state and resolve names."""
        await super().async_added_to_hass()
        
        self._assignee_names = []
        if self._assignee_ids:
            person_map = {}
            persons = self.hass.states.async_all("person")
            for person in persons:
                uid = person.attributes.get("user_id")
                if uid:
                    person_map[uid] = person.attributes.get("friendly_name", person.name)

            for user_id in self._assignee_ids:
                if user_id in person_map:
                    self._assignee_names.append(person_map[user_id])
                else:
                    user = self.hass.auth.get_user(user_id)
                    if user:
                        self._assignee_names.append(user.name)
                    else:
                        self._assignee_names.append(user_id)

        last_state = await self.async_get_last_state()
        if last_state:
            if last_state.attributes.get("last_done"):
                try:
                    self._last_done = dt_util.parse_datetime(last_state.attributes["last_done"])
                except Exception:
                    pass

            if last_state.attributes.get("history"):
                try:
                    raw_history = last_state.attributes["history"]
                    self._history = [dt_util.parse_datetime(d) for d in raw_history if dt_util.parse_datetime(d)]
                except Exception:
                    pass
        
        self._update_state()

    def _update_state(self):
        """Calculate next due date."""
        try:
            now = dt_util.now()
            calculated_next = None

            if self._calc_type == TYPE_PREDICTIVE:
                if len(self._history) >= 2:
                    deltas = []
                    sorted_hist = sorted(self._history)
                    for i in range(1, len(sorted_hist)):
                        deltas.append(sorted_hist[i] - sorted_hist[i-1])
                    
                    if deltas:
                        avg_seconds = sum(d.total_seconds() for d in deltas) / len(deltas)
                        avg_interval = timedelta(seconds=avg_seconds)
                        if self._last_done:
                            calculated_next = self._last_done + avg_interval

                if not calculated_next and self._interval_days:
                     if self._last_done:
                         calculated_next = self._last_done + timedelta(days=self._interval_days)
                     else:
                         calculated_next = now + timedelta(days=self._interval_days)

            elif self._calc_type == TYPE_SLIDING:
                if self._last_done and self._interval_days:
                    calculated_next = self._last_done + timedelta(days=self._interval_days)
                else:
                    days = self._interval_days if self._interval_days else 1
                    calculated_next = now + timedelta(days=days)

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

                    start_point = self._last_done if self._last_done else now
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
                    if self._last_done:
                         calculated_next = self._last_done + timedelta(days=self._interval_days)
                    else:
                         calculated_next = now + timedelta(days=self._interval_days)

            self._next_due = calculated_next
            
            if self._next_due:
                delta = self._next_due - now
                self._days_remaining = delta.days + (1 if delta.seconds > 0 else 0)
                is_today = (self._next_due.date() == now.date())

                if self._next_due < now:
                    self._state = "Overdue"
                    self._icon = "mdi:alert-circle"
                elif is_today:
                    self._state = "Due Today"
                    self._icon = "mdi:calendar-today"
                else:
                    self._state = f"Due in {self._days_remaining} days"
                    self._icon = self._icon_default
            else:
                self._state = "Need more history" if self._calc_type == TYPE_PREDICTIVE else "Unknown"
                self._days_remaining = None
                self._icon = "mdi:help-circle-outline"

        except Exception as e:
            _LOGGER.error(f"Error updating task {self._name}: {e}")
            self._state = "Error"
            self._icon = "mdi:alert"

    async def mark_as_done(self, custom_date=None):
        """Action: Mark the task as complete, optionally with a specific date."""
        if custom_date:
            done_time = custom_date
            if done_time.tzinfo is None:
                done_time = done_time.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        else:
            done_time = dt_util.now()
            
        self._history.append(done_time)
        self._history.sort()
        self._history = self._history[-10:]
        
        if self._history:
            self._last_done = self._history[-1]
            
        self._update_state()
        self.async_write_ha_state()

    async def reset_history(self):
        """Action: Clear history and reset state."""
        self._history = []
        self._last_done = None
        self._update_state()
        self.async_write_ha_state()
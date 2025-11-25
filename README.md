Task Tracker
A smart custom integration for Home Assistant to track recurring chores, maintenance tasks, and personal habits.
Unlike standard calendar events, Task Tracker calculates due dates based on when you actually completed the task, offering "sliding" intervals, strict schedules, and AI-lite predictive scheduling.
✨ Features
 * Three Smart Logic Modes:
   * 🗓️ Fixed: Strict schedule (e.g., "Every Sunday at 9 PM"). If you miss it, it stays overdue.
   * 🔄 Sliding: Resets the timer after completion (e.g., "Change HVAC filter 90 days after I last did it").
   * 🔮 Predictive: Learns from your habits. Averages your last 10 completions to predict the next due date.
 * Rich Metadata:
   * Assignees: Link tasks to real Home Assistant Users/Persons.
   * Tags: Organize with tags like health, chore, or garden.
 * Audit History: Keeps a persistent log of the last 10 completion timestamps.
 * Time Travel: Support for backdating tasks (e.g., "I did this yesterday") via service calls.
 * Fully UI Configurable: Add, Edit, and Manage tasks directly from Settings. No YAML required.
🧠 Logic Flow
graph TD
    Start([Task Completed]) --> CheckType{Check Type}
    
    CheckType -->|Fixed| Fixed[Get Scheduled Day/Time]
    Fixed --> F_Calc[Find NEXT occurrence strictly after Now]
    F_Calc --> Save
    
    CheckType -->|Sliding| Sliding[Get Interval X Days]
    Sliding --> S_Calc[Next Due = Date Done + X Days]
    S_Calc --> Save
    
    CheckType -->|Predictive| Pred{History > 2?}
    Pred -->|No| P_Guess[Use Initial Guess Interval]
    Pred -->|Yes| P_Avg[Calculate Average Interval of History]
    P_Avg --> P_Calc[Next Due = Date Done + Average]
    P_Guess --> Save
    P_Calc --> Save
    
    Save([Save 'Next Due' to State])

📂 Installation
Method 1: Manual Installation
 * Download this repository.
 * Copy the task_tracker folder into your Home Assistant config/custom_components/ directory.
 * Restart Home Assistant.
Method 2: HACS (Custom Repository)
 * Open HACS > Integrations.
 * Click the 3 dots (top right) > Custom Repositories.
 * Add the URL to this repository and select Integration.
 * Click Download.
 * Restart Home Assistant.
⚙️ Configuration
Note: This integration does not use YAML configuration.
 * Go to Settings > Devices & Services.
 * Click + Add Integration.
 * Search for Task Tracker.
 * Step 1: Enter the Task Name and select the Logic Type.
 * Step 2: Configure the details:
   * Interval/Schedule: Set days, times, or intervals based on the type.
   * Icon: Use the visual picker to find the perfect icon.
   * Assignees: Select household members (linked to HA Person entities).
   * Tags: Select existing tags or type a new one to create it.
Editing Tasks
To change a schedule or rename a task, simply click the Configure button on the integration entry list. Changes apply immediately.
🛠️ Services
task_tracker.complete_task
Marks a task as done.
Arguments:
 * last_done (Optional): Specify a date/time in the past if you forgot to log it earlier.
<!-- end list -->
action: task_tracker.complete_task
target:
  entity_id: sensor.change_hvac_filter
data:
  last_done: "2023-10-25 14:00:00"

task_tracker.reset_history
Wipes the audit log and resets the "Last Done" date. Useful if you made a mistake or want to restart the Predictive logic learning.
action: task_tracker.reset_history
target:
  entity_id: sensor.cut_nails

📱 Dashboard Examples
1. The "Mark Done" Button (Tile Card)
The cleanest way to interact with tasks.
type: tile
entity: sensor.take_out_trash
name: Take Out Trash
features:
  - type: button
    name: Mark Done
    icon: mdi:check
    tap_action:
      action: perform-action
      perform_action: task_tracker.complete_task
      target:
        entity_id: sensor.take_out_trash

2. "My Tasks" List (Auto-Entities)
Automatically shows tasks assigned to the current user.
Requires Auto-Entities from HACS.
type: custom:auto-entities
card:
  type: entities
  title: 👤 My Tasks
filter:
  include:
    - domain: sensor
      integration: task_tracker
      attributes:
        assignees: "Me"  # Matches the Friendly Name of your user
      options:
        secondary_info: last-updated

3. Detailed History View (Markdown)
Show the audit log of the last 10 times the task was completed.
type: markdown
content: >
  ## {{ state_attr('sensor.change_hvac_filter', 'friendly_name') }}
  
  **Status:** {{ states('sensor.change_hvac_filter') }}
  **Next Due:** {{ as_timestamp(state_attr('sensor.change_hvac_filter', 'next_due')) | timestamp_custom('%A, %b %d') }}
  
  ### 📜 History Log
  {% if state_attr('sensor.change_hvac_filter', 'history') %}
  {% for entry in state_attr('sensor.change_hvac_filter', 'history') | reverse %}
  - {{ as_timestamp(entry) | timestamp_custom('%b %d, %Y at %H:%M') }}
  {% endfor %}
  {% else %}
  No history yet.
  {% endif %}



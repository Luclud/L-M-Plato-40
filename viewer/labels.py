"""Plain-language names and Material icons for every code the pipeline emits.

The only place where viewer wording lives. ``label(kind, code)`` never crashes: unknown codes fall back to the code.
"""
from __future__ import annotations

import re

APP_NAME = "Steward"  # product name (team decision Fri 13 Nov); never PLATO / CLARA
APP_TAGLINE = "Runs the building, answers to people"
TZ = "Europe/Vienna"

ISSUE_TYPES = {
    "water_leak": ("Water leak", ":material/water_drop:"),
    "heating_comfort": ("Heating & comfort", ":material/thermostat:"),
    "ventilation_maintenance": ("Ventilation filter", ":material/air:"),
    "room_turnaround": ("Room turnaround", ":material/bed:"),
    "occupancy_surge": ("Busy weekend", ":material/trending_up:"),
    "guest_request": ("Guest request", ":material/chat:"),
    "energy_optimization": ("Energy plan", ":material/bolt:"),
    "logistics": ("Supplies & waste", ":material/local_shipping:"),
    "data_quality": ("Data issue", ":material/sensors_off:"),
}
INCIDENT_TYPES = ["room_turnaround", "water_leak", "heating_comfort", "ventilation_maintenance", "occupancy_surge"]
ROUTINE_TYPES = ["guest_request", "energy_optimization", "logistics", "data_quality"]

ACTIONS = {
    "flag_anomaly": ("Flagged a problem", ":material/flag:"),
    "request_inspection": ("Asked for an inspection", ":material/search:"),
    "dispatch_technician": ("Sent a technician", ":material/engineering:"),
    "dispatch_robot": ("Sent a cleaning robot", ":material/smart_toy:"),
    "schedule_cleaning": ("Scheduled a cleaning", ":material/cleaning_services:"),
    "release_room": ("Released the room for the guest", ":material/key:"),
    "block_room": ("Blocked the room", ":material/block:"),
    "reassign_room": ("Moved the guest to another room", ":material/swap_horiz:"),
    "notify_guest": ("Messaged the guest", ":material/mail:"),
    "adjust_room_setpoint": ("Adjusted the room temperature", ":material/thermostat:"),
    "water_shutoff": ("Shut off the water", ":material/valve:"),
    "optimize_energy": ("Planned battery and energy use", ":material/battery_charging_full:"),
    "order_service": ("Ordered a service", ":material/local_shipping:"),
    "call_oncall_housekeeper": ("Called the on-call housekeeper", ":material/call:"),
    "request_human_approval": ("Asked a manager to decide", ":material/how_to_reg:"),
    "defer_action": ("Postponed an action", ":material/schedule:"),
}

AUTONOMY = {  # (label, colour, icon)
    "autonomous": ("Done automatically", "green", ":material/check_circle:"),
    "human_approval": ("Needs approval", "orange", ":material/pending:"),
    "approval_and_consent": ("Needs approval + guest consent", "red", ":material/gpp_maybe:"),
}
PRIORITIES = {  # (label, colour)
    "critical": ("Critical", "red"),
    "high": ("High", "orange"),
    "medium": ("Medium", "blue"),
    "low": ("Low", "gray"),
}
RESOURCES = {
    "CLEANER_1": "Cleaner 1", "CLEANER_2": "Cleaner 2", "CLEANER_3": "Cleaner 3",
    "TECH_1": "Technician", "ROBOT_1": "Robot 1", "ROBOT_2": "Robot 2", "ONCALL_1": "On-call housekeeper",
}
ASSETS = {
    "HP1": "Heat pump", "AHU1": "Ventilation unit", "PV1": "Solar panels", "BAT1": "Battery",
    "WM_MAIN": "Main water meter", "BUILDING": "Whole building", "VEND1": "Vending machine",
    "WASTE_MAIN": "Main waste container", "ACS1": "Door access system", "LIFT1": "Lift", "GRID": "Grid connection",
}
ROLES = {"cleaner": "Cleaner", "technician": "Technician", "robot": "Robot", "oncall": "On-call housekeeper",
         "on_call": "On-call housekeeper", "on_call_housekeeping": "On-call housekeeper"}

# honesty labels - never remove
PROPOSED = "Proposed message — not sent"
SIMULATED = "Simulated"
MOCK = "Mock approval — nothing is executed"
CONSENT_NEEDED = "Guest consent needed — not granted"
AI_SUMMARY = "AI summary — facts from the decision log"
AI_ANSWER = "AI answer from the decision log — check the cited decisions"
OFFLINE_ANSWER = "Looked up in the decision log without an AI model — check the cited decisions"
CACHED_ANSWER = "Cached example answer"
ASK_INTRO = ("Ask about this run — a room, a date, approvals or incident types. Answers come only from the decision "
             "log, with numbered sources; the copilot cannot approve, decide or change anything.")
ASK_NO_KEY = ("The AI copilot is off (no key configured). Suggested questions show cached examples; typed questions "
              "are looked up in the decision log without an AI model.")
SCHEDULE_KNOWN_ONLY = "Only what was known at the selected time"
SCHEDULE_CHANGES = "Show plan changes"
SCHEDULE_DAY_PLAN = "The day's plan"
SCHEDULE_OPEN_PAGE = "Open the full day on Housekeeping"
SCHEDULE_TURNOVERS = "Today's turnovers"
SCHEDULE_EMPTY = "No shifts or cleaning data for this day."
SCHEDULE_LEGEND = ("Light bar = shift · red hatched = absence (▼ = when it became known) · filled = actual cleaning "
                   "(green) / robot run (blue) / on-call (brown) · outlined = planned · red dashed line = re-plan · "
                   "green line = selected time")
SCHEDULE_MARKS = {"checkin": "Check-in time", "early_checkin": "Early check-in requested", "waiting": "Guest waiting",
                  "released": "Room released", "checkout": "Checked out"}
LANG_FLAGS = {"de": "🇩🇪", "en": "🇬🇧", "it": "🇮🇹", "fr": "🇫🇷", "es": "🇪🇸", "nl": "🇳🇱", "pl": "🇵🇱", "cs": "🇨🇿"}

_PATTERNS = [
    (re.compile(r"^R(\d{3})$"), lambda m: f"Room {m.group(1)}"),
    (re.compile(r"^WM_R(\d{3})$"), lambda m: f"Water meter Room {m.group(1)}"),
    (re.compile(r"^Z_R(\d{3})$"), lambda m: f"Heating zone Room {m.group(1)}"),
    (re.compile(r"^GW_F(\d)$"), lambda m: f"Floor {m.group(1)} gateway"),
    (re.compile(r"^WASTE_F(\d)$"), lambda m: f"Floor {m.group(1)} waste bin"),
    (re.compile(r"^(?:EV|EVSE)(\d*)$"), lambda m: f"EV charger {m.group(1)}".strip()),
]
_TABLES = {"issue": ISSUE_TYPES, "action": ACTIONS, "autonomy": AUTONOMY, "priority": PRIORITIES,
           "resource": RESOURCES, "asset": ASSETS, "role": ROLES}


def _name(v):
    return v[0] if isinstance(v, tuple) else v


def label(kind: str, code) -> str:
    """Friendly name for ``code`` of ``kind`` (issue/action/autonomy/priority/resource/asset/role); falls back to the code."""
    if code is None:
        return ""
    code = str(code)
    table = _TABLES.get(kind, {})
    if code in table:
        return _name(table[code])
    if kind in ("asset", "resource"):
        if code in RESOURCES:
            return RESOURCES[code]
        if code in ASSETS:
            return ASSETS[code]
        for rx, f in _PATTERNS:
            m = rx.match(code)
            if m:
                return f(m)
    return code


def icon(kind: str, code) -> str:
    v = _TABLES.get(kind, {}).get(str(code))
    if isinstance(v, tuple):
        return v[-1] if kind != "priority" else ""
    return ":material/info:" if kind in ("issue", "action") else ""


def colour(kind: str, code) -> str:
    v = _TABLES.get(kind, {}).get(str(code))
    return v[1] if isinstance(v, tuple) and kind in ("autonomy", "priority") else "gray"


def assets_text(codes) -> str:
    return ", ".join(label("asset", c) for c in (codes or []) if c != "BUILDING") or label("asset", "BUILDING")

ALARM_CODES = {  # building-alarm codes (events.jsonl bms_alarm) -> plain words
    "ROOM_TEMP_LOW": "Room too cold", "ROOM_TEMP_HIGH": "Room too warm", "SENSOR_COMM_LOSS": "Sensor offline",
    "FILTER_DP_HIGH": "Filter pressure high", "AIRFLOW_LOW": "Airflow low", "WATER_NIGHT_FLOW": "Water flow at night",
}
ALARMS_TITLE = "Alarms vs Steward"
ALARMS_INTRO = ("The building's alarm system raises an alarm for every threshold it crosses. Steward groups what it "
                "sees into incidents. Same year, same building (PLATO-40, fictional), both counted from this run.")
ALARMS_LINK = "See alarms vs Steward →"
ALARMS_LABELS_NOTE = {"hand": "Dev aid: hand labels for this dataset. Labels are not ground truth.",
                      "weak": "Dev aid: labels derived from repair work orders and guest waits. Labels are not "
                              "ground truth."}
ALARMS_HONEST = ("Alarms and labels are both imperfect evidence: an alarm can be right where no work order was "
                 "written, and Steward also misses things.")
ALARMS_EMPTY = "This dataset has no building alarms and no Steward incidents to compare."

# guest messages (T31)
GUESTS_TITLE = "Guest messages"
CHANNELS = {"app_chat": "App chat", "email": "Email", "phone": "Phone", "kiosk": "Kiosk"}
REQUEST_TYPES = {
    "early_checkin": "Early check-in", "late_checkout": "Late check-out", "temperature_hot": "Too warm",
    "temperature_cold": "Too cold", "noise_complaint": "Noise", "wifi_help": "WiFi help", "extra_towels": "Extra towels",
    "arrival_delay": "Arriving late", "parking": "Parking", "maintenance_issue": "Something broken",
    "cleaning_request": "Cleaning", "luggage_storage": "Luggage storage", "invoice": "Invoice",
    "access_issue": "Door / access", "waiting_for_room": "Waiting for the room", "air_quality": "Air quality",
    "water_issue": "Water problem",
}
LANGUAGES = {"de": "German", "en": "English", "it": "Italian", "fr": "French", "es": "Spanish", "nl": "Dutch",
             "pl": "Polish", "cs": "Czech"}
MESSAGE_SOURCES = {"template": "template wording", "llm": "AI wording", "ai": "AI wording"}
GUESTS_INTRO = ("What guests wrote, next to what Steward proposed to answer, with the reply deadline. Replies are "
                "proposals — nothing was sent to a guest.")
GUESTS_EMPTY = "This dataset has no guest requests."
GUESTS_NONE_MATCH = "No guest request matches these filters."
GUESTS_NO_REPLY = "No reply — Steward did not answer this request"
GUESTS_DEADLINE_UNKNOWN = "no deadline given"
CONSENT_MOCK = "Mock: simulates the guest's answer; consent is needed, not granted, until a real guest answers"
CONSENT_YES, CONSENT_NO = "Guest says yes", "Guest says no"

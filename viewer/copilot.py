""""Ask the building": read-only Q&A over one loaded run (decision log), grounded with citations.

The copilot only reads: every tool is a query over the decisions DataFrame; nothing can approve, decide or write.
With a key (``.env``: ``LLM_PROVIDER=anthropic``, ``LLM_API_KEY``, ``LLM_MODEL``) Claude answers via tool use
(HTTP, like ``plato40/llm.py``). Without one, a deterministic offline answerer handles simple questions (a room or
asset, a date + action, approvals, counts, incident types) and ``copilot_cache.json`` holds answers to the suggested
questions per run (``python viewer/copilot.py --build-cache --decisions out/qs.jsonl``).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plato40 import llm  # noqa: E402
from viewer import components as C  # noqa: E402
from viewer import core  # noqa: E402
from viewer import labels as L  # noqa: E402
from viewer.views.incident import humanize, title  # noqa: E402

_SHIFT = re.compile(r"\s*\(?(?:shift\s+)?\bSH\d{6}\b\)?", re.I)

CACHE = Path(__file__).with_name("copilot_cache.json")
MAX_TOOL_CALLS = 6
TIMEOUT_S = 30
LIST_LIMIT = 15
NOT_IN_LOG = "not in the decision log"
CITE = re.compile(r"\[((?:D\d{6}|[A-Z]{2}-\d{5})(?:\s*,\s*(?:D\d{6}|[A-Z]{2}-\d{5}))*)\]")
_SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], 1)}

SYSTEM = (
    "You answer questions about one replay of PLATO-40, a fictional 40-room hotel, using ONLY the results of the "
    "tools: the decision log of this run and the building data (rooms, sensors, plant, energy, staff roster, "
    "bookings, guest requests). Rules: never guess; if the tools do not contain the answer, say it is "
    f"'{NOT_IN_LOG}' or the building data. Cite every fact from a decision with its id in square brackets, e.g. "
    "[D000123]; facts from the building data need no citation, but always say the local time they refer to. "
    "Missing sensor data is unknown, never zero or normal. Only state or compare numbers exactly as a tool "
    "returned them (e.g. per-day values from 'by_day'); never estimate, split or derive a number yourself. For "
    "staff, bookings and anything later than now, say it is as known at now. Use the friendly "
    "names and local times the tools return; do not print other ids, UTC times or file names. Simulated effects "
    "are simulated (not measured); guest messages are proposed, never sent; approvals in this viewer are mock and "
    "execute nothing; guest consent needed means not granted. You cannot change, approve or decide anything. "
    "Answer in English, at most 6 short sentences or bullets. A chart of every measurement you look up is shown "
    "below your answer automatically; do not describe it point by point."
)

TOOL_SPECS = [
    {"name": "list_incidents", "description": "Incidents (one per episode), most severe first. All filters optional.",
     "input_schema": {"type": "object", "properties": {
         "type": {"type": "string", "description": "incident type, e.g. water_leak or 'Water leak'"},
         "asset": {"type": "string", "description": "room or asset, e.g. 'Room 104', 'R104', 'heat pump'"},
         "date_from": {"type": "string", "description": "local date YYYY-MM-DD (inclusive)"},
         "date_to": {"type": "string", "description": "local date YYYY-MM-DD (inclusive)"},
         "needs_approval": {"type": "boolean"},
         "include_routine": {"type": "boolean", "description": "also guest requests, energy, supplies, data issues"}}}},
    {"name": "get_incident", "description": "One incident with all its decisions in order.",
     "input_schema": {"type": "object", "properties": {"episode_id": {"type": "string"}}, "required": ["episode_id"]}},
    {"name": "get_decision", "description": "One decision in full (reason, evidence, plan, message, approval).",
     "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]}},
    {"name": "get_evidence", "description": "Evidence details and verification plans of one incident.",
     "input_schema": {"type": "object", "properties": {"episode_id": {"type": "string"}}, "required": ["episode_id"]}},
    {"name": "count_by_type", "description": "Number of incidents/episodes and decisions per type.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "pending_approvals", "description": "Incidents with decisions still waiting for a (mock) approval.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "room_status", "description": "One room at one moment: housekeeping state, temperature vs setpoint, "
     "humidity, CO2, window, water use, current or next booking, open incidents.",
     "input_schema": {"type": "object", "properties": {
         "room": {"type": "string", "description": "e.g. 'Room 104' or 'R104'"},
         "time": {"type": "string", "description": "local 'YYYY-MM-DD HH:MM'; omit for now"}}, "required": ["room"]}},
    {"name": "building_now", "description": "The whole building at one moment: room states, rooms that need "
     "cleaning, open incidents, plant status, staff on shift, power, solar, outdoor temperature.",
     "input_schema": {"type": "object", "properties": {
         "time": {"type": "string", "description": "local 'YYYY-MM-DD HH:MM'; omit for now"}}}},
    {"name": "measurements", "description": "Min, mean, max and latest value of measurements over a period, with a "
     "chart, up to now. Without dates: today. Subjects: a room ('Room 104': temperature, humidity, co2, water), 'building' (power, solar, grid, "
     "battery), 'outdoor', 'heat pump' (heat, electric, cop, supply), 'ventilation' (filter, airflow, fan, supply), "
     "'water main', 'vending' (drinks, snacks), 'waste' (fill levels).",
     "input_schema": {"type": "object", "properties": {
         "subject": {"type": "string"},
         "metric": {"type": "string", "description": "optional, e.g. 'temperature', 'cop', 'filter'"},
         "date_from": {"type": "string", "description": "local date YYYY-MM-DD; omit for today"},
         "date_to": {"type": "string", "description": "local date YYYY-MM-DD (inclusive, at most 31 days)"}},
         "required": ["subject"]}},
    {"name": "staff_schedule", "description": "Shifts of cleaners, technician and robots on one local day as known "
     "at now, incl. sick / out-of-service notices and when they became known.",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "local date YYYY-MM-DD; omit for today"}}}},
    {"name": "arrivals_departures", "description": "Confirmed arrivals and departures on one local day as booked "
     "at now (rooms, times, number of guests).",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "local date YYYY-MM-DD; omit for today"}}}},
    {"name": "guest_requests", "description": "Guest requests received by now (synthetic): time, room, type, "
     "language, priority, "
     "deadline, text, and the decisions that answered them. All filters optional.",
     "input_schema": {"type": "object", "properties": {
         "room": {"type": "string"}, "date": {"type": "string", "description": "local date YYYY-MM-DD"},
         "type": {"type": "string", "description": "e.g. early_checkin, temperature_cold, noise_complaint"}}}},
]

STATE_NAMES = {"occupied": "occupied", "vacant_clean": "vacant and clean", "needs_cleaning": "needs cleaning",
               "cleaning": "being cleaned", "unknown": "no data (unknown)"}
PLANT_NAMES = {"HP1": "Heat pump", "AHU1": "Ventilation", "WM_MAIN": "Water main", "PV1": "PV / battery"}
PLANT_STATUS = {"ok": "running normally", "incident": "open incident", "no_data": "no data"}
STALE = pd.Timedelta(minutes=30)
MAX_DAYS = 31
# subject -> (label, {metric: (column, label, unit)}, default metrics); the room entry is filled per room
SUBJECTS = {
    "building": ("Building power", {"power": ("building_load_kw", "Building load", "kW"),
                                    "solar": ("pv_kw", "Solar (PV)", "kW"),
                                    "grid": ("grid_import_kw", "From the grid", "kW"),
                                    "hvac": ("hvac_electric_kw", "Heating & ventilation", "kW"),
                                    "battery": ("battery_soc_kwh", "Battery charge", "kWh")}, ["power", "solar", "grid"]),
    "outdoor": ("Outdoor temperature", {"temperature": ("outdoor_c", "Outdoors", "°C")}, ["temperature"]),
    "heat pump": ("Heat pump", {"heat": ("heat_output_kw", "Heat output", "kW"),
                                "electric": ("electric_kw", "Electricity use", "kW"),
                                "cop": ("cop", "Efficiency (COP)", ""),
                                "supply": ("supply_temp_c", "Supply temperature", "°C")}, ["heat", "electric"]),
    "ventilation": ("Ventilation", {"filter": ("filter_dp_pa", "Filter pressure drop", "Pa"),
                                    "airflow": ("airflow_m3h", "Airflow", "m³/h"),
                                    "fan": ("fan_kw", "Fan power", "kW"),
                                    "supply": ("supply_air_c", "Supply air", "°C")}, ["filter"]),
    "water main": ("Water main", {"water": ("rate_l_per_interval", "Whole building", "L/15 min")}, ["water"]),
    "vending": ("Vending machine", {"drinks": ("VEND1.stock_drinks", "Drinks in stock", "items"),
                                    "snacks": ("VEND1.stock_snacks", "Snacks in stock", "items")}, ["drinks", "snacks"]),
    "waste": ("Waste", {"main": ("WASTE_MAIN.fill_pct", "Central container", "%"),
                        **{f"floor {i}": (f"WASTE_F{i}.fill_pct", f"Floor {i} bin", "%") for i in range(1, 5)}},
              ["main"]),
}
_SUBJECT_WORDS = {"building": ("building", "power", "energy", "electric", "solar", "pv", "grid", "battery", "load"),
                  "outdoor": ("outdoor", "outside", "weather"), "heat pump": ("heat pump", "hp1", "heating"),
                  "ventilation": ("ventilation", "ahu", "air handl", "filter"),
                  "water main": ("water main", "main meter", "building water", "water use"),
                  "vending": ("vending", "drinks", "snacks"), "waste": ("waste", "bin", "container")}
ROOM_METRICS = {"temperature": ("temperature_c", "Temperature", "°C"), "setpoint": ("setpoint_c", "Setpoint", "°C"),
                "humidity": ("humidity_pct", "Humidity", "%"), "co2": ("co2_ppm", "CO₂", "ppm"),
                "water": ("rate_l_per_interval", "Water use", "L/15 min")}


def _num(v, nd=1):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, nd)


def _plain(text) -> str:
    """humanize() + no shift ids."""
    return _SHIFT.sub("", humanize(text)).strip()


def plain_title(g: pd.DataFrame) -> str:
    """Incident title without the icon, e.g. "Water leak · Room 104"."""
    return re.sub(r":material/[a-z_]+:\s*", "", title(g)).strip()


def _cut(s, n=300) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _asset_code(text: str) -> str | None:
    """'Room 104' / 'R104' / 'heat pump' / 'Cleaner 1' -> code (None = no match)."""
    t = (text or "").strip()
    m = re.search(r"\b(?:room\s*|R)(\d{3})\b", t, re.I)
    if m:
        return f"R{m.group(1)}"
    low = t.lower()
    for table in (L.ASSETS, L.RESOURCES):
        for code, name in table.items():
            if low in (code.lower(), name.lower()) or (len(low) > 3 and name.lower() in low):
                return code
    return t.upper() if re.fullmatch(r"[A-Z]+_?[A-Z0-9]*\d", t) else None


def _type_code(text: str) -> str | None:
    low = (text or "").strip().lower()
    for code, (name, _) in L.ISSUE_TYPES.items():
        if low in (code, name.lower()):
            return code
    return None


class Tools:
    """Read-only queries over one run. ``call`` is the only entry point used by the model."""

    def __init__(self, df: pd.DataFrame, approvals: dict | None = None, data=None, store=None, twin=None,
                 now=None):
        self._df = df
        self._approvals = dict(approvals or {})
        self._data, self._store, self._twin = data, store, twin
        self.now = pd.Timestamp(now).tz_convert("UTC").floor("15min") if now is not None else None
        self.cited: set[str] = set()   # decision/episode ids returned so far (allowed citations)
        self.charts: list[dict] = []   # measurements looked up in this answer (drawn under it)

    # ---- helpers
    def _dec(self, r) -> dict:
        self.cited.add(r.event_id)
        return {"decision_id": r.event_id, "time": C.local_time(r.t), "did": L.label("action", r.action),
                "assets": [L.label("asset", a) for a in r.affected_assets],
                "status": L.label("autonomy", r.autonomy), "priority": L.label("priority", r.priority),
                "reason": _cut(_plain(r.reason), 240),
                **({"guest_message": L.PROPOSED} if isinstance(r.message, dict) and r.message else {}),
                **({"effect": L.SIMULATED + ": " + _cut(_plain(str(r.simulated_effect)), 160)}
                   if core.simulated_label(r.simulated_effect) else {}),
                **({"guest_consent": L.CONSENT_NEEDED} if r.requires_guest_consent else {}),
                **({"approval": ("mock decision recorded: " + str(self._approvals[r.event_id].get("status"))
                                 if isinstance(self._approvals.get(r.event_id), dict) else "waiting for a mock approval")}
                   if r.requires_human_approval else {})}

    def _inc(self, g: pd.DataFrame) -> dict:
        first = g.iloc[0]
        self.cited.update([first.episode, *g.event_id])
        return {"episode_id": first.episode, "title": plain_title(g), "type": L.label("issue", first.issue_type),
                "when": C.time_range(g.t.min(), g.t.max()),
                "priority": L.label("priority", min(g.priority, key=lambda p: _SEV.get(p, 9))),
                "needs_approval": bool(g.requires_human_approval.any()),
                "first_decision_id": first.event_id, "decisions": len(g),
                "headline": _cut(_plain(first.reason), 200)}

    def _groups(self):
        return self._df.sort_values(["t", "event_id"]).groupby("episode", sort=False)

    # ---- tools
    def list_incidents(self, type=None, asset=None, date_from=None, date_to=None, needs_approval=None,
                       include_routine=False):
        df = self._df
        if df.empty:
            return {"count": 0, "incidents": []}
        keep = df.issue_type.isin(L.INCIDENT_TYPES if not include_routine else list(L.ISSUE_TYPES))
        if type:
            code = _type_code(type)
            keep &= df.issue_type == (code or type)
        eps = set(df[keep].episode)
        if asset:
            code = _asset_code(asset) or asset
            eps &= set(df[[code in a or any(x.startswith(f"WM_{code}") for x in a) for a in df.affected_assets]].episode)
        local = df.t.dt.tz_convert(L.TZ).dt.date
        if date_from:
            eps &= set(df[local >= pd.Timestamp(date_from).date()].episode)
        if date_to:
            eps &= set(df[local <= pd.Timestamp(date_to).date()].episode)
        out = [self._inc(g) for ep, g in self._groups() if ep in eps]
        if needs_approval is not None:
            out = [o for o in out if o["needs_approval"] == bool(needs_approval)]
        out.sort(key=lambda o: (_SEV.get(next((k for k, (n, _) in L.PRIORITIES.items() if n == o["priority"]), ""), 9)))
        return {"count": len(out), "incidents": out[:LIST_LIMIT], **({"more": len(out) - LIST_LIMIT}
                                                                       if len(out) > LIST_LIMIT else {})}

    def get_incident(self, episode_id):
        g = self._df[self._df.episode == episode_id].sort_values(["t", "event_id"])
        if g.empty:
            return {"error": f"incident {NOT_IN_LOG}"}
        return {**self._inc(g), "steps": [self._dec(r) for r in g.head(12).itertuples()]}

    def get_decision(self, event_id):
        g = self._df[self._df.event_id == event_id]
        if g.empty:
            return {"error": f"decision {NOT_IN_LOG}"}
        r = next(g.itertuples())
        self.cited.add(r.episode)
        return {**self._dec(r), "episode_id": r.episode,
                "expected_outcome": _cut(_plain(r.expected_outcome), 200),
                "how_we_will_know": _cut(_plain(r.verification_plan), 200),
                "evidence": [_cut(_plain(core.parse_evidence(e).detail or e), 160) for e in r.evidence[:5]]}

    def get_evidence(self, episode_id):
        g = self._df[self._df.episode == episode_id]
        if g.empty:
            return {"error": f"incident {NOT_IN_LOG}"}
        self.cited.update([episode_id, *g.event_id])
        ev = list(dict.fromkeys(_plain(core.parse_evidence(e).detail or e) for x in g.evidence for e in x))
        plans = list(dict.fromkeys(_plain(v) for v in g.verification_plan if isinstance(v, str) and v))
        return {"episode_id": episode_id, "first_decision_id": g.event_id.iloc[0],
                "evidence": [_cut(e, 160) for e in ev[:8] if e], "how_we_will_know": [_cut(p, 200) for p in plans[:3]]}

    def count_by_type(self):
        df = self._df
        if df.empty:
            return {"types": []}
        g = df.groupby("issue_type").agg(episodes=("episode", "nunique"), decisions=("event_id", "size"))
        return {"types": [{"type": L.label("issue", k), "incident": k in L.INCIDENT_TYPES,
                           "episodes": int(v.episodes), "decisions": int(v.decisions)} for k, v in g.iterrows()]}

    def pending_approvals(self):
        df = self._df
        need = df[df.requires_human_approval & ~df.event_id.isin(set(self._approvals))] if len(df) else df
        out = []
        for ep, g in need.sort_values(["t", "event_id"]).groupby("episode", sort=False):
            inc = self._inc(self._df[self._df.episode == ep].sort_values(["t", "event_id"]))
            inc["waiting"] = [{"decision_id": r.event_id, "did": L.label("action", r.action),
                               "time": C.local_time(r.t)} for r in g.head(4).itertuples()]
            out.append(inc)
        out.sort(key=lambda o: _SEV.get(next((k for k, (n, _) in L.PRIORITIES.items() if n == o["priority"]), ""), 9))
        return {"note": L.MOCK, "count": len(out), "incidents": out[:LIST_LIMIT]}

    # ---- building-data tools (read-only, "as of" a moment)
    def _need_data(self):
        if self._data is None or self._store is None:
            raise LookupError("the building data is not loaded in this view")

    def _span(self):
        return pd.Timestamp(self._data.start).tz_convert("UTC"), pd.Timestamp(self._data.end).tz_convert("UTC")

    def _moment(self, time=None) -> pd.Timestamp:
        """Local 'YYYY-MM-DD HH:MM' (or a date, or nothing = now) -> UTC 15-min step inside the dataset."""
        lo, hi = self._span()
        if time in (None, "", "now"):
            t = self.now if self.now is not None else hi - pd.Timedelta(minutes=15)
        else:
            x = pd.Timestamp(str(time))
            if len(str(time).strip()) <= 10 and self.now is not None:        # a date only: same clock time as now
                local_now = self.now.tz_convert(L.TZ).tz_localize(None)
                x = x + (local_now - local_now.normalize())
            t = (x.tz_convert("UTC") if x.tzinfo else x.tz_localize(L.TZ, ambiguous="NaT",
                                                                       nonexistent="shift_forward").tz_convert("UTC"))
            if pd.isna(t):
                raise ValueError(f"time {time!r} is ambiguous (clock change)")
            if t < lo or t >= hi:
                raise ValueError(f"{time} is outside the data, which covers {lo.tz_convert(L.TZ):%Y-%m-%d} to "
                                 f"{(hi - pd.Timedelta(minutes=15)).tz_convert(L.TZ):%Y-%m-%d}")
        return min(max(t.floor("15min"), lo), hi - pd.Timedelta(minutes=15))

    def _day(self, date=None) -> tuple[pd.Timestamp, pd.Timestamp]:
        """Local date (default: the day of now) -> UTC [start, end). Dates outside the data are an error."""
        d = (pd.Timestamp(str(date)) if date else self._moment().tz_convert(L.TZ).tz_localize(None)).normalize()
        start = d.tz_localize(L.TZ).tz_convert("UTC")
        end = (d + pd.Timedelta(days=1)).tz_localize(L.TZ).tz_convert("UTC")
        lo, hi = self._span()
        if end <= lo or start >= hi:
            raise ValueError(f"{d:%Y-%m-%d} is outside the data, which covers {lo.tz_convert(L.TZ):%Y-%m-%d} to "
                             f"{(hi - pd.Timedelta(minutes=15)).tz_convert(L.TZ):%Y-%m-%d}")
        return start, end

    @staticmethod
    def _asof(s, t):
        """Last value at or before t within 30 min (else None = unknown)."""
        if s is None or len(s) == 0:
            return None
        s = s[s.index <= t]
        s = s.dropna() if isinstance(s, pd.Series) else s
        if len(s) == 0 or t - s.index[-1] > STALE:
            return None
        return s.iloc[-1]

    def _open_incidents(self, t, asset: str | None = None) -> list[dict]:
        from viewer import twin as TW
        sp = TW.open_at(TW.episode_spans(self._df), t) if len(self._df) else pd.DataFrame()
        out = []
        for r in sp.itertuples() if len(sp) else []:
            if asset and not any(a == asset or a == f"WM_{asset}" for a in r.assets):
                continue
            g = self._df[self._df.episode == r.episode].sort_values(["t", "event_id"])
            self.cited.update([r.episode, g.event_id.iloc[0]])
            out.append({"title": plain_title(g), "since": C.local_time(r.start),
                        "priority": L.label("priority", r.priority), "first_decision_id": g.event_id.iloc[0]})
        return out[:8]

    def room_status(self, room, time=None):
        self._need_data()
        code = _asset_code(room)
        if code not in set(self._data.room_ids):
            return {"error": f"room {room!r} is not one of the 40 rooms"}
        from viewer import twin as TW
        t = self._moment(time)
        tel = self._store.telemetry(code)
        row = self._asof(tel, t)
        state = TW.room_state(row.housekeeping_state, row.presence) if row is not None else "unknown"
        out = {"room": L.label("asset", code), "at": C.local_time(t), "state": STATE_NAMES[state]}
        if row is not None:
            out.update({k: v for k, v in (("temperature_c", _num(row.temperature_c)),
                                          ("setpoint_c", _num(row.setpoint_c)),
                                          ("humidity_pct", _num(row.humidity_pct, 0)),
                                          ("co2_ppm", _num(row.co2_ppm, 0))) if v is not None})
            if not pd.isna(row.window_open):
                out["window"] = "open" if row.window_open >= 0.5 else "closed"
        w = self._asof(self._store.room_water(code), t)
        out["water_l_per_15min"] = _num(w) if w is not None else "no data (unknown)"
        b = self._data.bookings
        b = b[(b.room_id == code) & (b.status == "confirmed") & (b.created_utc <= t)]
        stay = b[(b.arrival_utc <= t) & (b.departure_utc > t)]
        if len(stay):
            s = stay.iloc[0]
            out["booking"] = {"stay": C.time_range(s.arrival_utc, s.departure_utc), "guests": int(s.guest_count)}
        nxt = b[b.arrival_utc > t].sort_values("arrival_utc")
        if len(nxt):
            out["next_arrival"] = C.local_time(nxt.arrival_utc.iloc[0])
        out["open_incidents"] = self._open_incidents(t, code)
        return out

    def building_now(self, time=None):
        self._need_data()
        from viewer import twin as TW
        t = self._moment(time)
        out = {"at": C.local_time(t)}
        if self._twin is not None:
            rooms = self._twin.rooms_at(t)
            counts = rooms.state.value_counts()
            out["rooms"] = {STATE_NAMES[s]: int(counts.get(s, 0)) for s in TW.STATES if counts.get(s, 0)}
            for s, key in (("needs_cleaning", "need_cleaning"), ("cleaning", "being_cleaned"), ("unknown", "no_data")):
                ids = rooms[rooms.state == s].room_id.tolist()
                if ids:
                    out[key] = [L.label("asset", r) for r in ids]
            sp = TW.open_at(TW.episode_spans(self._df), t) if len(self._df) else pd.DataFrame()
            out["plant"] = {PLANT_NAMES[k]: PLANT_STATUS[v] for k, v in self._twin.plant_at(t, sp).items()}
        out["open_incidents"] = self._open_incidents(t)
        ds, de = TW.local_day(t)
        sh = TW.staff_at(self._data.roster, t, ds, de)
        out["staff_today"] = [{"who": L.label("resource", r.resource_id), "shift": C.time_range(r.start, r.end),
                               "status": r.status.replace("_", " "), "on_shift_now": bool(r.start <= t < r.end
                                                                                           and r.status == "scheduled")}
                              for r in sh.itertuples()]
        en = self._store.energy()
        for col, key in (("building_load_kw", "power_kw"), ("pv_kw", "solar_kw"), ("grid_import_kw", "from_grid_kw")):
            v = self._asof(en[col], t) if col in en else None
            out[key] = _num(v) if v is not None else "no data (unknown)"
        o = self._asof(self._store.weather(), t)
        out["outdoor_c"] = _num(o) if o is not None else "no data (unknown)"
        return out

    def _subject(self, subject: str):
        low = (subject or "").strip().lower()
        code = _asset_code(subject) if re.search(r"\broom\s*\d{3}\b|\bR\d{3}\b", subject or "", re.I) else None
        if code in set(self._data.room_ids):
            return code, (L.label("asset", code), ROOM_METRICS, ["temperature", "setpoint"])
        for key, words in _SUBJECT_WORDS.items():
            if low == key or any(w in low for w in words):
                return key, SUBJECTS[key]
        return None, None

    def _frame(self, key: str) -> pd.DataFrame:
        s = self._store
        if key in self._data.room_ids:
            f = s.telemetry(key).copy()
            w = s.room_water(key)
            return f.join(w[~w.index.duplicated()].rename("rate_l_per_interval"), how="outer")
        if key == "building":
            return s.energy()
        if key == "outdoor":
            return s.weather().rename("outdoor_c").to_frame()
        if key == "heat pump":
            return s.equipment("HP1")
        if key == "ventilation":
            return s.equipment("AHU1")
        if key == "water main":
            m = s.main_water()
            return m[~m.index.duplicated()].rename("rate_l_per_interval").to_frame()
        return s.logistics()

    def measurements(self, subject, metric=None, date_from=None, date_to=None):
        self._need_data()
        key, spec = self._subject(subject)
        if key is None:
            return {"error": f"unknown subject {subject!r}; use a room, building, outdoor, heat pump, ventilation, "
                             "water main, vending or waste"}
        label, metrics, default = spec
        if metric:
            ml = metric.lower()
            pick = [m for m, (col, name, _) in metrics.items() if ml in m or m in ml or ml in name.lower()]
            if not pick:
                return {"error": f"unknown metric {metric!r} for {label}; choose from {list(metrics)}"}
        else:
            pick = default
        start, _ = self._day(date_from)
        _, end = self._day(date_to or date_from)
        if end <= start:
            return {"error": "date_to is before date_from"}
        if end - start > pd.Timedelta(days=MAX_DAYS):
            end = start + pd.Timedelta(days=MAX_DAYS)
        end = min(end, self._moment() + pd.Timedelta(minutes=15))      # only what is known at now
        if end <= start:
            return {"subject": label, "error": f"no data yet: that period is after now ({C.local_time(self._moment())})"}
        f = self._frame(key)
        f = f[(f.index >= start) & (f.index < end)]
        series, stats = {}, []
        for m in pick:
            col, name, unit = metrics[m]
            if col not in f:
                continue
            s = pd.to_numeric(f[col], errors="coerce")
            v = s.dropna()
            grid = pd.date_range(start, end, freq="15min" if key not in ("vending", "waste") else "1h",
                                 inclusive="left")
            missing = 1 - min(len(v), len(grid)) / max(len(grid), 1)
            if v.empty:
                stats.append({"metric": name, "unit": unit, "values": "no data (unknown) in this period"})
                continue
            series[name] = s
            days = v.groupby(v.index.tz_convert(L.TZ).date)
            stats.append({"metric": name, "unit": unit, "min": _num(v.min(), 2), "min_at": C.local_time(v.idxmin()),
                          "max": _num(v.max(), 2), "max_at": C.local_time(v.idxmax()), "mean": _num(v.mean(), 2),
                          "latest": _num(v.iloc[-1], 2), "latest_at": C.local_time(v.index[-1]),
                          **({"missing_share": f"{missing:.0%}"} if missing >= 0.02 else {}),
                          **({"by_day": [{"day": f"{pd.Timestamp(k):%a %-d %b}", "min": _num(x.min(), 2),
                                          "mean": _num(x.mean(), 2), "max": _num(x.max(), 2)} for k, x in days]}
                             if 1 < days.ngroups <= 14 else {})})
        period = C.time_range(start, end - pd.Timedelta(minutes=15))
        if series:
            units = {metrics[m][2] for m in pick if metrics[m][1] in series}
            self.charts.append({"title": f"{label} · {period}", "unit": " / ".join(sorted(units)),
                                "series": series, "start": start, "end": end})
        return {"subject": label, "period": period, "measurements": stats}

    def staff_schedule(self, date=None):
        self._need_data()
        from viewer import twin as TW
        ds, de = self._day(date)
        now = self._moment()
        sh = TW.staff_at(self._data.roster, now, ds, de).sort_values(["start", "resource_id"])
        shifts = [{"who": L.label("resource", x.resource_id), "role": x.role, "shift": C.time_range(x.start, x.end),
                   "status": x.status.replace("_", " "),
                   **({"known_since": C.local_time(x.known)} if x.known is not None and not pd.isna(x.known) else {})}
                  for x in sh.itertuples()]
        return {"day": f"{ds.tz_convert(L.TZ):%a %-d %b}", "as_known_at": C.local_time(now),
                **({"note": f"as known at {C.local_time(now)}; sick or out-of-service notices can still come in"}
                   if de > now else {}), "shifts": shifts,
                "absent": [s["who"] for s in shifts if s["status"] != "scheduled"]}

    def arrivals_departures(self, date=None):
        self._need_data()
        ds, de = self._day(date)
        now = self._moment()
        b = self._data.bookings
        b = b[b.created_utc <= now]                           # bookings that exist at the selected moment
        conf = b[b.status == "confirmed"]

        def rows(col):
            x = conf[(conf[col] >= ds) & (conf[col] < de)].sort_values([col, "room_id"])
            return [{"room": L.label("asset", r.room_id), "time": f"{getattr(r, col).tz_convert(L.TZ):%H:%M}",
                     "guests": int(r.guest_count)} for r in x.itertuples()]
        arr, dep = rows("arrival_utc"), rows("departure_utc")
        cancelled = int(((b.status == "cancelled") & (b.arrival_utc >= ds) & (b.arrival_utc < de)).sum())
        return {"day": f"{ds.tz_convert(L.TZ):%a %-d %b}", "as_known_at": C.local_time(now),
                **({"note": f"as booked at {C.local_time(now)}; bookings and cancellations can still change"}
                   if de > now else {}), "arrivals": len(arr), "departures": len(dep),
                "arriving": arr[:LIST_LIMIT * 3], "departing": dep[:LIST_LIMIT * 3],
                **({"cancelled_arrivals": cancelled} if cancelled else {})}

    def guest_requests(self, room=None, date=None, type=None):
        self._need_data()
        g = self._data.guest_requests
        g = g[g.timestamp_utc <= self._moment()]                       # requests received by now
        if room:
            code = _asset_code(room)
            g = g[g.room_id == code]
        if date:
            ds, de = self._day(date)
            g = g[(g.timestamp_utc >= ds) & (g.timestamp_utc < de)]
        if type:
            g = g[g.request_type.str.contains(str(type).strip().lower().replace(" ", "_"), na=False)]
        g = g.sort_values("timestamp_utc")
        by_req: dict[str, list[str]] = {}
        if len(self._df) and "guest_request_ids" in self._df:
            for r in self._df.itertuples():
                for q in (r.guest_request_ids if isinstance(r.guest_request_ids, list) else []):
                    by_req.setdefault(q, []).append(r.event_id)
        out = []
        for r in g.head(LIST_LIMIT).itertuples():
            ids = by_req.get(r.request_id, [])[:3]
            self.cited.update(ids)
            out.append({"time": C.local_time(r.timestamp_utc),
                        "room": L.label("asset", r.room_id) if isinstance(r.room_id, str) else "unknown",
                        "type": str(r.request_type).replace("_", " "), "language": r.language,
                        "priority": r.priority, "text": _cut(r.text, 160),
                        **({"due": C.local_time(r.response_due_utc)} if not pd.isna(r.response_due_utc) else {}),
                        **({"guest_consent": L.CONSENT_NEEDED} if r.requires_guest_consent else {}),
                        "answered_by": ids})
        return {"count": int(len(g)), "requests": out, **({"more": int(len(g)) - LIST_LIMIT}
                                                          if len(g) > LIST_LIMIT else {})}

    def call(self, name: str, args: dict | None) -> dict:
        fn = {s["name"]: getattr(self, s["name"]) for s in TOOL_SPECS}.get(name)
        if fn is None:
            return {"error": f"unknown tool {name!r}; tools are read-only: {[s['name'] for s in TOOL_SPECS]}"}
        try:
            return fn(**{k: v for k, v in (args or {}).items() if v not in (None, "")})
        except (TypeError, ValueError) as e:
            return {"error": f"bad arguments: {e}"}
        except LookupError as e:
            return {"error": f"{e}; this is {NOT_IN_LOG}"}


# ----------------------------------------------------------------------------- answers
@dataclass
class Answer:
    text: str                        # may contain [D000123] citations (render() turns them into links)
    source: str                      # llm | offline | cached:llm | cached:offline
    tool_calls: list = field(default_factory=list)
    error: str | None = None


def _anthropic(cfg: dict) -> Callable:
    def client(system, messages, tools, timeout):
        url = (cfg["base_url"] or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        body = {"model": cfg["model"], "max_tokens": 700, "temperature": 0, "system": system,
                "messages": messages, "tools": tools}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "content-type": "application/json", "x-api-key": cfg["key"], "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (configured endpoint)
            return json.loads(r.read().decode())
    return client


def default_client() -> Callable | None:
    """Claude client from .env, or None (no key / other provider / disabled)."""
    cfg = llm.config()
    return _anthropic(cfg) if cfg and cfg["provider"] == "anthropic" else None


def context(tools: Tools) -> str:
    """What 'now' means and which period the data covers (appended to the system prompt)."""
    if tools._data is None:
        return ""
    lo, hi = tools._span()
    now = tools._moment()
    iso = lambda t: f"{t.tz_convert(L.TZ):%Y-%m-%d %H:%M}"  # noqa: E731
    return (f" Now (the moment selected in the viewer) is {C.local_time(now)} {now.tz_convert(L.TZ):%Y} "
            f"(local {iso(now)}); 'now', 'today', 'yesterday' and 'tomorrow' refer to it. The data covers local "
            f"{iso(lo)} to {iso(hi - pd.Timedelta(minutes=15))}; pass dates in tool calls as YYYY-MM-DD with that year.")


def ask_llm(question: str, tools: Tools, client: Callable, history: list | None = None,
            max_calls: int = MAX_TOOL_CALLS, timeout: float = TIMEOUT_S) -> Answer:
    """Tool-use loop: at most ``max_calls`` tool calls and ``timeout`` seconds; never raises."""
    messages = [*(history or [])[-6:], {"role": "user", "content": question}]
    system = SYSTEM + context(tools)
    calls, t0 = [], time.monotonic()
    try:
        while True:
            left = timeout - (time.monotonic() - t0)
            if left <= 0:
                return Answer("", "llm", calls, "The AI copilot took too long (over 30 s). Try a shorter question.")
            out = client(system, messages, TOOL_SPECS, left)
            blocks = out.get("content") or []
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if not uses:
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
                return Answer(text or f"That is {NOT_IN_LOG}.", "llm", calls)
            if len(calls) + len(uses) > max_calls:
                return Answer("", "llm", calls, f"The question needed more than {max_calls} look-ups. "
                                                "Try asking about one room, date or incident.")
            results = []
            for u in uses:
                res = tools.call(u.get("name", ""), u.get("input") or {})
                calls.append((u.get("name"), u.get("input") or {}))
                results.append({"type": "tool_result", "tool_use_id": u.get("id"),
                                "content": json.dumps(res, ensure_ascii=False, default=str)[:6000]})
            messages += [{"role": "assistant", "content": blocks}, {"role": "user", "content": results}]
    except Exception:  # network, auth, quota, malformed response
        return Answer("", "llm", calls, "The AI copilot is not reachable right now.")


def _date_in(q: str, df: pd.DataFrame):
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", q, re.I)
    m2 = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", q)
    if m2:
        return pd.Timestamp(m2.group(0)).date()
    if not m or df.empty:
        return None
    years = sorted(df.t.dt.tz_convert(L.TZ).dt.year.unique())
    for y in years:
        try:
            d = pd.Timestamp(year=int(y), month=_MONTHS[m.group(2).lower()[:3]], day=int(m.group(1))).date()
        except ValueError:
            continue
        if (df.t.dt.tz_convert(L.TZ).dt.date == d).any():
            return d
    return None


def _action_in(q: str) -> str | None:
    low = q.lower()
    keys = {"on-call": "call_oncall_housekeeper", "oncall": "call_oncall_housekeeper", "technician": "dispatch_technician",
            "robot": "dispatch_robot", "shut": "water_shutoff", "inspection": "request_inspection",
            "service": "order_service", "moved": "reassign_room", "released": "release_room",
            "blocked": "block_room", "setpoint": "adjust_room_setpoint", "temperature": "adjust_room_setpoint"}
    return next((a for k, a in keys.items() if k in low), None)


_TYPE_WORDS = {"water_leak": ("water leak", "leak"), "heating_comfort": ("heating", "heat pump", "cold", "comfort"),
               "ventilation_maintenance": ("filter", "ventilation"), "occupancy_surge": ("busy weekend", "surge", "event"),
               "room_turnaround": ("turnaround", "early check-in", "early checkin", "cleaning conflict")}


def _bullet(o: dict) -> str:
    return f"- **{o['title']}**, {o['when']} ({o['priority']}): {o['headline']} [{o['first_decision_id']}]"


_ROOM_RX = re.compile(r"\broom\s*\d{3}\b|\bR\d{3}\b", re.I)
_SHOW_RX = re.compile(r"\b(show|chart|graph|plot|trend|how much|how high|level|levels|measure\w*)\b")


def _fmt(v, unit=""):
    return "unknown" if v is None or isinstance(v, str) else f"{v:g}{unit}"


def _offline_building(q: str, low: str, day, tools: Tools, use) -> Answer | None:
    """Offline answers from the building-data tools (room now, building now, staff, arrivals, requests, charts)."""
    date = str(day) if day is not None else None
    room = _asset_code(q) if _ROOM_RX.search(q) else None
    if re.search(r"guest requests?|requests? from|\bcomplain", low):
        res = use("guest_requests", **{k: v for k, v in (("room", room), ("date", date)) if v})
        if not res["count"]:
            return Answer(f"No guest request matches. That is {NOT_IN_LOG}.", "offline")
        lines = [f"- {r['time']}, {r['room']}: {r['type']} ({r['language']}, {r['priority']}): “{r['text']}”"
                 + (f" [{', '.join(r['answered_by'])}]" if r["answered_by"] else "") for r in res["requests"][:6]]
        return Answer(f"{L.n(res['count'], 'guest request')}:\n" + "\n".join(lines), "offline")
    if re.search(r"\bwho(?: is|'s| are)? (?:working|on shift|on duty)|\bstaff\b|\broster\b|\bshifts?\b", low):
        res = use("staff_schedule", **({"date": date} if date else {}))
        if not res["shifts"]:
            return Answer(f"No shifts are published for {res['day']} (as known at {res['as_known_at']}).", "offline")
        lines = [f"- {s['who']}: {s['shift'].split(', ')[-1]}" + ("" if s["status"] == "scheduled" else
                 f": **{s['status']}**" + (f" (known {s['known_since']})" if "known_since" in s else ""))
                 for s in res["shifts"]]
        return Answer(f"Staff on {res['day']} (as known at {res['as_known_at']}):\n" + "\n".join(lines), "offline")
    if "early" not in low and re.search(r"\barriv\w*|\bcheck-?ins?\b|\bdepart\w*|\bcheck-?outs?\b", low):
        res = use("arrivals_departures", **({"date": date} if date else {}))
        arr = ", ".join(f"{a['room']} {a['time']}" for a in res["arriving"][:10])
        dep = ", ".join(f"{a['room']} {a['time']}" for a in res["departing"][:10])
        return Answer(f"{res['day']} (as booked at {res['as_known_at']}): {L.n(res['arrivals'], 'arrival')}, "
                      f"{L.n(res['departures'], 'departure')}.\n- Arriving: {arr or 'none'}\n- Departing: {dep or 'none'}",
                      "offline")
    subject = next((k for k, words in _SUBJECT_WORDS.items() if any(w in low for w in words)), None)
    if (room or subject) and _SHOW_RX.search(low):
        res = use("measurements", subject=L.label("asset", room) if room else subject,
                  **({"date_from": date} if date else {}))
        if "error" in res:
            return None
        lines = [f"- {m['metric']}: " + (m["values"] if "values" in m else
                 f"{_fmt(m['min'])}–{_fmt(m['max'])} {m['unit']} (mean {_fmt(m['mean'])}, latest {_fmt(m['latest'])} "
                 f"at {m['latest_at']})" + (f", {m['missing_share']} missing" if "missing_share" in m else ""))
                 for m in res["measurements"]]
        return Answer(f"{res['subject']}, {res['period']}:\n" + "\n".join(lines), "offline")
    if room and re.search(r"\b(now|status|temperature|warm|cold|humid\w*|co2|booked|occupied|clean|state)\b", low):
        r = use("room_status", room=L.label("asset", room))
        if "error" in r:
            return None
        bits = [f"{r['room']} at {r['at']}: **{r['state']}**",
                f"temperature {_fmt(r.get('temperature_c'), ' °C')} (setpoint {_fmt(r.get('setpoint_c'), ' °C')})",
                f"humidity {_fmt(r.get('humidity_pct'), ' %')}, CO₂ {_fmt(r.get('co2_ppm'), ' ppm')}",
                f"water {_fmt(r.get('water_l_per_15min'), ' L/15 min')}"]
        if "booking" in r:
            bits.append(f"booked {r['booking']['stay']} ({L.n(r['booking']['guests'], 'guest')})")
        elif "next_arrival" in r:
            bits.append(f"next arrival {r['next_arrival']}")
        inc = "".join(f"\n- Open: **{i['title']}** since {i['since']} [{i['first_decision_id']}]"
                      for i in r["open_incidents"])
        return Answer(", ".join(bits) + "." + inc, "offline")
    if re.search(r"right now|overview|what'?s going on|what is going on|status of the building|"
                 r"needs? cleaning|building now", low):
        r = use("building_now")
        rooms = ", ".join(f"{v} {k}" for k, v in r.get("rooms", {}).items())
        lines = [f"Building at {r['at']}: {rooms or 'room states unknown'}."]
        if r.get("need_cleaning"):
            lines.append("- Need cleaning: " + ", ".join(r["need_cleaning"]))
        lines += [f"- Open: **{i['title']}** since {i['since']} ({i['priority']}) [{i['first_decision_id']}]"
                  for i in r["open_incidents"]]
        if r.get("plant"):
            lines.append("- Plant: " + ", ".join(f"{k} {v}" for k, v in r["plant"].items()))
        on = [s["who"] for s in r["staff_today"] if s["on_shift_now"]]
        lines.append(f"- On shift now: {', '.join(on) or 'nobody'}; power {_fmt(r['power_kw'], ' kW')}, "
                     f"solar {_fmt(r['solar_kw'], ' kW')}, outdoors {_fmt(r['outdoor_c'], ' °C')}")
        return Answer("\n".join(lines), "offline")
    return None


def ask_offline(question: str, tools: Tools) -> Answer:
    """Deterministic answers for simple questions, from the same read-only tools (no AI model)."""
    q, df = question.strip(), tools._df
    calls = []

    def use(name, **kw):
        calls.append((name, kw))
        return tools.call(name, kw)

    low = q.lower()
    day, action = _date_in(q, df), _action_in(q)
    if action and day is not None:
        local = df.t.dt.tz_convert(L.TZ).dt.date
        rows = df[(df.action == action) & (local == day)]
        if rows.empty:
            return Answer(f"No “{L.label('action', action).lower()}” on {pd.Timestamp(day):%a %-d %b}. That is "
                          f"{NOT_IN_LOG}.", "offline", calls)
        lines = []
        for r in rows.head(5).itertuples():
            d = use("get_decision", event_id=r.event_id)
            lines.append(f"- {d['time']}: {d['did']} for {', '.join(d['assets']) or 'the building'}: {d['reason']}"
                         f"{'; ' + d['approval'] if 'approval' in d else ''} [{r.event_id}]")
        return Answer(f"On {pd.Timestamp(day):%a %-d %b} the log has {L.n(len(rows), 'such decision')}:\n" + "\n".join(lines),
                      "offline", calls)
    if "approv" in low:
        res = use("pending_approvals")
        if not res["count"]:
            return Answer("No incident is waiting for a (mock) approval.", "offline", calls)
        lines = [f"- **{o['title']}** ({o['priority']}): "
                 + ", ".join(f"{w['did'].lower()} [{w['decision_id']}]" for w in o["waiting"]) for o in res["incidents"]]
        more = res["count"] - len(res["incidents"])
        return Answer(f"{L.n(res['count'], 'incident')} still wait for a manager's decision. {L.MOCK}.\n"
                      + "\n".join(lines) + (f"\n- … and {more} more" if more > 0 else ""), "offline", calls)
    if re.search(r"\bhow many\b|\bcount\b|\beach type\b|\bper type\b", low):
        res = use("count_by_type")
        inc = [t for t in res["types"] if t["incident"]]
        rest = [t for t in res["types"] if not t["incident"]]
        if not res["types"]:
            return Answer(f"This run has no decisions, so the answer is {NOT_IN_LOG}.", "offline", calls)
        return Answer("Incidents in this run:\n" + "\n".join(f"- {t['type']}: {t['episodes']}" for t in inc)
                      + ("\n\nRoutine work: " + ", ".join(f"{t['type'].lower()} {t['episodes']}" for t in rest)
                         if rest else ""), "offline", calls)
    if tools._data is not None:
        a = _offline_building(q, low, day, tools, use)
        if a is not None:
            a.tool_calls = calls
            return a
    asset = _asset_code(q) if re.search(r"\broom\s*\d{3}\b|\bR\d{3}\b", q, re.I) or any(
        n.lower() in low for n in [*L.ASSETS.values(), *L.RESOURCES.values()]) else None
    typ = None if asset else next((c for c, words in _TYPE_WORDS.items() if any(w in low for w in words)), None)
    if asset or typ:
        kw = {k: v for k, v in (("asset", asset), ("type", typ)) if v}
        if day is not None:
            kw.update(date_from=str(day), date_to=str(day))
        res = use("list_incidents", **kw)
        what = " and ".join(x for x in (L.label("asset", asset) if asset else "",
                                        L.label("issue", typ).lower() if typ else "") if x)
        if not res["count"]:
            return Answer(f"No incident for {what}. That is {NOT_IN_LOG}.", "offline", calls)
        more = res.get("more", 0)
        return Answer(f"{L.n(res['count'], 'incident')} for {what}:\n" + "\n".join(_bullet(o) for o in res["incidents"][:6])
                      + (f"\n- … and {res['count'] - 6} more" if res["count"] > 6 or more else ""), "offline", calls)
    return Answer(f"I couldn't find that. It is {NOT_IN_LOG} as far as the offline helper can tell. Without the "
                  "AI copilot I understand questions about a room (what happened, or its status now), the building "
                  "right now, staff, arrivals, guest requests, measurements ('show the heat pump'), a date with an "
                  "action, approvals, counts and incident types.", "offline", calls)


# ----------------------------------------------------------------------------- suggestions + cache
def suggestions(df: pd.DataFrame) -> list[str]:
    """Four questions that have an answer in this run (room of the most severe incident, approvals, a date, counts)."""
    out = []
    inc = df[df.issue_type.isin(L.INCIDENT_TYPES)] if len(df) else df
    rooms = inc.assign(s=inc.priority.map(_SEV).fillna(9)).sort_values(["s", "t"]) if len(inc) else inc
    room = next((a for x in rooms.affected_assets for a in x if re.fullmatch(r"R\d{3}", a)), None) if len(rooms) else None
    out.append(f"What happened in {L.label('asset', room)}?" if room else "What happened in Room 101?")
    out.append("Which incidents still need approval?")
    oc = df[df.action == "call_oncall_housekeeper"] if len(df) else df
    if len(oc):
        out.append(f"Why was the on-call housekeeper called on {oc.t.iloc[0].tz_convert(L.TZ):%-d %b}?")
    else:
        out.append("What did we do about the heat pump?")
    out.append("How many incidents of each type are there?")
    return out


def fingerprint(df: pd.DataFrame) -> str:
    cols = ["event_id", "timestamp_utc", "issue_type", "action", "episode"]
    rows = df[cols].astype(str).values.tolist() if len(df) else []
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def load_cache(path: Path = CACHE) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc.get("runs", {}) if isinstance(doc, dict) else {}


def cached(df: pd.DataFrame, question: str, cache: dict) -> Answer | None:
    e = (cache.get(fingerprint(df)) or {}).get("answers", {}).get(question.strip())
    return Answer(e["text"], "cached:" + str(e.get("source") or "offline")) if isinstance(e, dict) and isinstance(e.get("text"), str) else None


def answer(question: str, tools: Tools, client: Callable | None = None, cache: dict | None = None,
           history: list | None = None) -> Answer:
    """LLM (if a client) -> cached example -> offline answer. A failed LLM call falls back with its error kept."""
    err = None
    if client is not None:
        a = ask_llm(question, tools, client, history)
        if not a.error:
            return a
        err = a.error
        tools.charts.clear()                      # the fallback answer draws only what it looked up itself
    c = cached(tools._df, question, cache or {})
    a = c or ask_offline(question, tools)
    a.error = err
    return a


def render(text: str, df: pd.DataFrame, allowed: set[str] | None = None) -> tuple[str, list[dict]]:
    """[D000123] -> [1]; returns (markdown, sources [{n, episode, title}]). Ids not in the log (or not returned by a
    tool, if ``allowed``) are dropped. Remaining codes/UTC are turned into plain language line by line."""
    by_event = dict(zip(df.event_id, df.episode)) if len(df) else {}
    eps = set(df.episode) if len(df) else set()
    sources: list[dict] = []
    index: dict[str, int] = {}

    def num(cid: str) -> str | None:
        if allowed is not None and cid not in allowed:
            return None
        ep = by_event.get(cid) or (cid if cid in eps else None)
        if ep is None:
            return None
        if ep not in index:
            index[ep] = len(sources) + 1
            g = df[df.episode == ep]
            sources.append({"n": index[ep], "episode": ep, "title": f"{plain_title(g)} · {C.local_time(g.t.min())}"})
        return f"⟦{index[ep]}⟧"

    def sub(m):
        ns = list(dict.fromkeys(x for x in (num(c.strip()) for c in m.group(1).split(",")) if x))
        return "".join(ns)

    lines = []
    for line in CITE.sub(sub, text or "").splitlines():
        lead = re.match(r"^\s*(?:[-*•]|\d+\.)\s+", line)
        body = _plain(line[lead.end():] if lead else line)
        lines.append(("- " if lead else "") + body + (":" if line.rstrip().endswith(":") and body else ""))
    md = "\n".join(lines)
    md = re.sub(r"⟦(\d+)⟧", r" [\1]", md).replace("  [", " [")
    return md, sources


def build_cache(decisions: Path, out: Path = CACHE, client: Callable | None = None) -> dict:
    df = core.load_decisions(decisions)
    runs = load_cache(out)
    entry = {"decisions": Path(decisions).name, "answers": {}}
    for q in suggestions(df):
        a = answer(q, Tools(df), client)
        entry["answers"][q] = {"text": a.text, "source": a.source.removeprefix("cached:")}
    runs[fingerprint(df)] = entry
    out.write_text(json.dumps({"note": "Example answers for the suggested questions, keyed by run fingerprint. "
                                       "Built by `python viewer/copilot.py --build-cache`.", "runs": runs},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return entry


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build-cache", action="store_true", help="answer the suggested questions and store them")
    ap.add_argument("--decisions", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=CACHE)
    ap.add_argument("--ask", help="answer one question and print it")
    a = ap.parse_args(argv)
    client = default_client()
    if a.ask:
        df = core.load_decisions(a.decisions)
        ans = answer(a.ask, Tools(df), client)
        print(f"[{ans.source}] {ans.error or ''}\n{ans.text}")
        return 0
    entry = build_cache(a.decisions, a.out, client)
    for q, e in entry["answers"].items():
        print(f"Q: {q}\n[{e['source']}] {e['text']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

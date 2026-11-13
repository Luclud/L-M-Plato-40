"""Housekeeping & robot timeline for one local day, built only from pipeline inputs/outputs (no Streamlit).

UTC inside, Europe/Vienna for display. ``as_of`` = "only what was known at that moment": roster statuses by
``status_known_utc``, bookings/requests by creation time, decisions by ``timestamp_utc``, events by their time.
"""
from __future__ import annotations

import re

import pandas as pd

from plato40.clock import LOCAL_TZ
from plato40.messaging import parse_requested_time

LANES = ["CLEANER_1", "CLEANER_2", "CLEANER_3", "ROBOT_1", "ROBOT_2", "ONCALL_1"]
PLAN_ACTIONS = ("schedule_cleaning", "dispatch_robot", "call_oncall_housekeeper")
TASK_MIN = 15
CHECKIN_LOCAL = "15:00"
ONCALL_WINDOW = ("15:30", "22:00")
_WINDOW = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*local")
_ROBOT = re.compile(r"(ROBOT_\d+) floor pre-clean (\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_PRECLEAN = re.compile(r"pre-clean\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_ONCALL = re.compile(r"on site ~(\d{1,2}:\d{2}), room clean ~(\d{1,2}:\d{2})")
_SURGE = re.compile(r"\((\d{1,2}:\d{2}) local on\)")
_GUEST = re.compile(r"Guest request \w+ \((\w+)\) for (R\d{3})")
_ROOM = re.compile(r"^R\d{3}$")
_DEMAND = re.compile(r"demand (\d+) min vs capacity (\d+) min")
BLOCK_COLS = ["resource_id", "room", "start", "end", "kind", "label", "episode", "superseded", "known_at"]


def day_bounds(day) -> tuple[pd.Timestamp, pd.Timestamp]:
    s = pd.Timestamp(day).tz_localize(None).normalize()
    return s.tz_localize(LOCAL_TZ).tz_convert("UTC"), (s + pd.Timedelta(days=1)).tz_localize(LOCAL_TZ).tz_convert("UTC")


def at_local(day, hm: str) -> pd.Timestamp:
    h, m = (int(x) for x in hm.split(":"))
    return (pd.Timestamp(day).tz_localize(None).normalize() + pd.Timedelta(hours=h, minutes=m)) \
        .tz_localize(LOCAL_TZ, ambiguous=False, nonexistent="shift_forward").tz_convert("UTC")


def plan_window(reason: str, resource: str | None, day) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Planned [start, end) UTC from the decision text ('CLEANER_3 11:45-12:30 local', robot pre-clean, on-call)."""
    reason = str(reason or "")
    pairs = None
    if resource and str(resource).startswith("ROBOT"):
        m = _ROBOT.search(reason) or _PRECLEAN.search(reason)
        pairs = m.groups()[-2:] if m else None
    elif resource == "ONCALL_1":
        m = _ONCALL.search(reason)
        pairs = m.groups() if m else None
    if pairs is None:
        m = _WINDOW.search(reason)
        pairs = m.groups() if m else None
    if not pairs:
        return None
    a, b = at_local(day, pairs[0]), at_local(day, pairs[1])
    return (a, b if b > a else b + pd.Timedelta(days=1))


def _rooms(assets) -> list[str]:
    return [a for a in (assets or []) if _ROOM.match(str(a))]


def _local_date(t):
    return pd.Timestamp(t).tz_convert(LOCAL_TZ).date()


class Day:
    """All timeline pieces of one local day; build per (dataset, decisions, day), then filter by ``as_of``."""

    def __init__(self, data, df: pd.DataFrame, day):
        self.day = pd.Timestamp(day).date()
        self.start, self.end = day_bounds(self.day)
        self.data, self.df = data, df
        ev = data.events
        self.ev = ev[(ev.timestamp_utc >= self.start - pd.Timedelta(hours=6)) & (ev.timestamp_utc < self.end)]
        self.dec = df[(df.t >= self.start - pd.Timedelta(days=3)) & (df.t < self.end)] if len(df) else df
        self._durations = None

    # ---------------------------------------------------------------- staff
    def shifts(self, as_of=None) -> pd.DataFrame:
        """Shifts overlapping the day as known at ``as_of``: status shows the absence only once it is known."""
        cols = ["resource_id", "start", "end", "status", "known_at", "absent"]
        r = self.data.roster
        r = r[(r.start_utc < self.end) & (r.end_utc > self.start) & r.resource_id.isin(LANES)]
        rows = []
        for x in r.itertuples():
            known = as_of is None or x.status_known_utc <= as_of
            if not known and x.status == "scheduled":
                continue
            status = x.status if known else "scheduled"
            rows.append((x.resource_id, x.start_utc, x.end_utc, status,
                         x.status_known_utc if status != "scheduled" else None, status != "scheduled"))
        if self._oncall_used(as_of) or rows:
            a, b = at_local(self.day, ONCALL_WINDOW[0]), at_local(self.day, ONCALL_WINDOW[1])
            rows.append(("ONCALL_1", a, b, "on_call", None, False))
        return pd.DataFrame(rows, columns=cols)

    def _oncall_used(self, as_of) -> bool:
        return bool(len(self.dec)) and bool(((self.dec.action == "call_oncall_housekeeper")
                                             & (self.dec.t >= self.start)
                                             & ((self.dec.t <= as_of) if as_of is not None else True)).any())

    def absences(self, as_of=None) -> pd.DataFrame:
        s = self.shifts(as_of)
        return s[s.absent].reset_index(drop=True)

    # ---------------------------------------------------------------- actual work
    def actual_blocks(self, as_of=None) -> pd.DataFrame:
        """Pair *_started/_completed (on-call: called/completed) per room + resource in time order."""
        pairs = {"cleaning_started": ("cleaning_completed", "cleaning"),
                 "robot_floor_cleaning_started": ("robot_floor_cleaning_completed", "robot"),
                 "oncall_housekeeping_called": ("cleaning_completed", "oncall")}
        ev = self.ev.sort_values(["timestamp_utc", "event_id"])
        open_, rows = {}, []
        for e in ev.itertuples():
            res = e.resource_id if isinstance(e.resource_id, str) else None
            if e.type in pairs and res:
                open_[(e.room_id, res, pairs[e.type][0])] = (e.timestamp_utc, pairs[e.type][1])
            elif e.type in ("cleaning_completed", "robot_floor_cleaning_completed") and res:
                hit = open_.pop((e.room_id, res, e.type), None)
                if hit and e.timestamp_utc > self.start:
                    rows.append([res, e.room_id, hit[0], e.timestamp_utc, hit[1], f"Room {e.room_id[1:]}", None,
                                 False, e.timestamp_utc])
        out = pd.DataFrame(rows, columns=BLOCK_COLS).astype({"superseded": bool})
        if as_of is not None and len(out):
            out = out[out.start <= as_of].copy()
            out["end"] = out.end.where(out.end <= as_of, as_of)
            out = out[out.end > out.start]
        return out.reset_index(drop=True)

    def median_minutes(self, room: str | None, before) -> float:
        """Median actual cleaning minutes for rooms of that size, completed before ``before`` (causal)."""
        if self._durations is None:
            ev = self.data.events
            st = ev[ev.type == "cleaning_started"][["room_id", "resource_id", "timestamp_utc"]]
            en = ev[ev.type == "cleaning_completed"][["room_id", "resource_id", "timestamp_utc"]]
            m = 1 if len(st) and len(en) else None
            if m is not None:
                st2 = st.rename(columns={"timestamp_utc": "s"}).sort_values("s")
                en2 = en.rename(columns={"timestamp_utc": "e"}).sort_values("e")
                m = pd.merge_asof(en2, st2, left_on="e", right_on="s", by=["room_id", "resource_id"])
                m = m.dropna(subset=["s"])
                size = {r["id"]: r.get("area_m2") for r in self.data.rooms}
                m["size"] = m.room_id.map(size)
                m["min"] = (m.e - m.s).dt.total_seconds() / 60
            self._durations = m if m is not None else pd.DataFrame(columns=["e", "size", "min"])
        d = self._durations
        size = {r["id"]: r.get("area_m2") for r in self.data.rooms}.get(room)
        d = d[d.e < before]
        same = d[d["size"] == size] if size is not None else d
        d = same if len(same) else d
        return float(d["min"].median()) if len(d) else 45.0

    # ---------------------------------------------------------------- plans
    def planned_blocks(self, as_of=None) -> pd.DataFrame:
        """Planned work for the day known at ``as_of``; a later plan for the same room (human/robot) supersedes."""
        dec = self.dec[self.dec.action.isin(PLAN_ACTIONS)] if len(self.dec) else self.dec
        if as_of is not None and len(dec):
            dec = dec[dec.t <= as_of]
        rows = []
        for r in dec.sort_values(["t", "event_id"]).itertuples():
            res = r.resource_id if isinstance(r.resource_id, str) else None
            when = pd.to_datetime(r.scheduled_for_utc, utc=True, errors="coerce") if r.scheduled_for_utc else pd.NaT
            ref = when if pd.notna(when) else r.t
            if _local_date(ref) != self.day or res not in LANES:
                continue
            rooms = _rooms(r.affected_assets)
            reason = str(r.reason)
            g = _GUEST.match(reason)
            if g:
                kind = g.group(1).replace("_", " ").capitalize()
                rows.append([res, g.group(2), ref, ref + pd.Timedelta(minutes=TASK_MIN), "task",
                             f"{kind} · Room {g.group(2)[1:]}", r.episode, f"task:{r.event_id}", r.t])
                continue
            if not rooms:
                m = _SURGE.search(reason)
                if m and res.startswith("ROBOT"):
                    a = at_local(self.day, m.group(1))
                    rows.append([res, None, a, at_local(self.day, "15:00"), "bracket",
                                 f"pre-cleans vacated rooms from {m.group(1)}", r.episode, f"bracket:{res}", r.t])
                continue
            win = plan_window(reason, res, self.day)
            if win is None:
                win = (ref, ref + pd.Timedelta(minutes=self.median_minutes(rooms[0], r.t)))
            lane = "robot" if res.startswith("ROBOT") else "human"
            for room in rooms:
                rows.append([res, room, win[0], win[1], "plan", f"Room {room[1:]}", r.episode,
                             f"{lane}:{room}", r.t])
            m = _ROBOT.search(reason)
            if m and not res.startswith("ROBOT"):  # human plan that also books a robot pre-clean
                a, b = at_local(self.day, m.group(2)), at_local(self.day, m.group(3))
                for room in rooms:
                    rows.append([m.group(1), room, a, b, "plan", f"Room {room[1:]}", r.episode, f"robot:{room}", r.t])
        out = pd.DataFrame(rows, columns=["resource_id", "room", "start", "end", "kind", "label", "episode", "key",
                                          "known_at"])
        if out.empty:
            return pd.DataFrame(columns=BLOCK_COLS).astype({"superseded": bool})
        last = out.groupby("key").known_at.transform("max")
        out["superseded"] = out.known_at < last
        out = out.drop_duplicates(["key", "known_at", "resource_id", "start"], keep="last")
        return out[BLOCK_COLS].reset_index(drop=True)

    # ---------------------------------------------------------------- deadlines, re-plans, summary
    def deadlines(self, as_of=None) -> pd.DataFrame:
        cols = ["kind", "t", "room", "label"]
        rows = []
        bk = self.data.bookings
        arr = bk[(bk.arrival_utc >= self.start) & (bk.arrival_utc < self.end)]
        if "status" in arr:
            arr = arr[arr.status != "cancelled"]
        if as_of is not None:
            arr = arr[arr.created_utc <= as_of]
        if len(arr):
            rows.append(("checkin", at_local(self.day, CHECKIN_LOCAL), None, f"Check-in from {CHECKIN_LOCAL}"
                         f" · {len(arr)} arrival(s)"))
        gr = self.data.guest_requests
        gr = gr[(gr.request_type == "early_checkin") & gr.booking_id.isin(set(arr.booking_id))]
        if as_of is not None:
            gr = gr[gr.timestamp_utc <= as_of]
        for g in gr.itertuples():
            hm = parse_requested_time(g.text)
            if hm:
                rows.append(("early_checkin", at_local(self.day, hm), g.room_id,
                             f"Early check-in requested {hm} · Room {g.room_id[1:]}"))
        ev = self.ev[(self.ev.timestamp_utc >= self.start)]
        if as_of is not None:
            ev = ev[ev.timestamp_utc <= as_of]
        for e in ev[ev.type.isin(["checkout", "checkout_early", "arrival_waiting_room"])].itertuples():
            kind = "waiting" if e.type == "arrival_waiting_room" else "checkout"
            text = "Guest waiting" if kind == "waiting" else "Checked out"
            rows.append((kind, e.timestamp_utc, e.room_id, f"{text} · Room {e.room_id[1:]}"))
        rel = self.dec[(self.dec.action == "release_room") & (self.dec.t >= self.start)] if len(self.dec) else self.dec
        if as_of is not None and len(rel):
            rel = rel[rel.t <= as_of]
        for r in rel.itertuples():
            for room in _rooms(r.affected_assets):
                rows.append(("released", r.t, room, f"Room {room[1:]} released"))
        return pd.DataFrame(rows, columns=cols).sort_values("t").reset_index(drop=True) if rows \
            else pd.DataFrame(columns=cols)

    def replans(self, as_of=None) -> pd.DataFrame:
        cols = ["t", "label", "episodes"]
        d = self.dec[(self.dec.t >= self.start)] if len(self.dec) else self.dec
        if as_of is not None and len(d):
            d = d[d.t <= as_of]
        if not len(d):
            return pd.DataFrame(columns=cols)
        d = d[d.reason.str.startswith("Re-plan") | d.reason.str.contains("daily re-plan", case=False)]
        rows = []
        for t, g in d.groupby("t"):
            why = []
            for reason in g.reason:
                m = re.search(r"\(([^)]*)\)", reason)
                why.append(short_reason(m.group(1) if m else reason))
            rows.append((t, " · ".join(dict.fromkeys(why)), list(dict.fromkeys(g.episode))))
        return pd.DataFrame(rows, columns=cols)

    def day_summary(self, as_of=None) -> dict:
        bk = self.data.bookings
        dep = bk[(bk.departure_utc >= self.start) & (bk.departure_utc < self.end)]
        if "status" in dep:
            dep = dep[dep.status != "cancelled"]
        if as_of is not None:
            dep = dep[dep.created_utc <= as_of]
        rooms = list(dict.fromkeys(dep.room_id))
        demand = sum(self.median_minutes(r, self.start) for r in rooms)
        sh = self.shifts(as_of)
        cl = sh[sh.resource_id.str.startswith("CLEANER") & ~sh.absent]
        staff = float(((cl.end - cl.start).dt.total_seconds() / 60).sum()) if len(cl) else 0.0
        dl = self.deadlines(as_of)
        waited = dl[dl.kind == "waiting"].room.nunique() if len(dl) else 0
        calls = self.dec[(self.dec.action == "call_oncall_housekeeper") & (self.dec.t >= self.start)] \
            if len(self.dec) else self.dec
        if as_of is not None and len(calls):
            calls = calls[calls.t <= as_of]
        return {"rooms": len(rooms), "demand_min": int(round(demand)), "staff_min": int(round(staff)),
                "waited": int(waited), "oncall": int(len(calls)), "absent": int(sh.absent.sum()) if len(sh) else 0,
                "over": demand > staff}


_RES = {"CLEANER_1": "Cleaner 1", "CLEANER_2": "Cleaner 2", "CLEANER_3": "Cleaner 3", "ROBOT_1": "Robot 1",
        "ROBOT_2": "Robot 2", "ONCALL_1": "On-call housekeeper"}


def short_reason(text: str) -> str:
    """'CLEANER_1 sick became known - capacity re-planned' -> 'Cleaner 1 sick became known — re-planned'."""
    t = str(text).strip()
    if re.match(r"daily re-plan", t, re.I):
        return "Daily re-plan " + t.split("re-plan", 1)[1].replace("local", "").strip()
    for code, name in _RES.items():
        t = t.replace(code, name)
    t = re.sub(r"\bR(\d{3})\b", r"Room \1", t)
    t = re.sub(r"\s*-\s*capacity re-planned", "", t).replace(" local", "")
    return f"{t} — re-planned"

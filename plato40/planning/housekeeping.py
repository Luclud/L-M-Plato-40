"""Shared housekeeping planner (ticket T05; used by T05 turnaround, T06 surge, T08 logistics).

Who cleans which room when - with only the capacity that is *known* at planning time t.

Design
------
The core is pure and unit-testable: :func:`schedule` takes plain data (tasks, resource windows,
already-booked intervals) and returns a :class:`Plan`. Everything that touches the dataset is a thin,
causal helper around it:

* :func:`cleaning_records` / :func:`learn_durations` - cleaning minutes per room size, human only vs.
  after a robot floor pre-clean, learned from ``cleaning_started/_completed`` events that were
  *completed* by t (falls back to documented priors while there is no history).
* :func:`resource_windows` - scheduled shifts from a roster *as known at t* (``AsOf.roster()``), so a
  sick call known at 05:15 removes capacity from 05:15 on, but not before.
* :func:`build_demand` - rooms that need a turnover on a local day, with the deadline of the next
  arrival (booked 15:00 local or a requested early check-in) - from stays, housekeeping state and
  bookings known at t.
* :func:`plan_day` - convenience wrapper: windows from the as-of roster + :func:`schedule`.
* :func:`oncall_slot` - earliest slot of the on-call housekeeper (15:30-22:00 local, ~45 min travel).

Greedy policy (documented assumption): waiting guests first, then earliest deadline (earliest
arrival / requested early check-in), then earliest ready time. Each task goes to the cleaner that
finishes it first; a robot floor pre-clean is used when it makes the room ready no later than
human-only cleaning (it saves human minutes). Robots only start after the room is vacant
(``ready_from``) and never certify/release a room. Tasks that do not fit into the known windows are
reported as ``unplanned`` with a ``shortfall_minutes`` figure - capacity is never invented.

Times are UTC ``pd.Timestamp``; local (Europe/Vienna, DST) only via :mod:`plato40.clock`.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import pandas as pd

from ..clock import at_local, local

STEP = pd.Timedelta(minutes=15)
CHECKIN_LOCAL = "15:00"           # building.json operations
CHECKOUT_LOCAL = "11:00"
ONCALL_LOCAL = ("15:30", "22:00")  # building.json staff.notes.on_call_housekeeping
ONCALL_TRAVEL = pd.Timedelta(minutes=45)
# Priors used only until the dataset being run has enough completed cleanings (per size class).
PRIOR_MINUTES = {False: 60, True: 45}   # human-only / after robot floor pre-clean
PRIOR_ROBOT_MINUTES = 30
MIN_SAMPLES = 10

Interval = tuple[pd.Timestamp, pd.Timestamp]


# ---------------------------------------------------------------------------
# data classes
@dataclass(frozen=True)
class CleaningTask:
    """One room that needs a turnover.

    ready_from   earliest time a cleaner may enter (vacant: now; occupied: expected departure)
    deadline     when the next guest expects the room (None = no arrival -> lowest priority)
    minutes      human cleaning minutes without / with robot floor pre-clean
    waiting      a guest is already waiting for this room (top priority)
    assigned_to  cleaning already in progress by this resource (fixed slot, starts at ``started``)
    robot_ok     a robot may pre-clean the floor (room vacant from ready_from on)
    """

    room: str
    ready_from: pd.Timestamp
    deadline: pd.Timestamp | None = None
    minutes: int = 60
    minutes_with_robot: int | None = None
    waiting: bool = False
    robot_ok: bool = True
    assigned_to: str | None = None
    started: pd.Timestamp | None = None
    note: str = ""


@dataclass(frozen=True)
class Slot:
    room: str
    resource: str
    start: pd.Timestamp
    end: pd.Timestamp
    kind: str = "clean"   # "clean" (human, makes the room clean) | "robot_floor" (pre-clean only)


@dataclass
class Plan:
    t: pd.Timestamp
    slots: list[Slot] = field(default_factory=list)
    ready_at: dict[str, pd.Timestamp] = field(default_factory=dict)   # room -> planned clean time
    unplanned: list[str] = field(default_factory=list)                # rooms that do not fit (shortfall)
    late: dict[str, float] = field(default_factory=dict)              # room -> minutes after its deadline
    demand_minutes: float = 0.0
    capacity_minutes: float = 0.0
    shortfall_minutes: float = 0.0
    resources: list[str] = field(default_factory=list)                # resources with known windows

    def slot_for(self, room: str, kind: str = "clean") -> Slot | None:
        for s in self.slots:
            if s.room == room and s.kind == kind:
                return s
        return None

    @property
    def has_shortfall(self) -> bool:
        return bool(self.unplanned)

    def summary(self) -> str:
        return (f"{len(self.ready_at)} rooms planned, {len(self.unplanned)} unplanned, "
                f"{len(self.late)} late; demand {self.demand_minutes:.0f} min vs capacity "
                f"{self.capacity_minutes:.0f} min ({', '.join(self.resources) or 'no staff'})")


@dataclass(frozen=True)
class CleaningDurations:
    """Learned human cleaning minutes per room area (m2) and robot pre-clean flag."""

    by_area: Mapping[tuple[int, bool], int]
    pooled: Mapping[bool, int]
    robot_minutes: int = PRIOR_ROBOT_MINUTES
    area_of: Mapping[str, int] = field(default_factory=dict)
    samples: int = 0

    def minutes(self, room: str, robot_pre_clean: bool = False) -> int:
        a = self.area_of.get(room)
        if (a, robot_pre_clean) in self.by_area:
            return int(self.by_area[(a, robot_pre_clean)])
        return int(self.pooled.get(robot_pre_clean, PRIOR_MINUTES[robot_pre_clean]))


# ---------------------------------------------------------------------------
# time helpers
def ceil_step(ts: pd.Timestamp, step: pd.Timedelta = STEP) -> pd.Timestamp:
    return ts.ceil(step)


def _ceil_minutes(x: float, step: int = 15) -> int:
    return int(math.ceil(x / step) * step)


def local_day(ts: pd.Timestamp) -> _dt.date:
    return local(ts).date()


def day_bounds(day: _dt.date) -> Interval:
    """UTC [start, end) of a local calendar day (DST-safe)."""
    start = at_local(pd.Timestamp(day).tz_localize("Europe/Vienna"), "00:00")
    nxt = at_local(pd.Timestamp(day + _dt.timedelta(days=1)).tz_localize("Europe/Vienna"), "00:00")
    return start, nxt


def at_local_day(day: _dt.date, hhmm: str) -> pd.Timestamp:
    return at_local(pd.Timestamp(day).tz_localize("Europe/Vienna") + pd.Timedelta(hours=12), hhmm)


# ---------------------------------------------------------------------------
# learning
def cleaning_records(events: pd.DataFrame) -> pd.DataFrame:
    """Pair cleaning_started/_completed (same room + resource) -> columns room_id, resource_id,
    start, end, minutes, robot_pre_clean. Pure; filter by ``end <= t`` before learning."""
    cols = ["room_id", "resource_id", "start", "end", "minutes", "robot_pre_clean"]
    ev = events[["timestamp_utc", "type", "room_id", "resource_id"]] if "resource_id" in events else None
    if ev is None or ev.empty:
        return pd.DataFrame(columns=cols)
    s = ev[ev.type == "cleaning_started"].rename(columns={"timestamp_utc": "start"}).sort_values("start")
    c = ev[ev.type == "cleaning_completed"].rename(columns={"timestamp_utc": "end"}).sort_values("end")
    if s.empty or c.empty:
        return pd.DataFrame(columns=cols)
    m = pd.merge_asof(s[["start", "room_id", "resource_id"]], c[["end", "room_id", "resource_id"]],
                      left_on="start", right_on="end", by=["room_id", "resource_id"], direction="forward",
                      tolerance=pd.Timedelta(hours=6))
    m = m.dropna(subset=["end"])
    m["minutes"] = (m.end - m.start).dt.total_seconds() / 60
    rb = ev[ev.type == "robot_floor_cleaning_completed"][["timestamp_utc", "room_id"]].rename(
        columns={"timestamp_utc": "rob"}).sort_values("rob")
    if rb.empty:
        m["robot_pre_clean"] = False
    else:
        m = pd.merge_asof(m.sort_values("start"), rb, left_on="start", right_on="rob", by="room_id",
                          direction="backward", tolerance=pd.Timedelta(hours=8))
        m["robot_pre_clean"] = m.rob.notna()
    return m[cols].sort_values("end").reset_index(drop=True)


def robot_records(events: pd.DataFrame) -> pd.Series:
    """Minutes of robot floor cleanings indexed by completion time (pure)."""
    ev = events[events.type.isin(["robot_floor_cleaning_started", "robot_floor_cleaning_completed"])]
    if ev.empty:
        return pd.Series(dtype=float)
    s = ev[ev.type.str.endswith("started")].rename(columns={"timestamp_utc": "start"}).sort_values("start")
    c = ev[ev.type.str.endswith("completed")].rename(columns={"timestamp_utc": "end"}).sort_values("end")
    m = pd.merge_asof(s[["start", "room_id"]], c[["end", "room_id"]], left_on="start", right_on="end",
                      by="room_id", direction="forward", tolerance=pd.Timedelta(hours=3)).dropna()
    return pd.Series(((m.end - m.start).dt.total_seconds() / 60).values, index=m.end).sort_index()


def learn_durations(records: pd.DataFrame, rooms: list[dict], t: pd.Timestamp, *,
                    robot: pd.Series | None = None, min_samples: int = MIN_SAMPLES) -> CleaningDurations:
    """Median cleaning minutes (rounded up to 15) of cleanings *completed by t*.

    Per (room area, robot pre-clean) if >= min_samples, else pooled over areas, else PRIOR_MINUTES.
    ``rooms`` = building.json rooms (needs ``id`` and ``area_m2``)."""
    area_of = {r["id"]: int(r.get("area_m2") or 0) for r in rooms}
    rec = records[records.end <= t] if len(records) else records
    by_area: dict[tuple[int, bool], int] = {}
    pooled: dict[bool, int] = dict(PRIOR_MINUTES)
    if len(rec):
        rec = rec.assign(area=rec.room_id.map(area_of))
        for flag in (False, True):
            sub = rec[rec.robot_pre_clean == flag]
            if len(sub) >= min_samples:
                pooled[flag] = _ceil_minutes(sub.minutes.median())
            for a, g in sub.groupby("area"):
                if len(g) >= min_samples:
                    by_area[(int(a), flag)] = _ceil_minutes(g.minutes.median())
    rmin = PRIOR_ROBOT_MINUTES
    if robot is not None and len(robot):
        r = robot[robot.index <= t]
        if len(r) >= min_samples:
            rmin = _ceil_minutes(r.median())
    return CleaningDurations(by_area=by_area, pooled=pooled, robot_minutes=rmin, area_of=area_of,
                             samples=int(len(rec)))


# ---------------------------------------------------------------------------
# capacity
def resource_windows(roster: pd.DataFrame, resources: Iterable[str], start: pd.Timestamp,
                     end: pd.Timestamp) -> dict[str, list[Interval]]:
    """Scheduled shift intervals of ``resources`` clipped to [start, end).

    ``roster`` must be the roster *as known at planning time* (``AsOf(data, t).roster()``): absences
    that are not known yet still look 'scheduled' (and are re-planned once they become known)."""
    res = list(resources)
    out: dict[str, list[Interval]] = {r: [] for r in res}
    if roster is None or roster.empty or end <= start:
        return out
    r = roster[roster.resource_id.isin(res) & (roster.status == "scheduled")
               & (roster.start_utc < end) & (roster.end_utc > start)]
    for row in r.sort_values("start_utc").itertuples(index=False):
        out[row.resource_id].append((max(row.start_utc, start), min(row.end_utc, end)))
    return out


def oncall_slot(t: pd.Timestamp, minutes: int, busy_until: pd.Timestamp | None = None,
                travel: pd.Timedelta = ONCALL_TRAVEL) -> Slot | None:
    """Earliest on-call cleaning slot from t (arrival on site after ``travel``), inside 15:30-22:00 local
    of t's local day; None if the call cannot be made now or the cleaning would not finish by 22:00."""
    lo, hi = at_local(t, ONCALL_LOCAL[0]), at_local(t, ONCALL_LOCAL[1])
    if not (lo <= t < hi):
        return None
    start = ceil_step(max(t + travel, busy_until or t))
    end = start + pd.Timedelta(minutes=minutes)
    if end > hi:
        return None
    return Slot(room="", resource="ONCALL_1", start=start, end=end, kind="clean")


def _earliest_fit(windows: list[Interval], booked: list[Interval], earliest: pd.Timestamp,
                  minutes: int) -> pd.Timestamp | None:
    dur = pd.Timedelta(minutes=minutes)
    for ws, we in windows:
        s = ceil_step(max(ws, earliest))
        for bs, be in booked:
            if be <= s:
                continue
            if bs >= s + dur:
                break
            s = ceil_step(max(s, be))
        if s + dur <= we:
            return s
    return None


def _book(booked: dict[str, list[Interval]], res: str, start: pd.Timestamp, end: pd.Timestamp) -> None:
    lst = booked.setdefault(res, [])
    lst.append((start, end))
    lst.sort()


def _window_minutes(windows: Mapping[str, list[Interval]], t: pd.Timestamp) -> float:
    tot = 0.0
    for ivs in windows.values():
        for a, b in ivs:
            a = max(a, t)
            if b > a:
                tot += (b - a).total_seconds() / 60
    return tot


def task_order(task: CleaningTask) -> tuple:
    """Greedy priority: waiting guests, then earliest deadline, then earliest ready time."""
    far = pd.Timestamp.max.tz_localize("UTC")
    return (not task.waiting, task.deadline if task.deadline is not None else far, task.ready_from, task.room)


# ---------------------------------------------------------------------------
# core scheduler (pure)
def schedule(t: pd.Timestamp, tasks: Iterable[CleaningTask], cleaner_windows: Mapping[str, list[Interval]],
             robot_windows: Mapping[str, list[Interval]] | None = None, *,
             booked: Mapping[str, list[Interval]] | None = None, robot_minutes: int = PRIOR_ROBOT_MINUTES) -> Plan:
    """Greedy deadline-first schedule of ``tasks`` on the given resource windows, starting at t.

    cleaner_windows / robot_windows: resource -> list of (start, end) when it may work (as known at t).
    booked: resource -> intervals already taken (e.g. robot bin runs from T08); they are respected and
    the plan's own slots are added to a copy. Returns a :class:`Plan`; never mutates its inputs.
    """
    t0 = ceil_step(t)
    tasks = sorted(tasks, key=task_order)
    robot_windows = robot_windows or {}
    book: dict[str, list[Interval]] = {k: sorted(v) for k, v in (booked or {}).items()}
    plan = Plan(t=t, resources=sorted(r for r, w in cleaner_windows.items() if w))
    plan.capacity_minutes = _window_minutes(cleaner_windows, t0) - sum(
        _window_minutes({r: v}, t0) for r, v in book.items() if r in cleaner_windows)
    # cleanings already in progress are fixed: book them before anything else
    for task in tasks:
        if task.assigned_to:
            st = task.started or task.ready_from
            end = max(ceil_step(st + pd.Timedelta(minutes=int(task.minutes))), t0 + STEP)
            plan.slots.append(Slot(task.room, task.assigned_to, st, end))
            _book(book, task.assigned_to, st, end)
            plan.ready_at[task.room] = end
    for task in tasks:
        m_h = int(task.minutes)
        m_r = int(task.minutes_with_robot or task.minutes)
        plan.demand_minutes += m_h
        if not task.assigned_to:
            earliest = max(t0, ceil_step(task.ready_from))
            best = None  # (finish, human_minutes, slots)
            for c, wins in cleaner_windows.items():
                s = _earliest_fit(wins, book.get(c, []), earliest, m_h)
                if s is not None:
                    cand = (s + pd.Timedelta(minutes=m_h), m_h, [Slot(task.room, c, s, s + pd.Timedelta(minutes=m_h))])
                    if best is None or cand[:2] < best[:2]:
                        best = cand
            if task.robot_ok and robot_windows and m_r < m_h:
                for rb, rwins in robot_windows.items():
                    rs = _earliest_fit(rwins, book.get(rb, []), earliest, robot_minutes)
                    if rs is None:
                        continue
                    re_ = rs + pd.Timedelta(minutes=robot_minutes)
                    for c, wins in cleaner_windows.items():
                        s = _earliest_fit(wins, book.get(c, []), re_, m_r)
                        if s is None:
                            continue
                        cand = (s + pd.Timedelta(minutes=m_r), m_r,
                                [Slot(task.room, rb, rs, re_, "robot_floor"),
                                 Slot(task.room, c, s, s + pd.Timedelta(minutes=m_r))])
                        if best is None or cand[:2] <= best[:2]:
                            best = cand
            if best is None:
                plan.unplanned.append(task.room)
                plan.shortfall_minutes += m_h
                continue
            for sl in best[2]:
                plan.slots.append(sl)
                _book(book, sl.resource, sl.start, sl.end)
            plan.ready_at[task.room] = best[0]
        if task.deadline is not None and plan.ready_at[task.room] > task.deadline:
            plan.late[task.room] = (plan.ready_at[task.room] - task.deadline).total_seconds() / 60
    return plan


# ---------------------------------------------------------------------------
# dataset-facing helpers (causal)
def cleaning_in_progress(events: pd.DataFrame) -> dict[str, tuple[str, pd.Timestamp]]:
    """room -> (resource, started) for cleanings started but not completed in ``events`` (as of t)."""
    ev = events[events.type.isin(["cleaning_started", "cleaning_completed"])]
    out: dict[str, tuple[str, pd.Timestamp]] = {}
    for row in ev.itertuples(index=False):
        if row.type == "cleaning_started":
            out[row.room_id] = (row.resource_id, row.timestamp_utc)
        else:
            out.pop(row.room_id, None)
    return out


def arrivals_on(bookings: pd.DataFrame, day: _dt.date) -> pd.DataFrame:
    """Confirmed bookings (as known) arriving on local ``day``; first arrival per room."""
    b = bookings[bookings.status != "cancelled"]
    lo, hi = day_bounds(day)
    b = b[(b.arrival_utc >= lo) & (b.arrival_utc < hi)]
    return b.sort_values("arrival_utc").drop_duplicates("room_id")


def build_demand(t: pd.Timestamp, state, asof, day: _dt.date, durations: CleaningDurations, *,
                 deadlines: Mapping[str, pd.Timestamp] | None = None,
                 departures: Mapping[str, pd.Timestamp] | None = None,
                 waiting: Iterable[str] = (), in_progress: Mapping[str, tuple[str, pd.Timestamp]] | None = None,
                 rooms: Iterable[str] | None = None) -> list[CleaningTask]:
    """Rooms needing a turnover by the end of local ``day``, as known at t.

    - vacant and not clean (dirty / in_progress / unknown) -> ready now
    - occupied by a stay whose booking departs on/before ``day`` -> ready at the expected departure
      (booking departure = 11:00 local check-out deadline, or an agreed late check-out in ``departures``)
    Deadline = ``deadlines[room]`` (e.g. requested early check-in) else the booked arrival on ``day``
    (15:00 local) else None. Stays come from events (presence=0 does not mean vacant).
    """
    deadlines = deadlines or {}
    departures = departures or {}
    waiting = set(waiting)
    in_progress = in_progress or {}
    bk = asof.bookings()
    arr = arrivals_on(bk, day).set_index("room_id")
    dep_by_booking = bk.set_index("booking_id").departure_utc if len(bk) else pd.Series(dtype=object)
    dep_by_booking = dep_by_booking[~dep_by_booking.index.duplicated()]
    _, day_end = day_bounds(day)
    tasks = []
    for room in (rooms if rooms is not None else state.d.room_ids):
        stay = state.stay_at(room, t)
        if stay is not None:
            dep = departures.get(room)
            if dep is None:
                dep = dep_by_booking.get(stay.booking_id)
            if dep is None or pd.isna(dep) or dep >= day_end:
                continue  # stays over (or unknown departure): no turnover that day
            ready = max(t, dep)
            note = "occupied, expected departure"
        else:
            hk = state.housekeeping(room, t)
            if hk == "clean":
                continue
            ready = t
            note = f"vacant, housekeeping_state={hk}"
        dl = deadlines.get(room)
        if dl is None and room in arr.index:
            dl = arr.loc[room, "arrival_utc"]
        ip = in_progress.get(room) if stay is None else None
        tasks.append(CleaningTask(
            room=room, ready_from=ready, deadline=dl,
            minutes=durations.minutes(room, False), minutes_with_robot=durations.minutes(room, True),
            waiting=room in waiting, robot_ok=True,
            assigned_to=ip[0] if ip else None, started=ip[1] if ip else None, note=note))
    return tasks


def plan_day(t: pd.Timestamp, state, asof, demand: list[CleaningTask], *, day: _dt.date | None = None,
             booked: Mapping[str, list[Interval]] | None = None, use_robots: bool = True,
             robot_minutes: int = PRIOR_ROBOT_MINUTES) -> Plan:
    """Plan ``demand`` for the local day (default: day of t) with cleaners/robots as known at t."""
    day = day or local_day(t)
    lo, hi = day_bounds(day)
    start = max(t, lo)
    roster = asof.roster()
    roles = state.d.resources
    cleaners = [r for r, role in roles.items() if role == "cleaner"]
    robots = [r for r, role in roles.items() if role == "robot"] if use_robots else []
    cw = resource_windows(roster, cleaners, start, hi)
    rw = resource_windows(roster, robots, start, hi) if robots else {}
    return schedule(t, demand, cw, rw, booked=booked, robot_minutes=robot_minutes)

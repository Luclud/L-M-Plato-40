"""S05 The Unexpected Weekend - event-weekend (occupancy surge) agent (ticket T06).

An event weekend fills the house while a cleaner calls in sick. The agent looks at the next days with
the bookings and roster *as known at t* and opens one ``occupancy_surge`` episode per surge
(``surge:<first local date>``) when either

* **A - planned surge** (daily at 13:00 local, for the next ``LOOKAHEAD_DAYS`` days): a busy arrival day
  (arrivals >= busy threshold) whose same-day turnovers (rooms that check out and check in on the same
  day) exceed what the scheduled cleaners can turn in the window between booked check-out (11:00) and
  check-in (15:00), using learned cleaning minutes after a robot floor pre-clean, or
* **B - capacity lost during a surge** (the moment a cleaner absence becomes known, by
  ``status_known_utc``): the absence falls on a busy arrival day or the day after one.

Decisions in the episode: ``request_human_approval`` for an extra cleaner shift (notable cost - capacity
is never invented, the shortfall is stated in rooms), ``dispatch_robot`` floor pre-cleaning of vacated
rooms (robots on shift as known, never in occupied rooms, they cannot release rooms), and a re-plan
follow-up when the roster changes. Guest ETAs, the room-level schedule and the on-call housekeeper for
waiting guests stay with the turnaround agent (same planner, ``plato40.planning``), so every guest gets
one answer.

Busy threshold = max(BUSY_SHARE x rooms, p95 of daily arrivals over the previous BUSY_LOOKBACK days),
learned from the dataset being run. Causality: bookings by ``created_utc`` (``ctx.asof.bookings()``),
roster by ``status_known_utc`` (``ctx.asof.roster()``), cleaning minutes from cleanings completed by t.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

import pandas as pd

from ..clock import iso, local_hm
from ..decision import make_decision
from ..planning import (CHECKIN_LOCAL, CHECKOUT_LOCAL, at_local_day, cleaning_records, day_bounds,
                        learn_durations, local_day, resource_windows, robot_records)
from .base import TriggerDetector

ISSUE = "occupancy_surge"
DAILY_LOCAL = "13:00"          # after the day-ahead tariff publication; ~2 days before an arrival peak
LOOKAHEAD_DAYS = 2
MERGE_DAYS = 3                 # surge days this close belong to one event weekend (one episode)
BUSY_SHARE = 0.45              # floor: a day on which almost half of the house arrives is busy
BUSY_LOOKBACK = pd.Timedelta(days=90)
BUSY_QUANTILE = 0.95
MIN_HISTORY_DAYS = 14          # fewer known arrival days -> use the floor only
ROBOT_LOCAL = CHECKOUT_LOCAL   # robots pre-clean floors of rooms vacated by check-out
ONE_DAY = _dt.timedelta(days=1)


def _minutes(a: str, b: str) -> int:
    return (int(b[:2]) * 60 + int(b[3:])) - (int(a[:2]) * 60 + int(a[3:]))


TURN_WINDOW_MIN = _minutes(CHECKOUT_LOCAL, CHECKIN_LOCAL)   # booked check-out -> check-in (240 min)


def busy_threshold(daily_arrivals: pd.Series, n_rooms: int) -> float:
    """Busy-day threshold from past daily arrival counts (causal input) and the house size."""
    floor = BUSY_SHARE * n_rooms
    if len(daily_arrivals) < MIN_HISTORY_DAYS:
        return floor
    return max(floor, float(daily_arrivals.quantile(BUSY_QUANTILE)))


def window_capacity_rooms(n_cleaners: int, minutes_per_room: int) -> float:
    """Rooms the scheduled cleaners can turn between booked check-out and check-in."""
    return n_cleaners * TURN_WINDOW_MIN / max(minutes_per_room, 1)


@dataclass
class DayView:
    day: _dt.date
    arrivals: int
    departures: int
    same_day: int
    cleaners: list[str]
    robots: list[str]
    minutes: int
    capacity_rooms: float
    busy: float

    @property
    def shortfall_rooms(self) -> int:
        return max(0, int(round(self.same_day - self.capacity_rooms + 0.49)))

    @property
    def is_busy(self) -> bool:
        return self.arrivals >= self.busy

    @property
    def tight(self) -> bool:
        return self.is_busy and self.same_day > self.capacity_rooms


@dataclass
class Episode:
    key: str
    first: _dt.date
    last: _dt.date
    days: set = field(default_factory=set)
    robot_days: set = field(default_factory=set)
    robot_by_day: dict = field(default_factory=dict)  # day -> robot booked for the floor pre-clean
    asked: dict = field(default_factory=dict)      # day -> extra-staff shortfall already requested
    lost: set = field(default_factory=set)         # shift_ids already handled


class SurgeAgent(TriggerDetector):
    name = "surge"

    def build_triggers(self, data, state):
        self.data = data
        self.records = cleaning_records(data.events)
        self.robot_rec = robot_records(data.events)
        self.n_rooms = len(data.room_ids)
        self.cleaners = [r for r, role in data.resources.items() if role == "cleaner"]
        self.robots = [r for r, role in data.resources.items() if role == "robot"]
        self.eps: dict[str, Episode] = {}
        day = local_day(data.start)
        while True:
            ts = at_local_day(day, DAILY_LOCAL)
            if ts >= data.end:
                break
            if ts >= data.start:
                yield ts, "daily", day
            day = day + ONE_DAY
        r = data.roster
        ch = r[r.role.isin(["cleaner", "robot"]) & (r.status != "scheduled") & r.status_known_utc.notna()
               & (r.status_known_utc >= data.start) & (r.status_known_utc < data.end)]
        for row in ch.itertuples(index=False):
            yield row.status_known_utc, "roster", row

    # ------------------------------------------------------------- as-of views
    def _view(self, ctx, day) -> DayView:
        t = ctx.t
        bk = ctx.asof.bookings()
        bk = bk[bk.status != "cancelled"]
        lo, hi = day_bounds(day)
        arr = bk[(bk.arrival_utc >= lo) & (bk.arrival_utc < hi)]
        dep = bk[(bk.departure_utc >= lo) & (bk.departure_utc < hi)]
        past = bk[(bk.arrival_utc <= t) & (bk.arrival_utc > t - BUSY_LOOKBACK)]
        daily = past.groupby(past.arrival_utc.dt.tz_convert("Europe/Vienna").dt.date).size()
        roster = ctx.asof.roster()
        cw = resource_windows(roster, self.cleaners, lo, hi)
        rw = resource_windows(roster, self.robots, lo, hi)
        dur = learn_durations(self.records, self.data.rooms, t, robot=self.robot_rec)
        minutes = int(dur.pooled[True])
        cleaners = sorted(c for c, w in cw.items() if w)
        return DayView(day=day, arrivals=int(arr.room_id.nunique()), departures=int(dep.room_id.nunique()),
                       same_day=len(set(arr.room_id) & set(dep.room_id)), cleaners=cleaners,
                       robots=sorted(r for r, w in rw.items() if w), minutes=minutes,
                       capacity_rooms=window_capacity_rooms(len(cleaners), minutes),
                       busy=busy_threshold(daily, self.n_rooms))

    def _evidence(self, ctx, v: DayView) -> list[str]:
        return [f"bookings.csv:BUILDING:{iso(ctx.t)}:{v.day} {v.arrivals} arrivals, {v.departures} departures, "
                f"{v.same_day} same-day turnovers (bookings created by t; busy >= {v.busy:.0f})",
                f"staff_roster.csv:{','.join(v.cleaners) or 'no cleaner'}:{iso(ctx.t)}:scheduled cleaners on {v.day} "
                f"as known at t; robots {','.join(v.robots) or 'none'}"]

    def _summary(self, v: DayView) -> str:
        return (f"{v.day}: {v.arrivals} arrivals, {v.same_day} rooms check out and in the same day; "
                f"{len(v.cleaners)} cleaner(s) known ({', '.join(v.cleaners) or 'none'}) can turn "
                f"~{v.capacity_rooms:.1f} rooms between {CHECKOUT_LOCAL} and {CHECKIN_LOCAL} local at "
                f"~{v.minutes} min/room (learned, after robot floor pre-clean)")

    # ---------------------------------------------------------------- episodes
    def _episode(self, ctx, day) -> tuple[Episode, bool]:
        for ep in self.eps.values():
            if ctx.log.is_open(ep.key) and abs((day - ep.last).days) <= MERGE_DAYS:
                ep.last = max(ep.last, day)
                ep.days.add(day)
                return ep, False
        key = f"surge:{day}"
        ep = Episode(key=key, first=day, last=day, days={day})
        self.eps[key] = ep
        return ep, True

    def _add(self, ctx, ep, action, priority, reason, assets, **kw):
        d = make_decision(t=ctx.t, affected_assets=assets, issue_type=ISSUE, action=action, priority=priority,
                          reason=reason, agent=self.name, **kw)
        return ctx.log.add(d, episode_key=ep.key, prefix="SU")

    def _ask_staff(self, ctx, ep, v: DayView, why: str, priority: str, deps=None):
        need = max(1, v.shortfall_rooms)
        if ep.asked.get(v.day, -1) >= need and not why.startswith("capacity lost"):
            return None
        ep.asked[v.day] = need
        return self._add(
            ctx, ep, "request_human_approval", priority,
            f"{why}. {self._summary(v)} -> short by ~{need} room turnover(s). Request approval for an extra "
            f"cleaner shift on {v.day} (notable cost; capacity is not assumed until approved). Prioritisation until "
            "then: guests already waiting, then early check-in requests, then arrivals with long stays; the "
            "on-call housekeeper stays reserved for waiting guests 15:30-22:00 local.",
            ["BUILDING"] + v.cleaners, notable_cost=True, evidence=self._evidence(ctx, v),
            dependencies=deps or [], scheduled_for_utc=at_local_day(v.day, CHECKOUT_LOCAL),
            expected_outcome=f"Enough cleaning capacity on {v.day} that no arriving guest waits for a room",
            verification_plan=(f"Approval answered before {v.day} 07:00 local; on {v.day} no arrival_waiting_room "
                               "events and every arrival room clean by 15:00 local, else re-plan / escalate"),
            confidence=0.65)

    def _robots(self, ctx, ep, v: DayView):
        if v.day in ep.robot_days:
            return None
        when = at_local_day(v.day, ROBOT_LOCAL)
        free = [r for r in v.robots if ctx.state.available(r, when, known_at=ctx.t)[0]]
        if not free:
            return None
        ep.robot_days.add(v.day)
        ep.robot_by_day[v.day] = free[0]
        return self._add(
            ctx, ep, "dispatch_robot", "medium",
            f"Surge day {v.day}: robot floor pre-cleaning of rooms as soon as they are vacated by check-out "
            f"({ROBOT_LOCAL} local on), so cleaners only do bathrooms/beds (~{v.minutes} min/room learned). "
            "Robots never enter occupied rooms and cannot release a room.",
            ["BUILDING", free[0]], resource_id=free[0], scheduled_for_utc=when, evidence=self._evidence(ctx, v),
            expected_outcome="Human minutes per turnover reduced on the surge day (planned, not yet executed)",
            verification_plan="robot_floor_cleaning_completed events for vacated rooms before the cleaner starts",
            confidence=0.6)

    # ------------------------------------------------------------------ handle
    def handle(self, ctx, ts, kind, payload):
        if kind == "daily":
            self._daily(ctx, payload)
        elif kind == "roster":
            self._roster(ctx, payload)

    def _daily(self, ctx, today):
        for key, ep in list(self.eps.items()):
            if today > ep.last + ONE_DAY:
                ctx.log.close(key)
                self.eps.pop(key)
        for k in range(1, LOOKAHEAD_DAYS + 1):
            v = self._view(ctx, today + k * ONE_DAY)
            if not v.tight:
                continue
            ep, new = self._episode(ctx, v.day)
            d = self._ask_staff(ctx, ep, v, f"Occupancy surge ahead (seen {k} day(s) before)",
                                "high" if k == 1 or new else "medium")
            self._robots(ctx, ep, v)
            if d is None and new:
                self.eps.pop(ep.key, None)

    def _robot_lost(self, ctx, row):
        """A robot booked for a surge day became unavailable (known now): re-plan in the same episode."""
        sday = local_day(row.start_utc)
        for ep in list(self.eps.values()):
            if not ctx.log.is_open(ep.key) or ep.robot_by_day.get(sday) != row.resource_id:
                continue
            ep.robot_days.discard(sday)
            ep.robot_by_day.pop(sday, None)
            v = self._view(ctx, sday)
            ev = [f"staff_roster.csv:{row.resource_id}:{iso(row.status_known_utc)}:{row.shift_id} {row.status}"]
            d = self._robots(ctx, ep, v)
            if d is not None:
                d.reason = (f"Re-plan: {row.resource_id} {row.status} on {sday} (known {local_hm(row.status_known_utc)} "
                            f"local) -> floor pre-clean moved to {d.resource_id}. ") + d.reason
                d.evidence = ev + d.evidence
                continue
            self._add(
                ctx, ep, "defer_action", "high",
                f"Re-plan: {row.resource_id} {row.status} on {sday} (known {local_hm(row.status_known_utc)} local) and no "
                f"other robot on shift -> robot floor pre-clean cancelled; cleaners need the full human time per room "
                f"(~{self._view(ctx, sday).minutes} min with robot, more without). {self._summary(v)}",
                ["BUILDING", row.resource_id], evidence=ev + self._evidence(ctx, v),
                expected_outcome="Plan no longer relies on an unavailable robot; extra-staff request stays open",
                verification_plan=f"no dispatch_robot for {row.resource_id} on {sday}; arrivals clean by 15:00 local",
                confidence=0.6)

    def _roster(self, ctx, row):
        if row.end_utc <= ctx.t:
            return
        if row.role == "robot":
            self._robot_lost(ctx, row)
            return
        sday = local_day(row.start_utc)
        if not (local_day(ctx.t) <= sday <= local_day(ctx.t) + ONE_DAY):
            return
        for day in (sday, sday - ONE_DAY):
            v = self._view(ctx, day)
            if not v.is_busy:
                continue
            ep, _ = self._episode(ctx, day)
            if row.shift_id in ep.lost:
                return
            ep.lost.add(row.shift_id)
            vs = self._view(ctx, sday) if day != sday else v
            why = (f"capacity lost during the surge: {row.resource_id} {row.status} for shift {row.shift_id} "
                   f"({local_hm(row.start_utc)}-{local_hm(row.end_utc)} local), known {local_hm(row.status_known_utc)} "
                   "local - re-planned with the remaining staff")
            ev = [f"staff_roster.csv:{row.resource_id}:{iso(row.status_known_utc)}:{row.shift_id} {row.status}"]
            d = self._ask_staff(ctx, ep, vs, why, "critical" if sday == local_day(ctx.t) else "high")
            if d is not None:
                d.evidence = ev + d.evidence
            self._robots(ctx, ep, vs)
            return

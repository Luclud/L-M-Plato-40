"""S01 The Perfect Checkout - turnaround agent (ticket T05).

Early departure + early check-in request + tight cleaning shift -> turnaround conflict.
Plans cleaning with the shared housekeeping planner (``plato40.planning``), gives guests a realistic
ETA (never earlier than the planned cleaning end), re-plans when capacity changes (sick cleaner /
robot out of service becoming known, early check-outs, daily 06:30 local), calls the on-call
housekeeper only for guests already waiting inside 15:30-22:00 local, proposes a room swap when a
waiting guest would wait > 1 h and a clean vacant room of the same type is free, and releases a room
only when ``State.can_release`` is true at that tick.

What counts as a turnaround conflict (one episode ``turnaround:<room>:<local date>``):
  * an early check-in request for a room that still needs a turnover on the arrival day
    (occupied by a departing guest, or vacant but not clean), or
  * a guest waiting for a room (``arrival_waiting_room`` event / ``waiting_for_room`` request), or
  * a re-plan that finds an arrival whose room will not be clean by the booked check-in (15:00 local).
Early check-in requests for rooms that are already vacant and clean are plain guest service
(``guest_request``, answered once). Normal operation stays silent (no daily summary decisions).

Owned guest request types (T04 skips them): early_checkin, waiting_for_room, late_checkout, arrival_delay.
Causality: bookings/requests/events by their timestamps via ``ctx.asof``; roster via ``AsOf.roster()``
(status as known at t); durations learned only from cleanings completed by t.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import pandas as pd

from ..asof import prefix_of
from ..clock import at_local, iso, local, local_hm
from ..decision import make_decision
from ..messaging import compose_guest_message, parse_requested_time
from ..planning import (CHECKIN_LOCAL, CHECKOUT_LOCAL, arrivals_on, at_local_day, build_demand,
                        cleaning_in_progress, cleaning_records, day_bounds, learn_durations, local_day,
                        oncall_slot, plan_day, robot_records)
from .base import TriggerDetector

ISSUE = "room_turnaround"
DAILY_LOCAL = "06:30"                    # daily re-plan before the 07:00 housekeeping shift
MATERIAL = pd.Timedelta(minutes=30)      # ETA change that is worth a follow-up decision
REASSIGN_WAIT = pd.Timedelta(hours=1)    # propose another room if the guest would wait longer
# An early check-in is only a conflict if the planned clean end leaves less than this before the requested
# time: two 15-min planning steps absorb one overrunning room / late check-out without the guest waiting.
SLACK = pd.Timedelta(minutes=30)
CHECKOUT_LOOKBACK = pd.Timedelta(days=28)   # typical check-out time learned from recent check-outs
CHECKOUT_MIN_SAMPLES = 10
# If the departing guest is still in the room when 4 of 5 recent guests had already checked out, the
# "leaves at the typical time" assumption of a feasible early check-in failed -> open the incident then.
VACATE_CHECK_Q = 0.8
RELEASE_RETRIES = 4
OWNED_REQUESTS = ("early_checkin", "waiting_for_room", "late_checkout", "arrival_delay")
ROSTER_ROLES = ("cleaner", "robot")


@dataclass
class Episode:
    room: str
    day: object
    key: str
    deadline: pd.Timestamp | None = None
    early: bool = False
    language: str | None = None
    request_ids: list[str] = field(default_factory=list)
    eta: pd.Timestamp | None = None
    notified_eta: pd.Timestamp | None = None
    resource: str | None = None
    start: pd.Timestamp | None = None
    waiting: bool = False
    robot: bool = False
    oncall: bool = False
    reassigned: bool = False
    escalated: bool = False
    pending: bool = False   # plan provisional (request before the day's 06:30 re-plan): commit at re-plan
    released: bool = False
    last_notify: pd.Timestamp | None = None
    quiet: bool = False     # feasible early check-in with slack: guest service, not a turnaround incident
    guest_key: str | None = None
    force_review: str | None = None   # reason to open the incident at the next review (expectation failed)
    vacate_check: bool = False

    @property
    def log_key(self) -> str:
        return self.guest_key if (self.quiet and self.guest_key) else self.key


_FIRST_POS: dict = {}


def _first_pos(src) -> dict:
    """booking_id -> first row position in the bookings table (cached per table)."""
    hit = _FIRST_POS.get(id(src))
    if hit is None or hit[0] is not src or hit[1] != len(src):
        mp = {}
        for i, bid in enumerate(src.booking_id.tolist()):
            if isinstance(bid, str):
                mp.setdefault(bid, i)
        hit = _FIRST_POS[id(src)] = (src, len(src), mp)
    return hit[2]


class TurnaroundAgent(TriggerDetector):
    name = "turnaround"

    # ------------------------------------------------------------------ setup
    def build_triggers(self, data, state):
        self.data = data
        self.records = cleaning_records(data.events)
        self.robot_rec = robot_records(data.events)
        self.room_meta = {r["id"]: r for r in data.rooms}
        self.eps: dict[str, Episode] = {}
        self.deadlines: dict[tuple, pd.Timestamp] = {}      # (room, day) -> requested early check-in
        self.departures: dict[str, pd.Timestamp] = {}       # room -> agreed late check-out
        self.waiting: set[tuple] = set()                    # (room, day) with a guest waiting
        self.answered: set[str] = set()
        self.oncall_busy: pd.Timestamp | None = None
        self.shortfall_rooms: dict[str, set] = {}
        self.shortfall_days: set = set()    # local days whose re-plan (as known then) found a shortfall
        self.ruled_out: list[dict] = []
        self._dur = (None, None)
        co = data.events[data.events.type == "checkout"].timestamp_utc
        self._checkouts = co.sort_values().reset_index(drop=True)
        self._typical = (None, None)
        self._plan_cache: dict = {}
        self._version = 0

        # daily re-plan at 06:30 local of every local day in the window
        day = local_day(data.start)
        while True:
            ts = at_local_day(day, DAILY_LOCAL)
            if ts >= data.end:
                break
            if ts >= data.start:
                yield ts, "daily", day
            day = day + pd.Timedelta(days=1).to_pytimedelta()
        ev = data.events
        for row in ev[ev.type.isin(["checkout", "checkout_early"])].itertuples(index=False):
            yield row.timestamp_utc, "checkout", (row.room_id, row.type)
        for row in ev[ev.type == "arrival_waiting_room"].itertuples(index=False):
            yield row.timestamp_utc, "waiting", (row.room_id, row.booking_id, None)
        for row in ev[ev.type == "cleaning_completed"].itertuples(index=False):
            yield row.timestamp_utc, "cleaned", (row.room_id, 0)
        for row in ev[ev.type.isin(["checkin", "checkin_early", "checkin_after_wait"])].itertuples(index=False):
            yield row.timestamp_utc, "checkin", row.room_id
        gr = data.guest_requests
        for row in gr[gr.request_type.isin(OWNED_REQUESTS)].itertuples(index=False):
            yield row.timestamp_utc, "request", row
        r = data.roster
        ch = r[(r.status != "scheduled") & r.role.isin(ROSTER_ROLES) & r.status_known_utc.notna()
               & (r.status_known_utc >= data.start)]
        for row in ch.itertuples(index=False):
            yield row.status_known_utc, "roster", row

    # ---------------------------------------------------------------- helpers
    def _durations(self, t):
        day = local_day(t)
        if self._dur[0] != day:
            self._dur = (day, learn_durations(self.records, self.data.rooms, t, robot=self.robot_rec))
        return self._dur[1]

    def _typical_checkout(self, t, q=0.5):
        """Quantile q (default median) of local check-out times (minutes after midnight) of regular
        check-outs in the last CHECKOUT_LOOKBACK before t; None while there are too few."""
        day = local_day(t)
        if self._typical[0] != (day, q):
            co = self._checkouts
            lo, hi = co.searchsorted(t - CHECKOUT_LOOKBACK, side="right"), co.searchsorted(t, side="right")
            w = co.iloc[lo:hi]
            med = None
            if len(w) >= CHECKOUT_MIN_SAMPLES:
                lt = w.dt.tz_convert("Europe/Vienna")
                med = int((lt.dt.hour * 60 + lt.dt.minute).quantile(q))
            self._typical = ((day, q), med)
        return self._typical[1]

    def _expected_eta(self, ctx, plan, room, day, eta):
        """Planned clean end if the departing guest leaves at the typical (learned) check-out time instead of
        the booked 11:00 - used only to classify a request; guests are always told the planned time."""
        task = plan.tasks.get(room)
        med = self._typical_checkout(ctx.t)
        if eta is None or task is None or med is None or not ctx.state.occupied(room, ctx.t):
            return eta
        expected = max(ctx.t, at_local_day(day, f"{med // 60:02d}:{med % 60:02d}"))
        return eta - max(pd.Timedelta(0), task.ready_from - expected)

    def _plan(self, ctx, day):
        key = (ctx.t, day, self._version)
        if key in self._plan_cache:
            return self._plan_cache[key]
        t = ctx.t
        asof = ctx.asof
        dur = self._durations(t)
        lo, hi = day_bounds(day)
        deadlines = {room: dl for (room, d), dl in self.deadlines.items() if d == day}
        departures = {room: dep for room, dep in self.departures.items() if lo <= dep < hi}
        waiting = [room for (room, d) in self.waiting if d == day]
        inprog = cleaning_in_progress(asof.events(since=t - pd.Timedelta(hours=12)))
        demand = build_demand(t, ctx.state, asof, day, dur, deadlines=deadlines, departures=departures,
                              waiting=waiting, in_progress=inprog)
        plan = plan_day(t, ctx.state, asof, demand, day=day, robot_minutes=dur.robot_minutes)
        plan.tasks = {task.room: task for task in demand}
        self._plan_cache = {key: plan}
        return plan

    def _deadline_risk(self, ctx, plan, day, exclude=None) -> bool:
        """Another arriving guest's room is unplanned, or planned after its deadline even if its departing
        guest leaves at the typical check-out time (rooms without an arrival that day cannot make a guest wait)."""
        for r in plan.unplanned:
            task = plan.tasks.get(r)
            if r != exclude and task is not None and task.deadline is not None:
                return True
        for r in plan.late:
            task = plan.tasks.get(r)
            if r == exclude or task is None or task.deadline is None:
                continue
            exp = self._expected_eta(ctx, plan, r, day, plan.ready_at.get(r))
            if exp is None or exp > task.deadline:
                return True
        return False

    def _key(self, room, day):
        return f"turnaround:{room}:{day}"

    def _episode(self, ctx, room, day) -> Episode:
        key = self._key(room, day)
        ep = self.eps.get(key)
        if ep is None or not ctx.log.is_open(ep.log_key):
            ep = Episode(room=room, day=day, key=key)
            self.eps[key] = ep
        return ep

    def _add(self, ctx, ep: Episode | None, action, priority, reason, *, room=None, assets=None, issue=ISSUE,
             episode_key=None, **kw):
        room = room or ep.room
        if ep is not None and ep.quiet and episode_key is None:
            issue, episode_key = "guest_request", ep.guest_key
        d = make_decision(t=ctx.t, affected_assets=assets or [room], issue_type=issue, action=action,
                          priority=priority, reason=reason, agent=self.name, **kw)
        return ctx.log.add(d, episode_key=episode_key or ep.key, prefix="TA")

    def _close(self, ctx, ep: Episode):
        ctx.log.close(ep.key)
        if ep.guest_key:
            ctx.log.close(ep.guest_key)
        self.eps.pop(ep.key, None)

    def _booking(self, asof, booking_id):
        b = asof.bookings()
        pre = prefix_of(b) if isinstance(booking_id, str) else None   # non-str ids: plain pandas match
        if pre is not None:
            # b == src.iloc[:n]: first row of booking_id in b == its first row in src if that is within n
            src, n = pre
            pos = _first_pos(src).get(booking_id)
            return None if pos is None or pos >= n else b.iloc[pos]
        m = b[b.booking_id == booking_id]
        return None if m.empty else m.iloc[0]

    def _arrival_for(self, ctx, room, booking_id):
        """Booked arrival (UTC) of booking_id, else the room's next arrival as known, else next 15:00 local."""
        asof = ctx.asof
        bk = self._booking(asof, booking_id) if isinstance(booking_id, str) else None
        if bk is not None and bk.arrival_utc >= day_bounds(local_day(ctx.t))[0]:
            return bk.arrival_utc, bk.booking_id
        nxt = at_local(ctx.t, CHECKIN_LOCAL)
        if nxt <= ctx.t:
            nxt = at_local(ctx.t + pd.Timedelta(days=1), CHECKIN_LOCAL)
        return nxt, (bk.booking_id if bk is not None else None)

    def _occupant_evidence(self, ctx, room):
        stay = ctx.state.stay_at(room, ctx.t)
        if stay is None:
            hk = ctx.state.housekeeping(room, ctx.t)
            return [f"room_telemetry.csv:{room}:{iso(ctx.t)}:housekeeping_state={hk}, vacant"]
        return [f"events.jsonl:{room}:{iso(stay.start)}:stay {stay.booking_id} in progress (check-out by "
                f"{CHECKOUT_LOCAL} local on departure day)"]

    def _slot_text(self, plan, room):
        sl, rb = plan.slot_for(room), plan.slot_for(room, "robot_floor")
        if sl is None:
            return "no cleaner slot left in the known shifts (shortfall)"
        txt = f"{sl.resource} {local_hm(sl.start)}-{local_hm(sl.end)} local"
        if rb is not None:
            txt = f"{rb.resource} floor pre-clean {local_hm(rb.start)}-{local_hm(rb.end)}, then " + txt
        return txt

    def _schedule(self, ctx, ep, plan, why, priority, evidence, deps=None):
        """schedule_cleaning for ep.room per plan (only resources available as known at t)."""
        sl = plan.slot_for(ep.room)
        if sl is None or not ctx.state.available(sl.resource, sl.start, known_at=ctx.t)[0]:
            return None
        ep.resource, ep.start = sl.resource, sl.start
        return self._add(
            ctx, ep, "schedule_cleaning", priority,
            f"{why}; plan: {self._slot_text(plan, ep.room)} -> room clean ~{local_hm(sl.end)} local. "
            f"Day plan: {plan.summary()}",
            resource_id=sl.resource, scheduled_for_utc=sl.start, evidence=evidence,
            expected_outcome=f"{ep.room} clean by ~{local_hm(sl.end)} local (planned, not yet executed)",
            verification_plan=(f"cleaning_started/cleaning_completed for {ep.room} and housekeeping_state=clean; "
                               "re-plan if the slot slips >= 30 min or staff becomes unavailable"),
            dependencies=deps or [], confidence=0.75)

    def _robot(self, ctx, ep, plan, evidence):
        rb = plan.slot_for(ep.room, "robot_floor")
        if (rb is None or ep.robot or ctx.state.occupied(ep.room, ctx.t)
                or not ctx.state.available(rb.resource, rb.start, known_at=ctx.t)[0]):
            return None
        ep.robot = True
        return self._add(
            ctx, ep, "dispatch_robot", "medium",
            f"{ep.room} is vacant: robot floor pre-clean {local_hm(rb.start)}-{local_hm(rb.end)} local shortens "
            "the human turnover (robot does not clean the bathroom, cannot release the room)",
            resource_id=rb.resource, scheduled_for_utc=rb.start, evidence=evidence,
            expected_outcome="Floor done before the cleaner arrives; human cleaning time reduced (learned durations)",
            verification_plan=f"robot_floor_cleaning_completed for {ep.room}", confidence=0.7)

    def _notify(self, ctx, ep, kind, priority, reason, evidence, request_ids=None, deps=None, **facts):
        msg = compose_guest_message(kind, ep.language, room_id=ep.room, **facts)
        ep.last_notify = ctx.t
        return self._add(
            ctx, ep, "notify_guest", priority, reason, message=msg, evidence=evidence,
            guest_request_ids=request_ids or [], dependencies=deps or [],
            expected_outcome="Guest has a realistic time (proposed message - not proof of delivery)",
            verification_plan="Guest checks in at/after the stated time without an arrival_waiting_room event",
            confidence=0.7)

    def _eta_message(self, ep, eta, arrival):
        """(kind, facts, notified_eta). The stated time is never earlier than the planned cleaning end."""
        if ep.waiting:
            if eta is None:
                return "waiting_update", {"eta_local": "to be confirmed (no free cleaner yet - escalated)"}, None
            return "waiting_update", {"eta_local": local_hm(eta)}, eta
        if eta is not None and eta < arrival:
            shown = max(eta, ep.deadline) if ep.deadline is not None else eta
            return "early_checkin_possible", {"eta_local": local_hm(shown)}, shown
        earliest = max(eta, arrival) if eta is not None else arrival
        return "early_checkin_not_possible", {"earliest_local": local_hm(earliest)}, earliest

    # ------------------------------------------------------------------ handle
    def handle(self, ctx, ts, kind, payload):
        if kind == "daily":
            self._daily(ctx, payload)
        elif kind == "request":
            rt = payload.request_type
            if rt == "early_checkin":
                self._early_checkin(ctx, payload)
            elif rt == "waiting_for_room":
                self._waiting(ctx, payload.room_id, payload.booking_id, payload)
            elif rt == "late_checkout":
                self._late_checkout(ctx, payload)
            elif rt == "arrival_delay":
                self._arrival_delay(ctx, payload)
        elif kind == "checkout":
            room, typ = payload
            day = local_day(ctx.t)
            if self._key(room, day) in self.eps or typ == "checkout_early":
                ev = [f"events.jsonl:{room}:{iso(ts)}:{typ}"]
                self._review(ctx, day, f"{typ} of {room} at {local_hm(ts)} local", ev)
        elif kind == "waiting":
            room, booking, _ = payload
            self._waiting(ctx, room, booking, None)
        elif kind == "cleaned":
            self._release(ctx, *payload)
        elif kind == "checkin":
            for key in [k for k, ep in self.eps.items() if ep.room == payload]:
                self._close(ctx, self.eps[key])
        elif kind == "roster":
            self._roster(ctx, payload)
        elif kind == "vacate_check":
            self._vacate_check(ctx, *payload)

    # --- triggers -------------------------------------------------------------
    def _daily(self, ctx, day):
        today = local_day(ctx.t)
        for key, ep in list(self.eps.items()):
            if ep.day < today:
                self._close(ctx, ep)
        self.deadlines = {k: v for k, v in self.deadlines.items() if k[1] >= today}
        self.waiting = {k for k in self.waiting if k[1] >= today}
        self._review(ctx, day, f"daily re-plan {DAILY_LOCAL} local", [])

    def _vacate_check(self, ctx, room, day):
        """The feasible early check-in assumed the departing guest leaves at the typical time: if they are
        still in the room SLACK later, the assumption failed -> open the turnaround incident now."""
        ep = self.eps.get(self._key(room, day))
        if ep is None or not ep.quiet or ep.released or not ctx.state.occupied(room, ctx.t):
            return
        stay = ctx.state.stay_at(room, ctx.t)
        ep.force_review = f"departing guest still in {room} at {local_hm(ctx.t)} local (later than the typical check-out)"
        self._review(ctx, day, ep.force_review,
                     [f"events.jsonl:{room}:{iso(stay.start)}:stay {stay.booking_id} still in progress, no checkout yet"])

    def _roster(self, ctx, row):
        day = local_day(row.start_utc)
        today = local_day(ctx.t)
        if not (today <= day <= today + pd.Timedelta(days=1).to_pytimedelta()) or row.end_utc <= ctx.t:
            return
        ev = [f"staff_roster.csv:{row.resource_id}:{iso(row.status_known_utc)}:{row.shift_id} {row.status} "
              f"(shift {local_hm(row.start_utc)}-{local_hm(row.end_utc)} local)"]
        self._review(ctx, day, f"{row.resource_id} {row.status} became known - capacity re-planned", ev)

    def _early_checkin(self, ctx, req):
        if req.request_id in self.answered:
            return
        self.answered.add(req.request_id)
        t = ctx.t
        room = req.room_id
        arrival, booking_id = self._arrival_for(ctx, room, req.booking_id)
        day = local_day(arrival)
        hhmm = parse_requested_time(req.text)
        req_ts = at_local_day(day, hhmm) if hhmm else None
        early = req_ts is not None and req_ts < arrival
        deadline = req_ts if early else arrival
        if early:
            self.deadlines[(room, day)] = deadline
            self._version += 1
        plan = self._plan(ctx, day)
        evidence = [f"guest_requests.csv:{room}:{iso(req.timestamp_utc)}:{req.request_id} early check-in "
                    f"{hhmm or 'time not stated'} local",
                    f"bookings.csv:{room}:{booking_id or req.booking_id} arrival {day} {CHECKIN_LOCAL} local"]
        needs_turnover = room in plan.ready_at or room in plan.unplanned
        if not needs_turnover:
            stay = ctx.state.stay_at(room, t)
            ok = stay is None and ctx.state.housekeeping(room, t) == "clean"
            shown = max(deadline, t) if ok else arrival
            kind = "early_checkin_possible" if (ok and early) else "room_ready_eta"
            facts = {"eta_local": local_hm(shown)}
            msg = compose_guest_message(kind, req.language, room_id=room, **facts)
            self._add(ctx, None, "notify_guest", "low",
                      f"Early check-in request for {room}: room already vacant and clean, no turnover needed "
                      f"-> confirm {local_hm(shown)} local (release when the guest arrives)" if ok else
                      f"Check-in request for {room}: no turnover planned; booked check-in {local_hm(arrival)} local",
                      room=room, issue="guest_request", episode_key=f"guest:{req.request_id}",
                      message=msg, guest_request_ids=[req.request_id],
                      evidence=evidence + self._occupant_evidence(ctx, room),
                      expected_outcome="Guest informed (proposed message - not proof of delivery)",
                      verification_plan="Guest checks in without waiting", confidence=0.8)
            ctx.log.close(f"guest:{req.request_id}")
            return
        eta = plan.ready_at.get(room)
        eta_exp = self._expected_eta(ctx, plan, room, day, eta)
        feasible = (eta_exp is not None and eta_exp <= deadline - SLACK and not self._deadline_risk(ctx, plan, day, room)
                    and day not in self.shortfall_days and (room, day) not in self.waiting)
        existing = self.eps.get(self._key(room, day))
        fresh = existing is None or not ctx.log.is_open(existing.log_key)
        ep = self._episode(ctx, room, day)
        if fresh:
            ep.quiet, ep.guest_key = feasible, (f"guest:{req.request_id}" if feasible else None)
        elif ep.quiet and not feasible:
            ep.quiet = False
        ep.deadline, ep.early, ep.language = deadline, early, req.language
        ep.request_ids.append(req.request_id)
        ep.eta = eta
        if ep.quiet and not ep.vacate_check and ctx.state.occupied(room, t):
            med = self._typical_checkout(t, VACATE_CHECK_Q)
            if med is not None:
                ep.vacate_check = True
                when = max(t, at_local_day(day, f"{med // 60:02d}:{med % 60:02d}"))
                heapq.heappush(self._q, (when, next(self._seq), "vacate_check", (room, day)))
        evidence += self._occupant_evidence(ctx, room)
        late = eta is None or eta > deadline
        if ep.quiet:
            why = (f"Early check-in {room} {local_hm(deadline)} local on {day} is feasible: planned clean "
                   f"~{local_hm(eta_exp)} local (departing guest leaves at the typical check-out time; planned "
                   f"{local_hm(eta)} if they stay until {CHECKOUT_LOCAL}) leaves >= {int(SLACK.total_seconds() // 60)} min "
                   "slack and the day plan has no shortfall (guest service; becomes a turnaround conflict if the plan slips)")
        else:
            why = (f"Turnaround conflict {room}: early check-in requested for {local_hm(deadline)} local on {day} "
                   f"but the room still needs a turnover"
                   + ("" if eta is None else f"; planned clean ~{local_hm(eta)} local")
                   + (" - cannot meet the requested time" if late else
                      f" - less than {int(SLACK.total_seconds() // 60)} min slack or a shortfall in the day plan; prioritised"))
        sched = rob = None
        if t >= at_local_day(day, DAILY_LOCAL):
            sched = self._schedule(ctx, ep, plan, why, "high" if late else "medium", evidence)
            rob = self._robot(ctx, ep, plan, evidence) if late else None
        else:
            # night-before request: cleaners are committed at the day's re-plan (sick calls arrive overnight)
            ep.pending = eta is not None
        kind, facts, shown = self._eta_message(ep, eta, arrival)
        ep.notified_eta = shown
        deps = [d.event_id for d in (sched, rob) if d is not None]
        reason = (f"Realistic answer to {req.request_id}: {kind.replace('_', ' ')}"
                  + (f" ({', '.join(f'{k}={v}' for k, v in facts.items())})")
                  + ("" if sched or ep.pending else " - no cleaner capacity left in the known shifts; not promising an early room")
                  + (f"; provisional plan {self._slot_text(plan, room)}, cleaner committed at the {DAILY_LOCAL} re-plan"
                     if ep.pending else ""))
        self._notify(ctx, ep, kind, "medium" if late else "low", reason, evidence, request_ids=[req.request_id],
                     deps=deps, **facts)

    def _review(self, ctx, day, cause, evidence):
        """Re-plan a day; follow-ups only where the plan changed materially, new episodes for arrivals
        whose room would not be clean by the booked check-in."""
        plan = self._plan(ctx, day)
        t = ctx.t
        lo, hi = day_bounds(day)
        if local_day(t) == day and self._deadline_risk(ctx, plan, day):
            self.shortfall_days.add(day)
        for ep in [e for e in list(self.eps.values()) if e.day == day and not e.released]:
            if not ctx.log.is_open(ep.log_key):
                self.eps.pop(ep.key, None)
                continue
            if ep.oncall or (ep.room not in plan.ready_at and ep.room not in plan.unplanned):
                continue  # on-call already handling it, or room already clean / no turnover needed
            new_eta = plan.ready_at.get(ep.room)
            lost = (ep.resource is not None and ep.start is not None and ep.start > t
                    and not ctx.state.available(ep.resource, ep.start, known_at=t)[0])
            was_ok = ep.eta is not None and (ep.deadline is None or ep.eta <= ep.deadline)
            late = new_eta is None or (ep.deadline is not None and new_eta > ep.deadline)
            worse = (new_eta is None and ep.eta is not None) or (
                new_eta is not None and ep.eta is not None and new_eta - ep.eta >= MATERIAL)
            opened = False
            if ep.quiet:
                exp = self._expected_eta(ctx, plan, ep.room, day, new_eta)
                tight = exp is None or (ep.deadline is not None and exp > ep.deadline - SLACK)
                if lost or tight or ep.force_review or self._deadline_risk(ctx, plan, day, ep.room):
                    ep.quiet, opened = False, True   # feasible request became a conflict: open the incident now
                elif not ep.pending:
                    continue
            # follow-up only when the situation changed: staff lost, ETA slipped materially, or now late
            if not (lost or worse or (was_ok and late) or ep.pending or opened):
                continue
            was_pending, ep.pending = ep.pending, False
            ep.force_review = None
            ev = evidence + self._occupant_evidence(ctx, ep.room)
            sched = self._schedule(ctx, ep, plan, f"{'Commit plan' if was_pending else 'Re-plan'} {ep.room} ({cause})" +
                                   (f"; {ep.resource} no longer available" if lost else "") +
                                   ("; early check-in no longer has slack -> turnaround conflict" if opened else ""),
                                   "high" if late else "medium", ev)
            if late and not ctx.state.occupied(ep.room, t):
                self._robot(ctx, ep, plan, ev)
            ep.eta = new_eta
            arrival = at_local_day(day, CHECKIN_LOCAL)
            kind, facts, shown = self._eta_message(ep, new_eta, arrival)
            if (ep.language is not None or ep.waiting) and (
                    shown is None or ep.notified_eta is None or shown > ep.notified_eta):
                ep.notified_eta = shown
                self._notify(ctx, ep, kind, "high" if late else "medium",
                             f"Updated ETA for {ep.room} after re-plan ({cause})", ev,
                             deps=[sched.event_id] if sched else None, **facts)
        # arrivals whose room will not be clean by the booked check-in: one shortfall episode per day
        if local_day(t) != day:
            return
        risk = []
        for room in list(plan.unplanned) + list(plan.late):
            task = plan.tasks.get(room)
            if task is None or task.deadline is None or task.deadline < t or self._key(room, day) in self.eps:
                continue
            risk.append((room, task.deadline, plan.ready_at.get(room)))
        key = f"turnaround:shortfall:{day}"
        new = [r for r in risk if r[0] not in self.shortfall_rooms.setdefault(key, set())]
        if not new:
            return
        self.shortfall_rooms[key].update(r[0] for r in new)
        rooms = sorted({r[0] for r in risk})
        detail = "; ".join(f"{r} arrival {local_hm(dl)} -> " + (f"clean ~{local_hm(eta)}" if eta is not None else "no slot")
                           for r, dl, eta in sorted(risk))
        ev = evidence or [f"staff_roster.csv:cleaners:{iso(t)}:scheduled shifts as known"]
        ev = ev + [f"bookings.csv:{r}:arrival {day} {local_hm(dl)} local" for r, dl, _ in sorted(risk)][:6]
        ctx.log.add(make_decision(
            t=t, affected_assets=rooms, issue_type=ISSUE, action="request_human_approval", priority="high",
            reason=(f"Turnaround shortfall {day} ({cause}): {len(rooms)} arrival room(s) will not be clean by "
                    f"check-in with the known staff - {detail}. Day plan: {plan.summary()}. "
                    "Request extra housekeeping capacity (overtime / extra shift) - capacity is never invented"),
            notable_cost=True, evidence=ev,
            expected_outcome="Supervisor adds capacity or accepts later check-in; guests informed with realistic ETA",
            verification_plan="cleaning_completed for these rooms before arrival; no arrival_waiting_room events",
            confidence=0.6, agent=self.name, simulated_effect=f"shortfall {plan.shortfall_minutes:.0f} cleaning min (planner estimate)"),
            episode_key=key, prefix="TA")

    def _waiting(self, ctx, room, booking_id, req):
        t = ctx.t
        day = local_day(t)
        rid = [req.request_id] if req is not None else []
        if req is not None:
            self.answered.add(req.request_id)
        first = (room, day) not in self.waiting
        self.waiting.add((room, day))
        self._version += 1
        ep = self._episode(ctx, room, day)
        ep.waiting = True
        ep.quiet = False
        if req is not None:
            ep.language = req.language
            ep.request_ids.append(req.request_id)
        ev = ([f"events.jsonl:{room}:{iso(t)}:arrival_waiting_room {booking_id}"] if req is None else
              [f"guest_requests.csv:{room}:{iso(req.timestamp_utc)}:{req.request_id} {req.request_type}"])
        ev += self._occupant_evidence(ctx, room)
        ok, why_not = ctx.state.can_release(room, t)
        if ok:
            self._release(ctx, room, 0)
            return
        if not first and req is not None and ep.last_notify is not None and t - ep.last_notify < pd.Timedelta(hours=1):
            # answer the request (owned by T05) but without re-planning noise
            self._notify(ctx, ep, "waiting_update", "high", f"Answer to {req.request_id}: room still being prepared",
                         ev, request_ids=rid, eta_local=local_hm(ep.notified_eta) if ep.notified_eta else "unknown")
            return
        plan = self._plan(ctx, day)
        eta = ep.eta if (ep.oncall and ep.eta is not None) else plan.ready_at.get(room)
        dur = self._durations(t)
        deps = []
        # on-call housekeeper: waiting guest, on-call window, and faster than the regular plan
        if not ep.oncall and (eta is None or eta - t > REASSIGN_WAIT) and not ctx.state.occupied(room, t):
            sl = oncall_slot(t, dur.minutes(room, False), busy_until=self.oncall_busy)
            if sl is not None and (eta is None or sl.end < eta) and ctx.state.available(sl.resource, sl.start, known_at=t)[0]:
                ep.oncall = True
                self.oncall_busy = sl.end
                eta = sl.end
                d = self._add(ctx, ep, "call_oncall_housekeeper", "high",
                              f"Guest waiting for {room} and no regular cleaner can finish it in time "
                              f"({'no slot left' if plan.ready_at.get(room) is None else 'plan ETA ' + local_hm(plan.ready_at[room])}); "
                              f"ONCALL_1 on site ~{local_hm(sl.start)}, room clean ~{local_hm(sl.end)} local (one room at a time, costly)",
                              resource_id=sl.resource, scheduled_for_utc=sl.start, evidence=ev, notable_cost=True,
                              expected_outcome=f"{room} clean ~{local_hm(sl.end)} local after approval",
                              verification_plan=f"oncall_housekeeping_called + cleaning_completed for {room}",
                              confidence=0.7)
                deps.append(d.event_id)
        if not deps and (eta is None or eta - t > REASSIGN_WAIT) and not ep.reassigned:
            alt = self._alternative_room(ctx, room, day)
            if alt is not None:
                ep.reassigned = True
                d = self._add(ctx, ep, "reassign_room", "high",
                              f"Guest waiting for {room} would wait > 1 h (ETA "
                              f"{local_hm(eta) if eta is not None else 'unknown'} local); {alt} is the same room type, "
                              "vacant, clean and has no arrival today -> propose moving the booking",
                              assets=[room, alt], evidence=ev + [f"room_telemetry.csv:{alt}:{iso(t)}:housekeeping_state=clean, vacant"],
                              expected_outcome=f"Guest gets {alt} now after approval; {room} cleaned in the normal plan",
                              verification_plan=f"check-in into {alt}; release_room {alt} only if still clean & vacant",
                              confidence=0.6)
                deps.append(d.event_id)
        if not deps and not ep.oncall:
            sl = plan.slot_for(room)
            if sl is not None and sl.resource != ep.resource:
                d = self._schedule(ctx, ep, plan, f"Guest waiting for {room}: moved to the front of the queue", "high", ev)
                if d is not None:
                    deps.append(d.event_id)
            elif sl is None and not ep.escalated:
                ep.escalated = True
                d = self._add(ctx, ep, "request_human_approval", "high",
                              f"Guest waiting for {room} and no cleaner slot left in the known shifts; on-call only "
                              "15:30-22:00 local -> supervisor to approve overtime / extra cleaner",
                              evidence=ev, notable_cost=True,
                              expected_outcome="Extra capacity approved; ETA confirmed to the guest",
                              verification_plan=f"cleaning_completed for {room}", confidence=0.6)
                deps.append(d.event_id)
        ep.eta = eta
        kind, facts, shown = self._eta_message(ep, eta, t)
        ep.notified_eta = shown
        self._notify(ctx, ep, kind, "high",
                     f"Guest waiting for {room} ({why_not}); realistic ETA "
                     f"{facts.get('eta_local')} local", ev, request_ids=rid, deps=deps, **facts)

    def _alternative_room(self, ctx, room, day):
        meta = self.room_meta.get(room, {})
        typ = (meta.get("area_m2"), meta.get("has_bathtub"))
        booked = set(arrivals_on(ctx.asof.bookings(), day).room_id)
        claimed = {ep.room for ep in self.eps.values()}
        for r, m in self.room_meta.items():
            if r == room or (m.get("area_m2"), m.get("has_bathtub")) != typ or r in booked or r in claimed:
                continue
            if ctx.state.can_release(r, ctx.t)[0]:
                return r
        return None

    def _release(self, ctx, room, attempt):
        t = ctx.t
        day = local_day(t)
        ep = self.eps.get(self._key(room, day))
        if ep is None or ep.released or not (ctx.log.is_open(ep.log_key) or ep.waiting):
            return
        ok, why = ctx.state.can_release(room, t)
        if not ok:
            if attempt < RELEASE_RETRIES:
                heapq.heappush(self._q, (t + ctx.step, next(self._seq), "cleaned", (room, attempt + 1)))
            return
        ep.released = True
        self._add(ctx, ep, "release_room", "high" if ep.waiting else "medium",
                  f"{room} {why} at {local_hm(t)} local -> released for the "
                  + ("waiting guest" if ep.waiting else f"early arrival ({local_hm(ep.deadline) if ep.deadline else ''} local)"),
                  evidence=[f"room_telemetry.csv:{room}:{iso(t)}:housekeeping_state=clean",
                            f"events.jsonl:{room}:{iso(t)}:cleaning_completed / no stay in progress"],
                  expected_outcome="Guest can self-check-in now",
                  verification_plan=f"checkin event for {room}; no further arrival_waiting_room", confidence=0.9)
        self._close(ctx, ep)

    def _late_checkout(self, ctx, req):
        if req.request_id in self.answered:
            return
        self.answered.add(req.request_id)
        t = ctx.t
        room = req.room_id
        bk = self._booking(ctx.asof, req.booking_id)
        dep = bk.departure_utc if bk is not None else at_local(t, CHECKOUT_LOCAL)
        day = local_day(dep)
        hhmm = parse_requested_time(req.text)
        until = at_local_day(day, hhmm) if hhmm else None
        arr = arrivals_on(ctx.asof.bookings(), day)
        same_day = room in set(arr.room_id)
        ok = until is not None and until > dep and not same_day
        if ok:
            self.departures[room] = until
            self._version += 1
            kind, facts = "late_checkout_ok", {"until_local": local_hm(until)}
            reason = f"Late check-out {room} until {local_hm(until)} local: no arrival in this room on {day}"
        else:
            kind, facts = "late_checkout_not_possible", {"until_local": CHECKOUT_LOCAL}
            reason = (f"Late check-out {room} declined: next guest arrives on {day}, turnover needed"
                      if same_day else f"Late check-out {room}: requested time not understood/not later; standard {CHECKOUT_LOCAL}")
        msg = compose_guest_message(kind, req.language, room_id=room, **facts)
        self._add(ctx, None, "notify_guest", "low", reason, room=room, issue="guest_request",
                  episode_key=f"guest:{req.request_id}", message=msg, guest_request_ids=[req.request_id],
                  evidence=[f"guest_requests.csv:{room}:{iso(req.timestamp_utc)}:{req.request_id} late check-out "
                            f"{hhmm or '?'} local", f"bookings.csv:{room}:arrivals on {day}: {len(arr)}"],
                  expected_outcome="Guest informed; cleaning plan uses the agreed departure",
                  verification_plan="checkout event by the agreed time", confidence=0.8)
        ctx.log.close(f"guest:{req.request_id}")

    def _arrival_delay(self, ctx, req):
        if req.request_id in self.answered:
            return
        self.answered.add(req.request_id)
        msg = compose_guest_message("service_ack", req.language, room_id=req.room_id, request_type="arrival_delay")
        self._add(ctx, None, "notify_guest", "low",
                  f"Late arrival for {req.room_id}: self check-in works 24/7, room stays reserved; no re-plan needed",
                  room=req.room_id, issue="guest_request", episode_key=f"guest:{req.request_id}", message=msg,
                  guest_request_ids=[req.request_id],
                  evidence=[f"guest_requests.csv:{req.room_id}:{iso(req.timestamp_utc)}:{req.request_id} arrival_delay"],
                  expected_outcome="Guest reassured (proposed message - not proof of delivery)",
                  verification_plan="checkin event later that night", confidence=0.8)
        ctx.log.close(f"guest:{req.request_id}")

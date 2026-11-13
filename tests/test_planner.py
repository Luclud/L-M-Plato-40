"""Unit tests for the shared housekeeping planner (plato40.planning.housekeeping)."""
import datetime as dt

import pandas as pd

from plato40.asof import AsOf
from plato40.planning import (CleaningTask, at_local_day, build_demand, cleaning_in_progress, cleaning_records,
                              day_bounds, learn_durations, oncall_slot, plan_day, resource_windows, schedule)

T = pd.Timestamp
DAY = T("2026-03-02T00:00:00Z")


def w(a, b):
    return (DAY + pd.Timedelta(hours=a), DAY + pd.Timedelta(hours=b))


def task(room, ready_h, deadline_h=None, minutes=60, robot=45, **kw):
    return CleaningTask(room=room, ready_from=DAY + pd.Timedelta(hours=ready_h),
                        deadline=None if deadline_h is None else DAY + pd.Timedelta(hours=deadline_h),
                        minutes=minutes, minutes_with_robot=robot, **kw)


# ---- pure scheduler -----------------------------------------------------------
def test_deadline_first_and_ready_time_respected():
    plan = schedule(DAY + pd.Timedelta(hours=6), [task("R1", 6, 14), task("R2", 9, 10)], {"C1": [w(6, 14)]})
    r2 = plan.slot_for("R2")
    assert r2.start == DAY + pd.Timedelta(hours=9)          # not before the room is vacant
    assert plan.ready_at["R2"] == DAY + pd.Timedelta(hours=10)
    assert not plan.late and not plan.unplanned


def test_waiting_guest_goes_first():
    plan = schedule(DAY + pd.Timedelta(hours=6), [task("R1", 6, 8), task("R2", 6, 14, waiting=True)],
                    {"C1": [w(6, 14)]})
    assert plan.slot_for("R2").start < plan.slot_for("R1").start


def test_shortfall_reported_not_invented():
    tasks = [task(f"R{i}", 6, 13) for i in range(5)]
    plan = schedule(DAY + pd.Timedelta(hours=6), tasks, {"C1": [w(6, 9)]})   # 3 h for 5 h of work
    assert len(plan.ready_at) == 3 and len(plan.unplanned) == 2
    assert plan.shortfall_minutes == 120 and plan.has_shortfall
    for s in plan.slots:   # every slot inside the known window
        assert w(6, 9)[0] <= s.start and s.end <= w(6, 9)[1]


def test_booked_intervals_and_in_progress_are_respected():
    booked = {"C1": [w(6, 7)]}
    t = task("R9", 5, None, assigned_to="C1", started=DAY + pd.Timedelta(hours=7))
    plan = schedule(DAY + pd.Timedelta(hours=6), [task("R1", 6, 8), t], {"C1": [w(6, 14)]}, booked=booked)
    assert plan.slot_for("R9").start == DAY + pd.Timedelta(hours=7)
    assert plan.slot_for("R1").start == DAY + pd.Timedelta(hours=8)   # after booked + in-progress
    assert booked == {"C1": [w(6, 7)]}                                 # input not mutated


def test_robot_pre_clean_only_when_vacant_and_faster():
    plan = schedule(DAY + pd.Timedelta(hours=6), [task("R1", 8, 10, minutes=60, robot=15)],
                    {"C1": [w(6, 14)]}, {"ROBOT_1": [w(5, 20)]}, robot_minutes=30)
    rb, cl = plan.slot_for("R1", "robot_floor"), plan.slot_for("R1")
    assert rb is not None and rb.start >= DAY + pd.Timedelta(hours=8) and cl.start >= rb.end
    assert plan.ready_at["R1"] == DAY + pd.Timedelta(hours=8, minutes=45)
    # robot not worth it -> human only
    plan2 = schedule(DAY + pd.Timedelta(hours=6), [task("R1", 8, 10, minutes=45, robot=45)],
                     {"C1": [w(6, 14)]}, {"ROBOT_1": [w(5, 20)]})
    assert plan2.slot_for("R1", "robot_floor") is None


def test_resource_windows_only_scheduled_shifts():
    roster = pd.DataFrame({
        "resource_id": ["C1", "C2", "C3"], "status": ["scheduled", "sick", "scheduled"],
        "start_utc": [w(6, 14)[0], w(6, 14)[0], w(30, 38)[0]], "end_utc": [w(6, 14)[1], w(6, 14)[1], w(30, 38)[1]]})
    win = resource_windows(roster, ["C1", "C2", "C3"], *w(8, 24))
    assert win == {"C1": [w(8, 14)], "C2": [], "C3": []}


def test_oncall_slot_window_and_travel():
    t = at_local_day(dt.date(2026, 1, 24), "16:00")
    s = oncall_slot(t, 45)
    assert s.start == t + pd.Timedelta(minutes=45) and s.resource == "ONCALL_1"
    assert oncall_slot(at_local_day(dt.date(2026, 1, 24), "12:00"), 45) is None
    assert oncall_slot(at_local_day(dt.date(2026, 1, 24), "21:30"), 45) is None   # would end after 22:00
    assert oncall_slot(t, 45, busy_until=t + pd.Timedelta(hours=2)).start == t + pd.Timedelta(hours=2)


def test_day_bounds_dst():
    lo, hi = day_bounds(dt.date(2026, 3, 29))   # spring forward: 23 h day
    assert (hi - lo) == pd.Timedelta(hours=23)


# ---- dataset helpers (quickstart) ----------------------------------------------
def test_learn_durations_is_causal(qs):
    rec = cleaning_records(qs.events)
    early = learn_durations(rec, qs.rooms, qs.start)          # nothing completed yet -> priors
    assert early.samples == 0 and early.minutes("R101") == 60 and early.minutes("R101", True) == 45
    t = T("2026-01-10T12:00:00Z")
    later = learn_durations(rec, qs.rooms, t)
    assert later.samples == int((rec.end <= t).sum()) > 0
    assert 30 <= later.minutes("R308") <= 75


def _plan(qs, qs_state, t, day):
    asof = AsOf(qs, t)
    dur = learn_durations(cleaning_records(qs.events), qs.rooms, t)
    inprog = cleaning_in_progress(asof.events(since=t - pd.Timedelta(hours=12)))
    demand = build_demand(t, qs_state, asof, day, dur, deadlines={"R308": at_local_day(day, "11:30")},
                          in_progress=inprog)
    return plan_day(t, qs_state, asof, demand, day=day)


def test_sick_cleaner_removes_capacity_only_once_known(qs, qs_state):
    """CLEANER_1 sick on 8 Jan, reported 05:15Z: planned at 05:00 it still counts, from 05:15 it does not."""
    day = dt.date(2026, 1, 8)
    before = _plan(qs, qs_state, T("2026-01-08T05:00:00Z"), day)
    after = _plan(qs, qs_state, T("2026-01-08T05:15:00Z"), day)
    assert "CLEANER_1" in before.resources
    assert "CLEANER_1" not in after.resources
    assert all(s.resource != "CLEANER_1" for s in after.slots)
    assert after.capacity_minutes < before.capacity_minutes
    assert after.has_shortfall and after.shortfall_minutes > 0   # reported, not invented
    assert not before.has_shortfall


def test_demand_uses_stays_and_deadlines(qs, qs_state):
    t, day = T("2026-01-07T23:00:00Z"), dt.date(2026, 1, 8)
    asof = AsOf(qs, t)
    dur = learn_durations(cleaning_records(qs.events), qs.rooms, t)
    dl = at_local_day(day, "11:30")
    demand = {x.room: x for x in build_demand(t, qs_state, asof, day, dur, deadlines={"R308": dl})}
    assert "R308" in demand                                    # occupied, departs 8 Jan
    assert demand["R308"].deadline == dl
    assert demand["R308"].ready_from == at_local_day(day, "11:00")   # check-out deadline, not presence

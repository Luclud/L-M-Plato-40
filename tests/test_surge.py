"""S05 event-weekend agent: detected ahead of the surge, re-plan only once the sick call is known,
extra staff only via approval, robots only on shift, one episode per surge."""
import dataclasses

import pandas as pd
import pytest

from plato40 import engine
from plato40.detectors.s05_surge import (BUSY_SHARE, MIN_HISTORY_DAYS, SurgeAgent, busy_threshold,
                                         window_capacity_rooms)
from plato40.state import State

T = pd.Timestamp


def _run(data):
    log = engine.run(data, [SurgeAgent()], verbose=False)
    return [d.to_json() for d in log.decisions]


@pytest.fixture(scope="module")
def decisions(qs):
    return _run(qs)


def _su(ds):
    return [d for d in ds if d["issue_type"] == "occupancy_surge"]


def test_quickstart_event_weekend_detected_in_time(decisions):
    hits = [d for d in _su(decisions) if T("2026-01-21T00:00Z") <= T(d["timestamp_utc"]) <= T("2026-01-23T06:00Z")]
    assert hits, "no occupancy_surge decision before 23 Jan 06:00Z"
    assert any(d["action"] == "request_human_approval" and d["requires_human_approval"] for d in hits)


def test_replan_at_sick_call_not_before(decisions):
    known = T("2026-01-24T05:30Z")
    sick = [d for d in _su(decisions) if "CLEANER_1 sick" in d["reason"]]
    assert sick and min(T(d["timestamp_utc"]) for d in sick) == known
    before = [d for d in _su(decisions) if T(d["timestamp_utc"]) < known]
    assert all("sick" not in d["reason"] for d in before)
    # the plan before the sick call still counts CLEANER_1 as available (it was not known yet)
    assert any("CLEANER_1" in d["affected_assets"] for d in before if d["action"] == "request_human_approval")


def test_sick_call_known_later_moves_the_replan(qs):
    r = qs.roster.copy()
    m = (r.resource_id == "CLEANER_1") & (r.status == "sick") & (r.status_known_utc == T("2026-01-24T05:30Z"))
    assert m.sum() == 1
    r.loc[m, "status_known_utc"] = T("2026-01-24T08:00Z")
    ds = _su(_run(dataclasses.replace(qs, roster=r)))
    sick = [d for d in ds if "CLEANER_1 sick" in d["reason"]]
    assert sick and min(T(d["timestamp_utc"]) for d in sick) == T("2026-01-24T08:00Z")


def test_extra_staff_only_with_approval_and_no_invented_capacity(qs, decisions):
    asks = [d for d in _su(decisions) if d["action"] == "request_human_approval"]
    assert asks
    for d in asks:
        assert d["requires_human_approval"] is True and d["autonomy"] != "autonomous"
        assert "capacity is not assumed until approved" in d["reason"]
    replan = [d for d in asks if "CLEANER_1 sick" in d["reason"]][0]
    assert "CLEANER_1" not in replan["affected_assets"]   # the sick cleaner is not counted as capacity


def test_robots_only_on_shift_as_known(qs_state, decisions):
    rob = [d for d in _su(decisions) if d["action"] == "dispatch_robot"]
    assert rob
    for d in rob:
        ok, why = qs_state.available(d["resource_id"], T(d["scheduled_for_utc"]), known_at=T(d["timestamp_utc"]))
        assert ok, why
        assert d["requires_human_approval"] is False


def test_one_episode_per_event_weekend(decisions):
    su = _su(decisions)
    assert len({d["episode_id"] for d in su}) == 1
    assert len(su) <= 8


def test_no_future_data(qs, decisions):
    cut = T("2026-01-23T12:00Z")
    r = qs.roster.copy()
    unknown = r.status_known_utc.notna() & (r.status_known_utc > cut)
    r.loc[unknown, "status"] = "scheduled"
    trunc = dataclasses.replace(
        qs,
        events=qs.events[qs.events.timestamp_utc <= cut].reset_index(drop=True),
        guest_requests=qs.guest_requests[qs.guest_requests.timestamp_utc <= cut].reset_index(drop=True),
        telemetry=qs.telemetry[qs.telemetry.timestamp_utc <= cut].reset_index(drop=True),
        bookings=qs.bookings[qs.bookings.created_utc <= cut].reset_index(drop=True),
        roster=r,
    )
    State(trunc)
    strip = lambda ds: [{k: v for k, v in d.items() if k not in ("event_id", "episode_id")}
                        for d in ds if T(d["timestamp_utc"]) <= cut]
    assert strip(_run(trunc)) == strip(decisions)


def test_threshold_helpers():
    assert busy_threshold(pd.Series([5] * (MIN_HISTORY_DAYS - 1)), 40) == BUSY_SHARE * 40
    assert busy_threshold(pd.Series([10] * 50 + [30] * 10), 40) == 30
    assert window_capacity_rooms(2, 45) == pytest.approx(2 * 240 / 45)
    assert window_capacity_rooms(0, 45) == 0


def test_robot_out_of_service_replans_in_same_episode(qs):
    base = _su(_run(qs))
    rob = [d for d in base if d["action"] == "dispatch_robot" and d["scheduled_for_utc"].startswith("2026-01-23")]
    assert rob
    robot, known = rob[0]["resource_id"], T("2026-01-22T15:00Z")
    r = qs.roster.copy()
    m = (r.resource_id == robot) & (r.start_utc.dt.tz_convert("Europe/Vienna").dt.date == T("2026-01-23").date())
    assert m.sum() == 1
    r.loc[m, "status"], r.loc[m, "status_known_utc"] = "out_of_service", known
    ds = _su(_run(dataclasses.replace(qs, roster=r)))
    follow = [d for d in ds if robot in d["reason"] and "Re-plan" in d["reason"]]
    assert follow and min(T(d["timestamp_utc"]) for d in follow) == known
    assert {d["episode_id"] for d in follow} == {rob[0]["episode_id"]}
    assert not [d for d in ds if T(d["timestamp_utc"]) < known and "Re-plan" in d["reason"]]
    for d in ds:   # nothing after the status is known relies on the unavailable robot that day
        if d["action"] == "dispatch_robot" and T(d["timestamp_utc"]) >= known:
            assert not (d["resource_id"] == robot and d["scheduled_for_utc"].startswith("2026-01-23"))

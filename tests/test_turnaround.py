"""S01 turnaround agent on quickstart: incidents in time, realistic ETAs, safe releases, causality."""
import dataclasses
import re

import pandas as pd
import pytest

from plato40 import engine
from plato40.detectors.s01_turnaround import TurnaroundAgent
from plato40.state import State

T = pd.Timestamp


def _run(data):
    log = engine.run(data, [TurnaroundAgent()], verbose=False)
    return [d.to_json() for d in log.decisions]


@pytest.fixture(scope="module")
def decisions(qs):
    return _run(qs)


def _ta(decisions, room=None):
    return [d for d in decisions if d["issue_type"] == "room_turnaround"
            and (room is None or room in d["affected_assets"])]


@pytest.mark.parametrize("room,start,good_by", [
    ("R308", "2026-01-07T22:00:00Z", "2026-01-08T09:00:00Z"),
    ("R207", "2026-01-22T22:00:00Z", "2026-01-23T10:00:00Z"),
])
def test_quickstart_s01_incidents_detected_in_time(decisions, room, start, good_by):
    hits = [d for d in _ta(decisions, room) if T(start) <= T(d["timestamp_utc"]) <= T(good_by)]
    assert hits, f"no room_turnaround decision for {room} before {good_by}"


def test_every_early_checkin_request_answered_with_consistent_eta(qs, decisions):
    reqs = qs.guest_requests[qs.guest_requests.request_type == "early_checkin"]
    by_req = {}
    for d in decisions:
        for rid in d.get("guest_request_ids", []):
            if d["action"] == "notify_guest":
                by_req.setdefault(rid, d)
    for r in reqs.itertuples():
        assert r.request_id in by_req, f"{r.request_id} not answered"
        d = by_req[r.request_id]
        assert T(d["timestamp_utc"]) >= r.timestamp_utc
        if d["issue_type"] != "room_turnaround":
            continue
        # stated time must be >= planned cleaning end ("~HH:MM local" in the plan text of the episode)
        said = re.findall(r"(\d\d:\d\d)", d["message"]["text"])
        planned = [x for x in decisions if x.get("episode_id") == d["episode_id"]
                   and x["timestamp_utc"] <= d["timestamp_utc"]]
        ends = re.findall(r"clean ~(\d\d:\d\d)", " ".join(x["reason"] for x in planned))
        if said and ends:
            assert said[-1] >= ends[-1], (d["message"]["text"], ends)


def test_release_only_when_clean_and_vacant(qs, qs_state, decisions):
    rel = [d for d in decisions if d["action"] == "release_room"]
    assert rel
    for d in rel:
        for room in d["affected_assets"]:
            ok, why = qs_state.can_release(room, T(d["timestamp_utc"]))
            assert ok, (d["event_id"], why)
        assert not str(d.get("resource_id", "")).startswith("ROBOT")


def test_resources_available_as_known(qs_state, decisions):
    for d in decisions:
        if d.get("resource_id"):
            when = T(d.get("scheduled_for_utc") or d["timestamp_utc"])
            ok, why = qs_state.available(d["resource_id"], when, known_at=T(d["timestamp_utc"]))
            assert ok, (d["event_id"], why)


def test_sick_call_triggers_replan_not_before(decisions):
    r308 = _ta(decisions, "R308")
    jan8 = [d for d in r308 if "2026-01-07T22" <= d["timestamp_utc"] <= "2026-01-08T09"]
    sched = [d for d in jan8 if d["action"] == "schedule_cleaning"]
    assert any(d["timestamp_utc"] == "2026-01-08T05:15:00Z" and d["resource_id"] != "CLEANER_1" for d in sched)
    assert all(d["resource_id"] == "CLEANER_1" for d in sched if d["timestamp_utc"] < "2026-01-08T05:15:00Z")


def test_oncall_only_for_waiting_guests_in_window(decisions):
    calls = [d for d in decisions if d["action"] == "call_oncall_housekeeper"]
    assert calls
    for d in calls:
        assert d["requires_human_approval"] is True
        ep = [x for x in decisions if x.get("episode_id") == d["episode_id"] and x["timestamp_utc"] <= d["timestamp_utc"]]
        assert any("waiting" in x["reason"].lower() for x in ep)


def test_lookalike_clean_room_is_not_a_conflict(decisions):
    """Late check-out / vacant-clean early check-in answers are guest service, not turnaround episodes."""
    gs = [d for d in decisions if d["issue_type"] == "guest_request"]
    assert gs and all(d["action"] == "notify_guest" for d in gs)


def test_volume_per_episode_is_low(decisions):
    ta = _ta(decisions)
    eps = {d["episode_id"] for d in ta}
    assert len(ta) / len(eps) <= 3.5


def test_no_future_data(qs, decisions):
    """Truncate every availability-timed input at a cutoff: decisions up to the cutoff stay identical."""
    cut = T("2026-01-08T09:00:00Z")
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
    State(trunc)  # builds
    strip = lambda ds: [{k: v for k, v in d.items() if k not in ("event_id", "episode_id", "dependencies")}
                        for d in ds if T(d["timestamp_utc"]) <= cut]
    assert strip(_run(trunc)) == strip(decisions)

"""T04 guest service agent: coverage, deadlines, language, consent, comfort-ack rule, causality."""
import copy
import json

import pandas as pd
import pytest

from conftest import ROOT
from plato40 import engine, io
from plato40.decision import make_decision
from plato40.detectors import REGISTRY
from plato40.detectors.base import TriggerDetector
from plato40.detectors.guest_service import (COMFORT_TYPES, TURNAROUND_TYPES, GuestServiceAgent, is_t04_owned,
                                             response_stats)

DEV = ROOT / "data" / "dev"


def _run(data, extra=()):
    return engine.run(data, [*extra, GuestServiceAgent()], verbose=False)


def _first_decisions(log):
    first = {}
    for d in sorted(log.decisions, key=lambda d: (d.timestamp_utc, d.event_id)):
        for rid in d.guest_request_ids:
            first.setdefault(rid, d)
    return first


def _check_coverage(data, log, label):
    gr = data.guest_requests
    owned = gr[gr.request_type.map(is_t04_owned)]
    first = _first_decisions(log)
    lat = []
    for row in owned.itertuples(index=False):
        d = first.get(row.request_id)
        assert d is not None, f"{label}: {row.request_id} ({row.request_type}) unanswered"
        t = pd.Timestamp(d.timestamp_utc)
        assert row.timestamp_utc <= t <= row.response_due_utc, f"{label}: {row.request_id} late/early"
        assert d.message and d.message["language"] == row.language, f"{label}: {row.request_id} language"
        lat.append({"latency_min": (t - row.timestamp_utc).total_seconds() / 60, "on_time": True})
    # exactly one *first* decision per request: one episode per request id
    eps = {}
    for d in log.decisions:
        for rid in d.guest_request_ids:
            eps.setdefault(rid, set()).add(d.episode_id)
    assert all(len(v) == 1 for v in eps.values())
    s = response_stats(lat)
    print(f"\n[{label}] T04-owned requests: {s['n']}, on time {s['on_time']}/{s['n']}, "
          f"response median {s['median_min']:.1f} min, p95 {s['p95_min']:.1f} min")
    return first


def test_registered():
    assert REGISTRY["guest_service"] is GuestServiceAgent


def test_quickstart_coverage_deadlines_language(qs):
    log = _run(qs)
    first = _check_coverage(qs, log, "quickstart")
    # turnaround types are left to T05
    skipped = set(qs.guest_requests[qs.guest_requests.request_type.isin(TURNAROUND_TYPES)].request_id)
    assert skipped and not skipped & set(first)
    # comfort types: acknowledged (comfort agent is silent here) within deadline, in guest language
    comfort = qs.guest_requests[qs.guest_requests.request_type.isin(COMFORT_TYPES)]
    for row in comfort.itertuples(index=False):
        d = first[row.request_id]
        assert d.action == "notify_guest" and pd.Timestamp(d.timestamp_utc) <= row.response_due_utc
        assert d.message["language"] == row.language


def test_occupied_entry_needs_consent(qs, qs_state):
    log = _run(qs)
    entries = [d for d in log.decisions if d.action in ("request_inspection", "schedule_cleaning")]
    assert entries
    for d in entries:
        room = d.affected_assets[0]
        towels = "no room entry" in d.reason
        if qs_state.occupied(room, pd.Timestamp(d.timestamp_utc)) and not towels:
            assert d.requires_guest_consent and d.requires_human_approval, d.event_id
    # maintenance in occupied room: consent request message precedes the approval decision
    maint = [d for d in log.decisions if d.action == "request_inspection" and d.requires_guest_consent]
    assert maint
    for d in maint:
        ep = [x for x in log.decisions if x.episode_id == d.episode_id]
        assert ep[0].action == "notify_guest" and ep[0].message["kind"] in ("maintenance_consent_request",
                                                                            "service_ack")


def test_staff_only_on_shift(qs, qs_state):
    log = _run(qs)
    sched = [d for d in log.decisions if d.resource_id]
    assert sched
    for d in sched:
        ok, why = qs_state.available(d.resource_id, pd.Timestamp(d.scheduled_for_utc),
                                     known_at=pd.Timestamp(d.timestamp_utc))
        assert ok, (d.event_id, why)


class _FakeComfort(TriggerDetector):
    """Stands in for T02: one room-level decision right after each temperature request."""
    name = "comfort"

    def build_triggers(self, data, state):
        gr = data.guest_requests
        for row in gr[gr.request_type.isin(COMFORT_TYPES)].itertuples(index=False):
            yield row.timestamp_utc + pd.Timedelta(minutes=10), "x", row.room_id

    def handle(self, ctx, ts, kind, room):
        ctx.log.add(make_decision(t=ctx.t, affected_assets=[room], issue_type="heating_comfort",
                                  action="adjust_room_setpoint", priority="medium", reason="test",
                                  agent=self.name), episode_key=f"fake:{room}:{ctx.t}")


def test_comfort_owned_requests_not_duplicated(qs):
    log = _run(qs, extra=[_FakeComfort()])
    comfort_ids = set(qs.guest_requests[qs.guest_requests.request_type.isin(COMFORT_TYPES)].request_id)
    mine = [d for d in log.decisions if d.agent == "guest_service" and set(d.guest_request_ids) & comfort_ids]
    assert not mine


def _truncate(data, cut):
    d = copy.copy(data)
    d.guest_requests = data.guest_requests[data.guest_requests.timestamp_utc <= cut].reset_index(drop=True)
    d.access = data.access[data.access.timestamp_utc <= cut].reset_index(drop=True)
    return d


def test_no_future_data(qs):
    cut = qs.start + (qs.end - qs.start) / 2
    full = [d.to_json() for d in _run(qs).decisions if pd.Timestamp(d.timestamp_utc) <= cut]
    part = [d.to_json() for d in _run(_truncate(qs, cut)).decisions if pd.Timestamp(d.timestamp_utc) <= cut]
    key = lambda x: json.dumps({k: v for k, v in x.items() if k not in ("event_id", "episode_id", "dependencies")},
                               sort_keys=True, ensure_ascii=False)
    assert sorted(map(key, full)) == sorted(map(key, part))


def _with_request(data, **row):
    d = copy.copy(data)
    base = {"booking_id": "BKTEST", "channel": "app_chat", "text": "test", "requires_guest_consent": "not_required"}
    base.update(row)
    d.guest_requests = (pd.concat([data.guest_requests, pd.DataFrame([base])], ignore_index=True)
                        .sort_values("timestamp_utc").reset_index(drop=True))
    return d


def test_access_issue_checks_access_log(qs):
    t = qs.start + pd.Timedelta(days=3, hours=19, minutes=2)
    d = _with_request(qs, request_id="GRTEST1", timestamp_utc=t, room_id="R203", language="fr",
                      request_type="access_issue", priority="critical",
                      response_due_utc=t + pd.Timedelta(minutes=15))
    d.access = (pd.concat([qs.access, pd.DataFrame([{"timestamp_utc": t - pd.Timedelta(minutes=5), "door_id": "R203",
                                                      "credential": "BKTEST", "result": "denied"}])],
                          ignore_index=True).sort_values("timestamp_utc").reset_index(drop=True))
    log = _run(d)
    mine = [x for x in log.decisions if "GRTEST1" in x.guest_request_ids]
    assert [x.action for x in mine] == ["notify_guest", "flag_anomaly"]
    assert mine[0].message["language"] == "fr" and mine[0].priority == "critical"
    assert "1 denied attempt" in mine[1].reason and any("access_log.csv:R203" in e for e in mine[1].evidence)
    assert pd.Timestamp(mine[0].timestamp_utc) <= t + pd.Timedelta(minutes=15)


def test_night_request_goes_to_next_shift(qs, qs_state):
    night = qs.start + pd.Timedelta(days=2, hours=1, minutes=7)   # ~02:07 local, no housekeeping on shift
    d = _with_request(qs, request_id="GRTEST2", timestamp_utc=night, room_id="R105", language="nl",
                      request_type="extra_towels", priority="low", response_due_utc=night + pd.Timedelta(hours=4))
    log = _run(d)
    (x,) = [x for x in log.decisions if "GRTEST2" in x.guest_request_ids]
    slot = pd.Timestamp(x.scheduled_for_utc)
    assert x.action == "schedule_cleaning" and slot > night + pd.Timedelta(hours=2)
    assert qs_state.available(x.resource_id, slot, known_at=pd.Timestamp(x.timestamp_utc))[0]
    assert x.message["language"] == "nl" and x.message["kind"] == "towels_delivery"


def test_dev_coverage():
    if not (DEV / "manifest.json").exists():
        pytest.skip("dev data missing")
    data = io.load(DEV)
    _check_coverage(data, _run(data), "dev")

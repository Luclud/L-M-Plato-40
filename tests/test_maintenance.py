"""S04 maintenance cascade (AHU1 filter): detection, gap handling, service planning, causality."""
import dataclasses

import numpy as np
import pandas as pd
import pytest
from conftest import ROOT

from plato40 import engine, io
from plato40.asof import AsOf
from plato40.detectors.s04_maintenance import MaintenanceAgent
from plato40.state import State

T = pd.Timestamp
DEV = ROOT / "data" / "dev"


def _run(data):
    log = engine.run(data, [MaintenanceAgent()], verbose=False)
    return [d.to_json() for d in sorted(log.decisions, key=lambda d: (d.timestamp_utc, d.event_id))]


def _vent(rows):
    return [r for r in rows if r["issue_type"] == "ventilation_maintenance"]


def _truncate(data, cutoff):
    """Dataset as it would look at cutoff: no measurement, event, work order or roster news after it."""
    roster = AsOf(data, cutoff).roster().copy()   # as-of views are cached/shared: never modify in place
    roster.loc[roster.status_known_utc > cutoff, "status_known_utc"] = pd.NaT
    return dataclasses.replace(
        data,
        equipment=data.equipment[data.equipment.timestamp_utc <= cutoff].reset_index(drop=True),
        events=data.events[data.events.timestamp_utc <= cutoff].reset_index(drop=True),
        work_orders=data.work_orders[data.work_orders.created_utc <= cutoff].reset_index(drop=True),
        roster=roster,
    )


@pytest.fixture(scope="module")
def qs_rows(qs):
    return _run(qs)


def test_quickstart_filter_detected_early_one_episode(qs_rows):
    v = _vent(qs_rows)
    assert v, "AHU1 filter degradation not detected"
    first = v[0]
    assert "AHU1" in first["affected_assets"]
    assert T(first["timestamp_utc"]) <= T("2026-01-16T12:00:00Z")
    assert T(first["timestamp_utc"]) >= T("2026-01-14T00:00:00Z")
    # one episode for the whole incident, spanning the sensor gap (16-21 Jan)
    eps = {r["episode_id"] for r in v}
    assert len(eps) == 1
    ts = [T(r["timestamp_utc"]) for r in v]
    assert min(ts) < T("2026-01-16T22:30:00Z") and max(ts) > T("2026-01-21T03:15:00Z")
    gap = [r for r in v if "UNKNOWN" in r["reason"]]
    assert len(gap) == 1 and T("2026-01-16T22:30:00Z") < T(gap[0]["timestamp_utc"]) < T("2026-01-17T06:00:00Z")
    # closed only after the filter change (dp drop), never during the gap
    assert "Verified" in v[-1]["reason"] and T(v[-1]["timestamp_utc"]) >= T("2026-01-23T10:00:00Z")


def test_quickstart_contractor_because_technician_on_vacation(qs_rows):
    orders = [r for r in _vent(qs_rows) if r["action"] == "order_service"]
    assert len(orders) == 1
    o = orders[0]
    assert o["requires_human_approval"] is True
    assert "vacation" in o["reason"] and "TECH_1" in " ".join(o["evidence"])
    assert T(o["timestamp_utc"]) < T("2026-01-19T07:00:00Z")          # ordered before the vacation starts
    assert T(o["scheduled_for_utc"]).normalize() > T(o["timestamp_utc"]).normalize()  # >= next working day
    assert T(o["scheduled_for_utc"]).weekday() < 5


def test_no_unavailable_technician_scheduled(qs, qs_state, qs_rows):
    disp = [r for r in qs_rows if r.get("resource_id")]
    assert disp, "expected at least one technician dispatch"
    for r in disp:
        t = T(r["timestamp_utc"])
        ok, why = qs_state.available(r["resource_id"], T(r["scheduled_for_utc"]), known_at=t)
        assert ok, (r["event_id"], why)
    # sick day known 15 Jan 01:15Z -> re-plan away from SH000054 at that moment
    replan = [r for r in disp if "SH000054" in r["reason"]]
    assert replan and T(replan[0]["timestamp_utc"]) == T("2026-01-15T01:15:00Z")


def test_lookalikes_not_flagged(qs_rows, qs):
    # night/reduced airflow mode and normal slow loading before 14 Jan -> no maintenance decision
    assert not [r for r in _vent(qs_rows) if T(r["timestamp_utc"]) < T("2026-01-14T00:00:00Z")]
    # a sensor gap alone (no degradation) is data quality, never a maintenance incident
    cut = T("2026-01-13T00:00:00Z")
    d = _truncate(qs, cut)
    eq = d.equipment.copy()
    gap = (eq.asset_id == "AHU1") & (eq.metric == "filter_dp_pa") & eq.timestamp_utc.between(
        T("2026-01-10T06:00:00Z"), T("2026-01-11T06:00:00Z"))
    eq.loc[gap, "value_num"] = np.nan
    eq.loc[gap, "value"] = np.nan
    rows = _run(dataclasses.replace(d, equipment=eq))
    assert not _vent(rows)
    dq = [r for r in rows if r["issue_type"] == "data_quality" and "AHU1" in r["affected_assets"]
          and T(r["timestamp_utc"]) < cut]                      # (the truncated tail is a gap too)
    assert len(dq) == 1 and "unknown" in dq[0]["reason"]


@pytest.mark.parametrize("cutoff", ["2026-01-15T06:00:00Z", "2026-01-17T06:00:00Z"])
def test_no_future_data(qs, qs_rows, cutoff):
    cut = T(cutoff)
    full = [(r["timestamp_utc"], r["action"], r["reason"]) for r in qs_rows if T(r["timestamp_utc"]) <= cut]
    trunc = [(r["timestamp_utc"], r["action"], r["reason"]) for r in _run(_truncate(qs, cut))
             if T(r["timestamp_utc"]) <= cut]
    assert full and full == trunc


@pytest.mark.skipif(not (DEV / "manifest.json").exists(), reason="dev data missing")
def test_dev_incidents_and_postponed_orders():
    d = io.load(DEV)
    v = _vent(_run(d))
    eps = {}
    for r in v:
        eps.setdefault(r["episode_id"], []).append(r)
    windows = [(T("2026-07-15T01:00:00Z"), T("2026-07-29T01:00:00Z")),   # WO00042 heavily loaded
               (T("2026-10-10T04:00:00Z"), T("2026-10-24T04:00:00Z"))]   # WO00063 heavily loaded
    for a, b in windows:
        assert any(a <= T(r["timestamp_utc"]) <= b for r in v), (a, b)
    matched = {r["episode_id"] for r in v for a, b in windows if a <= T(r["timestamp_utc"]) <= b}
    assert len(set(eps) - matched) <= 3
    defer = [r for r in v if r["action"] == "defer_action"]
    assert {e for r in defer for e in r["evidence"] if "postponed" in e}, "postponed WOs not reflected"
    assert all("lift platform" in r["reason"] for r in defer)

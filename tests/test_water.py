"""S02 water leak agent: loud + subtle detection, look-alikes, causality."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from plato40 import engine
from plato40.detectors.s02_water import WaterAgent, _streak


def _run(data):
    return [d for d in engine.run(data, [WaterAgent()], verbose=False).decisions]


def _truncate(data, t):
    """Dataset as it would look if the replay stopped at t (nothing after t exists)."""
    ts = lambda df, col="timestamp_utc": df[df[col] <= t].reset_index(drop=True)
    manifest = dict(data.manifest, end_utc=(t + pd.Timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return dataclasses.replace(
        data, manifest=manifest, telemetry=ts(data.telemetry), room_water=ts(data.room_water),
        water_main=ts(data.water_main), events=ts(data.events), guest_requests=ts(data.guest_requests),
        work_orders=ts(data.work_orders, "created_utc"), bookings=ts(data.bookings, "created_utc"),
        access=ts(data.access))


@pytest.fixture(scope="module")
def qs_water(qs):
    return _run(qs)


def _first(ds, room, issue="water_leak"):
    return next(d for d in sorted(ds, key=lambda d: d.timestamp_utc) if room in d.affected_assets and d.issue_type == issue)


def test_streak():
    m = np.array([1, 1, 0, 1, 1, 1, 0], bool)
    assert _streak(m).tolist() == [1, 2, 0, 1, 2, 3, 0]


def test_r104_burst_detected_in_time(qs_water):
    d = _first(qs_water, "R104")
    assert pd.Timestamp(d.timestamp_utc) <= pd.Timestamp("2026-01-12T19:00:00Z")
    actions = {x.action for x in qs_water if x.episode_id == d.episode_id}
    assert {"flag_anomaly", "request_inspection", "water_shutoff"} <= actions
    shut = next(x for x in qs_water if x.episode_id == d.episode_id and x.action == "water_shutoff")
    assert shut.requires_human_approval and shut.requires_guest_consent


def test_r305_toilet_before_guest_report(qs_water):
    d = _first(qs_water, "R305")
    assert pd.Timestamp(d.timestamp_utc) < pd.Timestamp("2026-01-22T08:50:00Z")
    eps = {x.episode_id for x in qs_water if "R305" in x.affected_assets}
    assert len(eps) == 1


def test_lookalikes_not_flagged(qs_water):
    rooms = {a for d in qs_water for a in d.affected_assets if a.startswith("R")}
    # R307: occupied, 4 x 15 min of 24-80 L (bath/shower, 15 Jan); R309/R208: cleaning with ~10 L/15 min
    assert rooms <= {"R104", "R305"}, rooms


def test_shower_only_occupied_night_not_flagged(qs, qs_state):
    """Synthetic: an occupied room with only bursty night showers never opens an episode."""
    rw = qs.room_water.copy()
    room = "R307"
    m = (rw.room_id == room) & (rw.timestamp_utc.dt.hour.isin([0, 1, 2]))
    burst = m & (rw.timestamp_utc.dt.minute.isin([0, 15, 30]))  # 45 min of shower, 15 min pause, each night hour
    rw.loc[m, "rate_l_per_interval"] = 0.0
    rw.loc[burst, "rate_l_per_interval"] = 30.0
    out = _run(dataclasses.replace(qs, room_water=rw))
    assert not [d for d in out if room in d.affected_assets]


def test_no_decision_near_meter_reset_or_legionella(qs, qs_water):
    for r in qs.room_water[qs.room_water.reset].itertuples(index=False):
        for d in qs_water:
            if r.room_id in d.affected_assets:
                assert abs(pd.Timestamp(d.timestamp_utc) - r.timestamp_utc) > pd.Timedelta(hours=2)
    wo = qs.work_orders[qs.work_orders.summary.str.contains("Legionella", na=False)]
    for w in wo.itertuples(index=False):
        for d in qs_water:
            t = pd.Timestamp(d.timestamp_utc)
            if any(a in w.summary for a in d.affected_assets):
                assert not (w.scheduled_utc - pd.Timedelta(hours=1) <= t <= w.completed_utc + pd.Timedelta(hours=2))


@pytest.mark.parametrize("room", ["R104", "R305"])
def test_causal_same_first_decision(qs, qs_water, room):
    full = _first(qs_water, room)
    t = pd.Timestamp(full.timestamp_utc)
    trunc = _first(_run(_truncate(qs, t)), room)
    assert (trunc.timestamp_utc, trunc.action, trunc.affected_assets, trunc.reason) == \
           (full.timestamp_utc, full.action, full.affected_assets, full.reason)


def test_gap_leak_detected_synthetic(qs):
    """Add a constant 6 L/15 min loss to the main meter from 15 Jan -> one gap episode on WM_MAIN."""
    wm = qs.water_main.copy()
    leak = wm.timestamp_utc >= pd.Timestamp("2026-01-15T00:00:00Z")
    wm.loc[leak, "rate_l_per_interval"] += 6.0
    out = _run(dataclasses.replace(qs, water_main=wm))
    gap = [d for d in out if "WM_MAIN" in d.affected_assets]
    assert gap and len({d.episode_id for d in gap}) == 1
    assert pd.Timestamp("2026-01-16T00:00:00Z") < pd.Timestamp(gap[0].timestamp_utc) < pd.Timestamp("2026-01-18T00:00:00Z")
    assert not [d for d in _run(qs) if "WM_MAIN" in d.affected_assets]


def test_evidence_never_after_decision(qs_water):
    from validate import EVIDENCE_TS
    for d in qs_water:
        for ev in d.evidence:
            for m in EVIDENCE_TS.findall(ev):
                assert pd.Timestamp(m) <= pd.Timestamp(d.timestamp_utc) + pd.Timedelta(minutes=15), (d.event_id, ev)

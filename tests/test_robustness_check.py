"""Robustness check script: trimmed copies are consistent and loadable (quickstart, fast)."""
import importlib.util
import json
from pathlib import Path

import pandas as pd

from plato40 import engine, io
from plato40.detectors import build
from plato40.state import State

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("robustness_check", ROOT / "scripts" / "robustness_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)
T = pd.Timestamp


def test_variants_follow_the_dataset_not_fixed_dates():
    v = rc.variants(T("2026-01-01T00:00Z"), T("2027-01-01T00:00Z"))
    assert set(v) == {"start_1mar", "start_1jul", "first_60d", "first_21d"}
    assert v["first_21d"] == (T("2026-01-01T00:00Z"), T("2026-01-22T00:00Z"))
    short = rc.variants(T("2026-01-05T00:00Z"), T("2026-01-26T00:00Z"))   # quickstart: only what fits
    assert set(short) == set()


def test_trim_is_consistent_and_runs(qs, tmp_path):
    s, e = T("2026-01-12T00:00Z"), T("2026-01-17T00:00Z")
    rc.trim(ROOT / "data" / "quickstart", tmp_path / "trim", s, e, "test")
    d = io.load(tmp_path / "trim")
    assert (d.start, d.end) == (s, e) and d.manifest["days"] == 5
    for df, col in [(d.telemetry, "timestamp_utc"), (d.events, "timestamp_utc"), (d.logistics, "timestamp_utc"),
                    (d.guest_requests, "timestamp_utc"), (d.work_orders, "created_utc"), (d.forecast, "issued_utc")]:
        assert df.empty or ((df[col] >= s) & (df[col] < e)).all(), col
    assert ((d.roster.end_utc > s) & (d.roster.start_utc < e)).all()
    # stays open at the new start are carried over as stay_in_progress
    occupied = {r for r in qs.room_ids if State(qs).occupied(r, s - pd.Timedelta(seconds=1))}
    sip = set(d.events[d.events.type == "stay_in_progress"].room_id)
    assert sip and sip <= occupied
    log = engine.run(d, build(["data_quality", "water"]), verbose=False)
    assert all(T(x.timestamp_utc) >= s for x in log.decisions)
    json.dumps([x.to_json() for x in log.decisions])

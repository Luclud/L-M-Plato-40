"""Bad-data robustness of the loader (holdout may contain quirks the dev data does not):
duplicate forecast rows, non-numeric measurement cells, unparsable timestamps, shuffled files."""
import dataclasses
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from plato40 import engine, io
from plato40.detectors import build
from plato40.detectors.s04_maintenance import features

ROOT = Path(__file__).resolve().parents[1]
QS = ROOT / "data" / "quickstart"


def _rw(path: Path, fn):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    fn(df).to_csv(path, index=False)


@pytest.fixture(scope="module")
def messy(tmp_path_factory):
    if not (QS / "manifest.json").exists():
        pytest.skip("quickstart data missing")
    d = tmp_path_factory.mktemp("messy") / "qs"
    shutil.copytree(QS, d)
    # every file shuffled, 2 % of rows duplicated (incl. the weather forecast, which had no dedupe)
    for f in d.glob("*.csv"):
        _rw(f, lambda df: pd.concat([df, df.sample(frac=0.02, random_state=1)]).sample(frac=1, random_state=2))

    def garbage(df):
        idx = df.sample(n=40, random_state=3).index
        df.loc[idx[:20], "temperature_c"] = "abc"
        df.loc[idx[20:30], "co2_ppm"] = "n/a?"
        df.loc[idx[30:], "timestamp_utc"] = "not a time"
        return df
    _rw(d / "room_telemetry.csv", garbage)
    _rw(d / "energy.csv", lambda df: df.assign(pv_kw=df.pv_kw.where(df.index % 97 != 0, "--")))
    return d


def test_loader_cleans_instead_of_crashing(messy):
    d = io.load(messy)
    assert not d.forecast.duplicated(["issued_utc", "target_utc"]).any()
    assert d.forecast.issued_utc.is_monotonic_increasing
    for col in ("temperature_c", "co2_ppm"):
        assert pd.api.types.is_float_dtype(d.telemetry[col])
    assert pd.api.types.is_float_dtype(d.energy.pv_kw)
    assert d.telemetry.timestamp_utc.notna().all()
    kinds = {(x.kind, x.source) for x in d.dq}
    assert {("non_numeric", "room_telemetry.csv"), ("non_numeric", "energy.csv"),
            ("unparsable_time", "room_telemetry.csv"), ("duplicate", "weather_forecast.csv")} <= kinds


def test_clean_data_loads_unchanged(qs):
    kinds = {x.kind for x in qs.dq}
    assert not kinds & {"non_numeric", "unparsable_time"}


def test_full_pipeline_runs_on_messy_data(messy):
    d = io.load(messy)
    log = engine.run(d, build(), verbose=False)
    assert log.decisions
    json.dumps([x.to_json() for x in log.decisions])


def test_ahu_nominal_fallback_is_causal(qs):
    """Without a nameplate airflow the nominal is learned up to t only: features before a cut-off
    must not change when the data after it changes."""
    ahu = next(a["id"] for a in qs.building["building"]["assets"] if a.get("type") == "air_handling_unit")
    full = features(qs, ahu, np.nan)
    cut = qs.start + pd.Timedelta(days=10)
    eq = qs.equipment.copy()
    later = (eq.timestamp_utc >= cut) & (eq.asset_id == ahu) & (eq.metric == "airflow_m3h")
    eq.loc[later, "value_num"] = eq.loc[later, "value_num"] * 3   # very different future
    qs2 = dataclasses.replace(qs, equipment=eq)
    changed = features(qs2, ahu, np.nan)
    before = full.index < cut
    pd.testing.assert_frame_equal(full[before], changed[before])

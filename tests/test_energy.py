"""T07 energy agent: day-ahead battery plan (simulated), SoC bounds, causality, one plan per day."""
import dataclasses
import re

import numpy as np
import pandas as pd
import pytest
from conftest import ROOT

from plato40 import engine, io
from plato40.clock import local
from plato40.detectors.energy import BatterySpec, EnergyAgent, plan_battery, simulate

DEV = ROOT / "data" / "dev"
ISO = re.compile(r"published (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")


def _run(data):
    agent = EnergyAgent()
    log = engine.run(data, [agent], verbose=False)
    return agent, [d.to_json() for d in sorted(log.decisions, key=lambda d: (d.timestamp_utc, d.event_id))]


@pytest.fixture(scope="module")
def qs_run(qs):
    return _run(qs)


def _check_bounds(power, soc, spec, soc0=None):
    assert soc.min() >= spec.soc_min - 1e-9 and soc.max() <= spec.soc_max + 1e-9
    assert power.max() <= spec.p_discharge + 1e-9 and -power.min() <= spec.p_charge + 1e-9
    # SoC consistent with power and one-way efficiency
    dsoc = np.diff(soc)
    expected = np.where(power >= 0, -power / spec.eta, -power * spec.eta)
    assert np.allclose(dsoc, expected, atol=1e-6)


@pytest.mark.parametrize("seed", range(20))
def test_plan_respects_soc_and_power_bounds(seed):
    rng = np.random.default_rng(seed)
    spec = BatterySpec()
    n = int(rng.choice([23, 24, 25]))
    net = rng.normal(20, 25, n)                      # includes PV surplus hours (negative)
    price = rng.uniform(0.1, 0.4, n)
    soc0 = float(rng.uniform(spec.soc_min, spec.soc_max))
    for tv in (None, 0.9 * price.mean()):
        power, soc = plan_battery(net, price, soc0, spec, terminal_value=tv)
        _check_bounds(power, soc, spec)
        if tv is None:
            assert soc[-1] >= soc[0] - 1e-9
        idle = np.where(net > 0, net * price, 0).sum()
        cost = np.where(net - power > 0, (net - power) * price, 0).sum()
        stored = 0 if tv is None else tv * (soc[-1] - soc[0])
        assert cost - stored <= idle + 1e-6           # never worse than doing nothing (on the forecast)


def test_plan_charges_cheap_discharges_expensive():
    spec = BatterySpec()
    price = np.array([0.10] * 6 + [0.30] * 6 + [0.20] * 12)
    power, soc = plan_battery(np.full(24, 30.0), price, 10.0, spec)
    assert power[:6].min() < 0 and power[6:12].max() > 0
    assert (power[:6] <= 1e-9).all() and (power[6:12] >= -1e-9).all()


def test_simulate_clips_to_bounds():
    spec = BatterySpec()
    grid, soc, applied = simulate([100.0] * 8 + [-100.0] * 8, np.full(16, 10.0), 50.0, spec, 0.25)
    assert soc.min() >= spec.soc_min - 1e-9 and soc.max() <= spec.soc_max + 1e-9
    assert np.abs(applied).max() <= 40 + 1e-9
    assert np.allclose(grid, (10.0 - applied) * 0.25)


def test_battery_spec_from_building(qs):
    spec = BatterySpec.from_building(qs.building)
    assert (spec.soc_min, spec.soc_max, spec.p_charge, spec.p_discharge, spec.rte) == (10, 95, 40, 40, 0.9)


def test_one_plan_per_day_with_published_price_evidence(qs_run, qs):
    agent, rows = qs_run
    assert rows, "expected daily plans on quickstart"
    dates = [local(pd.Timestamp(r["timestamp_utc"])).date() for r in rows]
    assert len(dates) == len(set(dates))
    assert len({r["episode_id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["issue_type"] == "energy_optimization" and r["action"] == "optimize_energy"
        assert r["autonomy"] == "autonomous" and r["agent"] == "energy"
        assert r["simulated_effect"].startswith("SIMULATED:") and "not a measured saving" in r["simulated_effect"]
        t = pd.Timestamp(r["timestamp_utc"])
        pubs = [pd.Timestamp(m) for ev in r["evidence"] for m in ISO.findall(ev) if ev.startswith("tariff.csv")]
        assert pubs and all(p <= t for p in pubs)
        # the plan targets the published day: scheduled start is after the decision
        assert pd.Timestamp(r["scheduled_for_utc"]) > t


def test_plan_soc_bounds_in_replay(qs_run):
    agent, _ = qs_run
    spec = agent.spec
    for p in agent.plans:
        _check_bounds(p["power"], p["soc"], spec)
        res = agent.evaluate_plan(p)
        if res is not None:
            assert spec.soc_min - 1e-9 <= res["soc_min"] <= res["soc_max"] <= spec.soc_max + 1e-9


def test_no_comfort_band_changes(qs_run, qs):
    """The energy agent never touches setpoints or rooms - it cannot push an occupied room outside 20-23 C."""
    _, rows = qs_run
    rooms = set(qs.room_ids)
    for r in rows:
        assert r["action"] != "adjust_room_setpoint"
        assert not rooms & set(r["affected_assets"])


def test_total_simulated_saving_reported(qs, capsys):
    agent, _ = _run(qs)
    out = capsys.readouterr().out
    assert "[energy]" in out and "SIMULATED saving vs observed schedule" in out and "not a measured" in out
    assert agent.summary["plans"] == len(agent.plans)
    assert np.isfinite(agent.summary["simulated_saving_eur"])


def _truncate(data, cutoff):
    e, w = data.energy, data.weather
    return dataclasses.replace(
        data,
        manifest={**data.manifest, "end_utc": (cutoff + pd.Timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        energy=e[e.timestamp_utc <= cutoff].reset_index(drop=True),
        weather=w[w.timestamp_utc <= cutoff].reset_index(drop=True),
        forecast=data.forecast[data.forecast.issued_utc <= cutoff].reset_index(drop=True),
        tariff=data.tariff[data.tariff.published_utc <= cutoff].reset_index(drop=True),
    )


@pytest.mark.parametrize("i", [0, 5, -1])
def test_no_future_data_truncated_replay_gives_same_plan(qs_run, qs, i):
    _, rows = qs_run
    ref = rows[i]
    cutoff = pd.Timestamp(ref["timestamp_utc"])
    _, cut_rows = _run(_truncate(qs, cutoff))
    same = [r for r in cut_rows if r["timestamp_utc"] == ref["timestamp_utc"]]
    assert len(same) == 1
    for k in ("reason", "evidence", "simulated_effect", "scheduled_for_utc"):
        assert same[0][k] == ref[k]


def test_warm_up_and_truncated_price_days_skipped(qs_run, qs):
    _, rows = qs_run
    first = pd.Timestamp(rows[0]["timestamp_utc"])
    assert first - qs.start >= pd.Timedelta(days=1)           # needs one day of history for profiles
    last_pub = qs.tariff.published_utc.max()
    assert all(pd.Timestamp(r["timestamp_utc"]) < last_pub for r in rows)  # 1-hour price stub at dataset end


@pytest.mark.skipif(not (DEV / "manifest.json").exists(), reason="dev data missing")
def test_dev_at_most_one_plan_per_day():
    agent, rows = _run(io.load(DEV))
    assert 300 <= len(rows) <= 366
    dates = [local(pd.Timestamp(r["timestamp_utc"])).date() for r in rows]
    assert len(dates) == len(set(dates))
    for p in agent.plans:
        _check_bounds(p["power"], p["soc"], agent.spec)

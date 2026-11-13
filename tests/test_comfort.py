"""T02 / S03: heat-pump degradation vs cold-weather look-alike, comfort protection, causality, bounded episodes."""
import dataclasses
import re

import numpy as np
import pandas as pd
import pytest

from plato40.asof import AsOf
from plato40.detectors.s03_comfort import ComfortAgent
from plato40.engine import run
from plato40.models import ExpectedCOP, causal_expected_cop, heatpump_frame, performance_residual
from plato40.state import State


def _run(data):
    agent = ComfortAgent()
    log = run(data, [agent], verbose=False)
    return agent, [d for d in log.decisions if d.agent == "comfort"]


@pytest.fixture(scope="module")
def qs_run(qs):
    return _run(qs)


# ---------------------------------------------------------------- model -----------------------------------------------
def test_expected_cop_model_explains_cold_and_flags_degradation():
    rng = np.random.default_rng(0)
    out = rng.uniform(-15, 10, 2000)
    sup = 45 - 0.5 * out
    cop = 3.4 + 0.07 * out - 0.015 * sup + rng.normal(0, 0.03, out.size)
    m = ExpectedCOP.fit(out, sup, cop)
    assert m is not None
    # naturally low COP in the cold is *expected*, residual ~0
    cold_actual = 3.4 + 0.07 * -14 - 0.015 * (45 + 7)
    assert abs(cold_actual / m.predict([-14], [52])[0] - 1) < 0.03
    # a 40 % drop at the same weather is a clear negative residual
    assert 0.6 * cold_actual / m.predict([-14], [52])[0] - 1 < -0.3
    assert ExpectedCOP.fit(out[:10], sup[:10], cop[:10]) is None  # not enough history


def test_causal_expected_cop_uses_history_only(qs):
    hp = heatpump_frame(qs)
    exp_full, _ = causal_expected_cop(hp)
    cut = qs.start + pd.Timedelta(days=8)
    hp_cut = hp.copy()
    hp_cut.loc[hp_cut.index >= cut, "heat_output_kw"] *= 0.1   # sabotage the future
    exp_cut, _ = causal_expected_cop(hp_cut)
    a = exp_full[exp_full.index < cut + pd.Timedelta(days=1)]
    pd.testing.assert_series_equal(a, exp_cut[a.index])
    r = performance_residual(hp, exp_full)
    assert r.dropna().index.min() >= hp.index[0]


# ---------------------------------------------------------------- quickstart incident -----------------------------------
def test_coldsnap_detected_in_time_with_service_in_same_episode(qs_run):
    _, ds = qs_run
    services = [d for d in ds if d.action in ("dispatch_technician", "order_service")]
    assert services, "no service action"
    first = min(services, key=lambda d: d.timestamp_utc)
    assert first.timestamp_utc <= "2026-01-15T12:00:00Z"
    assert first.requires_human_approval
    assert "HP1" in first.affected_assets and first.issue_type == "heating_comfort"
    assert "expected" in first.reason and "residual" in first.reason and "outdoor" in first.reason
    # TECH_1 sick that day (known before detection) -> contractor, never an unavailable technician
    assert first.action == "order_service"
    assert any(e.startswith("equipment.csv:HP1") for e in first.evidence)
    # the episode also carries pre-heat and a resolution, and the service is the first decision of it
    ep = [d for d in ds if d.episode_id == first.episode_id]
    assert ep[0] is first
    assert any(d.action == "adjust_room_setpoint" for d in ep)
    assert ep[-1].action == "flag_anomaly"


def test_one_building_episode_and_bounded_room_followups(qs_run, qs):
    _, ds = qs_run
    building = {d.episode_id for d in ds if d.action in ("dispatch_technician", "order_service")}
    assert len(building) == 1
    room_eps = {}
    for d in ds:
        if d.episode_id not in building:
            room = d.affected_assets[0]
            assert room in qs.room_ids
            room_eps.setdefault((room, d.timestamp_utc[:10]), set()).add(d.episode_id)
    assert all(len(v) == 1 for v in room_eps.values()), "more than one episode per room and day"
    days = len({k[1] for k in room_eps}) or 1
    assert len(room_eps) <= len(qs.room_ids) * days
    # at most 2 decisions per room episode (setpoint follow-up + guest notification)
    per_ep = pd.Series([d.episode_id for d in ds if d.episode_id not in building]).value_counts()
    assert per_ep.max() <= 2


def test_setpoints_within_band_or_approval(qs_run, qs):
    agent, ds = qs_run
    lo, hi = qs.building["constraints"]["occupied_comfort_c"]
    sp = [d for d in ds if d.action == "adjust_room_setpoint"]
    assert sp
    for d in sp:
        target = float(re.search(r"setpoint target ([\d.]+) C", d.reason).group(1))
        assert (lo <= target <= hi) or d.requires_human_approval
        assert not (lo <= target <= hi) or not d.requires_human_approval
    assert lo <= agent.setpoint_target() <= hi


def test_guest_notifications_reference_requests(qs_run, qs):
    _, ds = qs_run
    notes = [d for d in ds if d.action == "notify_guest"]
    assert notes
    gr = qs.guest_requests.set_index("request_id")
    for d in notes:
        assert d.guest_request_ids and d.message and d.message["text"]
        rid = d.guest_request_ids[0]
        assert gr.loc[rid, "request_type"] == "temperature_cold"
        assert pd.Timestamp(gr.loc[rid, "timestamp_utc"]) <= pd.Timestamp(d.timestamp_utc)
        assert gr.loc[rid, "room_id"] in d.affected_assets


# ---------------------------------------------------------------- causality ---------------------------------------------
def _truncate(d, cut):
    r = d.roster.copy()
    late = r.status_known_utc.notna() & (r.status_known_utc > cut)
    r.loc[late, "status"] = "scheduled"
    r.loc[late, "status_known_utc"] = pd.NaT
    man = dict(d.manifest, end_utc=cut.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return dataclasses.replace(
        d, manifest=man, roster=r,
        events=d.events[d.events.timestamp_utc <= cut],
        bookings=d.bookings[d.bookings.created_utc <= cut],
        access=d.access[d.access.timestamp_utc <= cut] if "timestamp_utc" in d.access else d.access,
        telemetry=d.telemetry[d.telemetry.timestamp_utc <= cut],
        equipment=d.equipment[d.equipment.timestamp_utc <= cut],
        energy=d.energy[d.energy.timestamp_utc <= cut],
        weather=d.weather[d.weather.timestamp_utc <= cut],
        forecast=d.forecast[d.forecast.issued_utc <= cut],
        tariff=d.tariff[d.tariff.published_utc <= cut],
        work_orders=d.work_orders[d.work_orders.created_utc <= cut],
        guest_requests=d.guest_requests[d.guest_requests.timestamp_utc <= cut],
    )


def test_no_future_data_truncated_replay_gives_same_decisions(qs_run, qs):
    _, ds = qs_run
    first = min((d for d in ds if d.action in ("dispatch_technician", "order_service")), key=lambda d: d.timestamp_utc)
    cut = pd.Timestamp(first.timestamp_utc) + pd.Timedelta(minutes=15)
    _, ds_cut = _run(_truncate(qs, cut))
    full = [(d.timestamp_utc, d.action, d.reason, d.evidence) for d in ds if pd.Timestamp(d.timestamp_utc) < cut]
    trunc = [(d.timestamp_utc, d.action, d.reason, d.evidence) for d in ds_cut if pd.Timestamp(d.timestamp_utc) < cut]
    assert full and full == trunc


def test_preheat_only_uses_issued_forecast_and_published_tariff(qs_run, qs, qs_state):
    agent, ds = qs_run

    class Ctx:
        pass

    n = 0
    for t in pd.date_range(qs.start + pd.Timedelta(days=2), qs.end, freq="7h"):
        ctx = Ctx()
        ctx.t, ctx.asof, ctx.state = t, AsOf(qs, t), qs_state
        plan = agent.plan_preheat(ctx)
        if plan is None:
            continue
        n += 1
        assert plan["issued"] <= t
        assert plan["published"] is None or plan["published"] <= t
        assert plan["slot"] >= t and plan["tmin"] > t
    assert n > 0
    for d in ds:
        if d.action == "adjust_room_setpoint" and d.reason.startswith("Pre-heat"):
            t = pd.Timestamp(d.timestamp_utc)
            for e in d.evidence:
                m = re.search(r"(issued|published) (\S+Z)", e)
                if m:
                    assert pd.Timestamp(m.group(2)) <= t, e


# ---------------------------------------------------------------- look-alikes -------------------------------------------
def test_cold_weather_with_healthy_heat_pump_is_not_degradation(qs):
    """Same cold snap, but HP1 delivers what a healthy unit delivers at that weather (and no lockouts): no service."""
    hp = heatpump_frame(qs)
    exp, _ = causal_expected_cop(hp)
    # healthy reference model from normal operation in the first week (before any degradation)
    first_week = hp[hp.normal & (hp.index < qs.start + pd.Timedelta(days=7))]
    m = ExpectedCOP.fit(first_week.outdoor_c, first_week.supply_temp_c, first_week.cop_obs)
    healthy_heat = pd.Series(m.predict(hp.outdoor_c, hp.supply_temp_c), index=hp.index) * hp.electric_kw
    eq = qs.equipment.copy()
    sel = (eq.asset_id == "HP1") & (eq.metric == "heat_output_kw")
    eq.loc[sel, "value_num"] = healthy_heat.reindex(eq.loc[sel, "timestamp_utc"]).to_numpy()
    st = (eq.asset_id == "HP1") & (eq.metric == "status") & eq.value.astype(str).str.startswith("lockout")
    eq.loc[st, "value"] = "running"
    healthy = dataclasses.replace(qs, equipment=eq)
    assert healthy.weather.outdoor_c.min() < -10        # the cold itself is still there
    _, ds = _run(healthy)
    assert not [d for d in ds if d.action in ("dispatch_technician", "order_service")]
    assert not ds, "no heating_comfort episodes without a heat-pump shortfall"


def test_open_window_and_vacant_rooms_are_not_cold_alarms(qs_run, qs):
    agent, ds = qs_run
    st = State(qs)
    for d in ds:
        if d.reason.startswith(tuple(qs.room_ids)) and d.action == "adjust_room_setpoint":
            room = d.affected_assets[0]
            t = pd.Timestamp(d.timestamp_utc)
            assert st.occupied(room, t)
            assert agent.window.loc[:t, room].iloc[-1] != 1
    kinds = {r["kind"] for r in agent.ruled_out}
    assert "cold_with_window_open" in kinds


def test_dispatches_technician_when_available_as_known(qs_run, qs):
    _, ds = qs_run
    t = pd.Timestamp(min(d.timestamp_utc for d in ds if d.action == "order_service"))
    r = qs.roster.copy()
    tech = r[r.role == "technician"].iloc[[0]].copy()
    tech["shift_id"], tech["start_utc"], tech["end_utc"] = "SHTEST", t - pd.Timedelta(hours=1), t + pd.Timedelta(hours=7)
    tech["status"], tech["status_known_utc"] = "scheduled", pd.NaT
    r = r[~((r.role == "technician") & (r.end_utc > t - pd.Timedelta(hours=1)) & (r.start_utc < t + pd.Timedelta(hours=7)))]
    r = pd.concat([r, tech]).sort_values("start_utc").reset_index(drop=True)
    _, ds2 = _run(dataclasses.replace(qs, roster=r))
    first = min((d for d in ds2 if d.action in ("dispatch_technician", "order_service")), key=lambda d: d.timestamp_utc)
    assert first.action == "dispatch_technician"
    assert first.resource_id == tech.resource_id.iloc[0] and first.requires_human_approval
    assert first.affected_assets == ["HP1"]

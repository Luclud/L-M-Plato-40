"""Building & energy optimisation (T07): day-ahead battery plan, effects SIMULATED.

Once per day, at the first tick after the next day's prices are published (13:00 local), the agent
- forecasts the next local day's hourly net load (building load - PV) from history known at t:
  non-HVAC load and PV as local-hour profiles of the last days, HVAC from a causal linear fit
  hvac_kw ~ outdoor_c applied to the latest *issued* temperature forecast (no PV/cloud forecast exists),
- plans BAT1 charge/discharge by dynamic programming over SoC (10-95 kWh, +/-40 kW, eta 0.9 round trip,
  stored energy at day end valued at rte x mean price): charge in cheap hours / PV surplus, discharge in expensive hours,
- emits ONE optimize_energy decision (episode energy:<local_date>) with the price window as evidence and a
  SIMULATED effect vs. the recently observed battery schedule. Exported energy is valued at 0 EUR/kWh
  (no feed-in tariff in the data), so savings are conservative.

Heat-pump pre-heating stays with the comfort agent (T02): this agent never changes setpoints, so it cannot
push an occupied room outside the 20-23 C band. finish() replays every plan on what actually happened
(ex-post, evaluation only - not used by any decision) and prints the total SIMULATED saving vs. the
observed schedule. Offline replay: proposed actions do not change later observations; nothing is real.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..clock import iso, local
from ..decision import make_decision
from .base import TriggerDetector

BAT = "BAT1"
PV = "PV1"
HP = "HP1"
SOC_STEP = 1.0                   # kWh resolution of the DP
PROFILE_DAYS = 7                 # local-hour profiles from the last 7 days
HVAC_FIT_DAYS = 14               # hvac ~ outdoor temperature fit window
MIN_HISTORY = pd.Timedelta(days=1)
MIN_HOURS = 20                   # skip truncated price days (e.g. dataset end)
CYCLE_COST = 0.01                # EUR per kWh battery throughput (wear proxy, objective only)
FEED_IN = 0.0                    # EUR/kWh for export - unknown in the data, valued at 0 (conservative)


@dataclass
class BatterySpec:
    soc_min: float = 10.0
    soc_max: float = 95.0
    p_charge: float = 40.0
    p_discharge: float = 40.0
    rte: float = 0.9

    @property
    def eta(self) -> float:      # one-way efficiency (charge and discharge)
        return math.sqrt(self.rte)

    @classmethod
    def from_building(cls, building: dict) -> "BatterySpec":
        for a in building.get("building", {}).get("assets", []):
            if a.get("id") == BAT or a.get("type") == "battery":
                lo, hi = a.get("soc_bounds_kwh", [cls.soc_min, cls.soc_max])
                return cls(float(lo), float(hi), float(a.get("max_charge_kw", cls.p_charge)),
                           float(a.get("max_discharge_kw", cls.p_discharge)), float(a.get("round_trip_efficiency", cls.rte)))
        return cls()


def _grid_cost(grid_kwh, price, feed_in=FEED_IN):
    return np.where(grid_kwh > 0, grid_kwh * price, grid_kwh * feed_in)


def plan_battery(net_kw, price, soc0: float, spec: BatterySpec, dt_h: float = 1.0,
                 cycle_cost: float = CYCLE_COST, feed_in: float = FEED_IN, terminal_value: float | None = None):
    """Optimal hourly battery schedule for a forecast net load (load - PV, kW) and prices (EUR/kWh).

    Returns (power_kw_signed, soc_kwh) with power + = discharge (dataset convention), soc of length n+1.
    SoC stays within [soc_min, soc_max], |power| within limits. Final SoC >= start SoC, or - with
    terminal_value (EUR/kWh) - any final SoC with stored energy valued at that price.
    """
    net = np.asarray(net_kw, dtype=float)
    pr = np.asarray(price, dtype=float)
    n = len(net)
    levels = np.arange(spec.soc_min, spec.soc_max + 1e-9, SOC_STEP)
    s0 = int(np.clip(round((soc0 - spec.soc_min) / SOC_STEP), 0, len(levels) - 1))
    delta = levels[None, :] - levels[:, None]                     # [from, to] SoC change, kWh
    batt = np.where(delta >= 0, -delta / spec.eta, -delta * spec.eta) / dt_h   # kW at terminals, + = discharge
    feasible = (batt <= spec.p_discharge + 1e-9) & (-batt <= spec.p_charge + 1e-9)
    wear = cycle_cost * np.abs(batt) * dt_h
    # backward DP: value[s] = min cost from step h with SoC s to the end
    if terminal_value is None:
        value = np.where(np.arange(len(levels)) >= s0, 0.0, np.inf)
    else:
        value = -terminal_value * (levels - levels[s0])
    choice = np.zeros((n, len(levels)), dtype=int)
    for h in range(n - 1, -1, -1):
        grid = (net[h] - batt) * dt_h
        step_cost = _grid_cost(grid, pr[h], feed_in) + wear
        total = np.where(feasible, step_cost + value[None, :], np.inf)
        choice[h] = total.argmin(axis=1)
        value = total[np.arange(len(levels)), choice[h]]
    idx = [s0]
    for h in range(n):
        idx.append(choice[h, idx[-1]])
    idx = np.array(idx)
    soc = levels[idx]
    power = batt[idx[:-1], idx[1:]]
    return power, soc


def simulate(power_kw, net_kw, soc0: float, spec: BatterySpec, dt_h: float):
    """Apply a battery power schedule to a net load, clipping to SoC/power limits.

    Returns (grid_kwh per step, soc trajectory length n+1, applied power)."""
    soc = [float(soc0)]
    applied, grid = [], []
    for p, nl in zip(power_kw, net_kw):
        p = float(np.clip(p, -spec.p_charge, spec.p_discharge))
        s = soc[-1]
        if p >= 0:
            p = min(p, max(0.0, (s - spec.soc_min) * spec.eta / dt_h))
            s -= p * dt_h / spec.eta
        else:
            p = -min(-p, max(0.0, (spec.soc_max - s) / spec.eta / dt_h))
            s -= p * dt_h * spec.eta
        soc.append(min(spec.soc_max, max(spec.soc_min, s)))
        applied.append(p)
        grid.append((nl - p) * dt_h)
    return np.array(grid), np.array(soc), np.array(applied)


class EnergyAgent(TriggerDetector):
    name = "energy"

    def __init__(self):
        self.plans: list[dict] = []
        self.summary: dict = {}

    def build_triggers(self, data, state):
        self.spec = BatterySpec.from_building(data.building)
        e = data.energy.set_index("timestamp_utc").sort_index()
        self.q = e                                                   # 15-min observations
        h = e.resample("1h").mean()
        h["base_kw"] = h.building_load_kw - h.hvac_electric_kw
        h["hour_end"] = h.index + pd.Timedelta(hours=1)
        h["lhour"] = h.index.tz_convert("Europe/Vienna").hour
        w = data.weather.set_index("timestamp_utc").sort_index().outdoor_c.resample("1h").mean()
        h["outdoor_c"] = w.reindex(h.index)
        self.h = h
        for pub in sorted(data.tariff.published_utc.dropna().unique()):
            yield pd.Timestamp(pub), "prices_published", pd.Timestamp(pub)

    # ---- forecast of the next day's net load (causal) ---------------------------------------------
    def _forecast(self, ctx, hours: pd.DatetimeIndex):
        known = self.h[self.h.hour_end <= ctx.t]
        if known.empty or known.index[-1] + pd.Timedelta(hours=1) - known.index[0] < MIN_HISTORY:
            return None
        prof = known[known.index >= ctx.t - pd.Timedelta(days=PROFILE_DAYS)].groupby("lhour")[
            ["base_kw", "pv_kw", "hvac_electric_kw"]].mean()
        lh = hours.tz_convert("Europe/Vienna").hour
        if not set(lh) <= set(prof.dropna().index):
            return None
        base = prof.base_kw.reindex(lh).to_numpy()
        pv = prof.pv_kw.reindex(lh).to_numpy()
        hvac = prof.hvac_electric_kw.reindex(lh).to_numpy()
        fit = known[known.index >= ctx.t - pd.Timedelta(days=HVAC_FIT_DAYS)][["outdoor_c", "hvac_electric_kw"]].dropna()
        fc = ctx.asof.forecast()
        issued = None
        if len(fit) >= 48 and fit.outdoor_c.std() > 0.5 and not fc.empty:
            b, a = np.polyfit(fit.outdoor_c, fit.hvac_electric_kw, 1)
            ft = fc.set_index("target_utc").outdoor_c_forecast.reindex(hours).to_numpy()
            pred = np.clip(a + b * ft, 0, None)
            hvac = np.where(np.isnan(pred), hvac, pred)
            if np.isfinite(ft).any():
                issued = fc.issued_utc.iloc[0]
        load = base + hvac
        return dict(net=load - pv, load=load, pv=pv, issued=issued)

    # ---- decision ---------------------------------------------------------------------------------
    def handle(self, ctx, ts, kind, payload):
        pub = payload
        tr = ctx.asof.tariff()
        day = tr[(tr.published_utc == pub) & (tr.timestamp_utc >= ctx.t)]
        day = day.dropna(subset=["price_eur_kwh"]).sort_values("timestamp_utc")
        if len(day) < MIN_HOURS:
            return
        hours = pd.DatetimeIndex(day.timestamp_utc)
        price = day.price_eur_kwh.to_numpy(dtype=float)
        fc = self._forecast(ctx, hours)
        now = self.q.loc[:ctx.t].battery_soc_kwh.dropna()
        if fc is None or now.empty or np.isnan(fc["net"]).any():
            return
        sp = self.spec
        soc_now = float(now.iloc[-1])
        # energy left in the battery at the end of the day is worth rte x the day's mean price (no free lunch)
        stored_value = sp.rte * float(price.mean())
        power, soc = plan_battery(fc["net"], price, soc_now, sp, terminal_value=stored_value)
        if np.abs(power).max() < 0.5:
            return
        plan_cost = float(_grid_cost((fc["net"] - power), price).sum())
        # baseline = observed controller behaviour (PV self-consumption: charge surplus, discharge deficit)
        base_grid, base_soc, _ = simulate(fc["net"], fc["net"], soc_now, sp, 1.0)
        base_soc_end = base_soc[-1]
        # value any SoC difference at the end of the day at the mean price (stored energy is not free)
        base_cost = float(_grid_cost(base_grid, price).sum()) + (soc[-1] - base_soc_end) * stored_value
        saving = base_cost - plan_cost

        ldate = local(hours[0]).strftime("%Y-%m-%d")
        lh = [local(x).strftime("%H") for x in hours]
        ch = [lh[i] for i in range(len(power)) if power[i] < -0.5]
        dis = [lh[i] for i in range(len(power)) if power[i] > 0.5]
        e_ch = float(-power[power < 0].sum())
        e_dis = float(power[power > 0].sum())
        imin, imax = int(price.argmin()), int(price.argmax())
        comfort_open = ctx.log.is_open(f"comfort:{HP}")
        evidence = [
            f"tariff.csv:BUILDING:published {iso(pub)}: day-ahead {ldate} ({len(day)} h) min {price[imin]:.4f} EUR/kWh "
            f"at {lh[imin]}h local, max {price[imax]:.4f} at {lh[imax]}h local, mean {price.mean():.4f}",
            f"energy.csv:{BAT}:SoC {soc_now:.1f} kWh at {iso(ctx.t)}; load/PV profiles "
            f"{iso(max(self.h.index[0], ctx.t - pd.Timedelta(days=PROFILE_DAYS)))}..{iso(ctx.t)}",
        ]
        if fc["issued"] is not None:
            evidence.append(f"weather_forecast.csv:BUILDING:issued {iso(fc['issued'])} (HVAC load from outdoor temperature fit)")
        reason = (
            f"Day-ahead battery plan for {ldate} (prices published {iso(pub)}): charge {e_ch:.0f} kWh in hours "
            f"{_hours(ch)} local (cheap / PV surplus), discharge {e_dis:.0f} kWh in hours {_hours(dis)} local (expensive). "
            f"Forecast net load {fc['net'].sum():.0f} kWh (load {fc['load'].sum():.0f}, PV {fc['pv'].sum():.0f}); "
            f"SoC kept within {sp.soc_min:.0f}-{sp.soc_max:.0f} kWh, |P| <= {max(sp.p_charge, sp.p_discharge):.0f} kW, "
            f"leftover energy valued at {stored_value:.3f} EUR/kWh; forecast deviations are absorbed by self-consumption. No setpoint changes: heat-pump pre-heating stays with the comfort agent"
            + (f" ({HP} comfort episode open - comfort first, no heating load shifted)." if comfort_open else ".")
        )
        d = ctx.log.add(make_decision(
            t=ctx.t, affected_assets=[BAT, PV], issue_type="energy_optimization", action="optimize_energy",
            priority="low", reason=reason, evidence=evidence, scheduled_for_utc=hours[0],
            expected_outcome=f"Lower grid import cost on {ldate} without touching room comfort",
            verification_plan=(f"Next day: {BAT} SoC stays within {sp.soc_min:.0f}-{sp.soc_max:.0f} kWh and grid import cost "
                               "vs. observed schedule is computed ex-post; if forecast error > 30 % or a comfort episode opens, "
                               "fall back to the default controller"),
            simulated_effect=(f"SIMULATED: {-saving:+.2f} EUR grid cost vs observed schedule (self-consumption controller on forecast "
                              f"load, export valued 0 EUR/kWh); estimate only, not a measured saving"),
            confidence=0.6, agent=self.name), episode_key=f"energy:{ldate}", prefix="EN")
        ctx.log.close(f"energy:{ldate}")
        self.plans.append(dict(event_id=d.event_id, date=ldate, hours=hours, power=power, soc=soc,
                               saving_est=saving, price=price, net=fc["net"]))

    # ---- ex-post evaluation (after the replay; never used for a decision) ---------------------------
    def evaluate_plan(self, plan: dict):
        hours = plan["hours"]
        q = self.q.loc[hours[0]:hours[-1] + pd.Timedelta(minutes=45)]
        if len(q) != 4 * len(hours) or q[["building_load_kw", "pv_kw", "grid_import_kw", "battery_soc_kwh"]].isna().any().any():
            return None
        price_q = np.repeat(plan["price"], 4)
        net_q = (q.building_load_kw - q.pv_kw).to_numpy()
        # execution: follow the plan, the battery absorbs forecast deviations (self-consumption on the error)
        power_q = np.repeat(plan["power"], 4) + (net_q - np.repeat(plan["net"], 4))
        soc0 = float(q.battery_soc_kwh.iloc[0])
        grid, soc, _ = simulate(power_q, net_q, soc0, self.spec, 0.25)
        plan_cost = float(_grid_cost(grid, price_q).sum())
        obs_cost = float((q.grid_import_kw * 0.25 * price_q).sum() - (q.grid_export_kw * 0.25 * FEED_IN).sum())
        last = q.iloc[-1]
        obs_soc_end = float(last.battery_soc_kwh) - float(last.battery_discharge_kw_signed) * 0.25 * (
            1 / self.spec.eta if last.battery_discharge_kw_signed > 0 else self.spec.eta)
        obs_soc_end = min(self.spec.soc_max, max(self.spec.soc_min, obs_soc_end))
        obs_cost += (soc[-1] - obs_soc_end) * self.spec.rte * float(np.mean(plan["price"]))
        return dict(saving=obs_cost - plan_cost, soc_min=float(soc.min()), soc_max=float(soc.max()))

    def finish(self, ctx):
        res = [r for r in (self.evaluate_plan(p) for p in self.plans) if r is not None]
        total = float(sum(r["saving"] for r in res))
        est = float(sum(p["saving_est"] for p in self.plans))
        self.summary = dict(plans=len(self.plans), evaluated_days=len(res), simulated_saving_eur=round(total, 2),
                            forecast_saving_eur=round(est, 2))
        if self.plans:
            print(f"[energy] {len(self.plans)} daily battery plans; SIMULATED saving vs observed schedule "
                  f"(ex-post replay of {len(res)} days): {total:.2f} EUR; forecast-time estimate {est:.2f} EUR. "
                  "Simulation only - not a measured or real saving.")


def _hours(hs: list[str]) -> str:
    return ",".join(hs) if hs else "none"

"""S03 Energy vs Comfort - heat-pump degradation, comfort protection, pre-heat, service.

Detection: expected-COP model (`plato40.models.heatpump`, refitted daily on history only) ->
load-weighted relative residual. The cold itself lowers COP (look-alike): only a persistent
shortfall *beyond* what the outdoor/supply temperature explains counts as degradation.
Lockout codes are noisy and only used as supporting evidence.

One building-level episode `comfort:HP1` per incident:
  1. degradation persists >= 3 h -> dispatch_technician (TECH_1 if available as known at t) or
     order_service hvac_contractor (approval). Comfort wins over price, stated in `reason`.
  2. forecast (as issued) shows colder hours ahead -> pre-heat occupied rooms within the comfort band
     in the cheapest *published* tariff hour before the cold (autonomous, simulated effect).
  3. many occupied rooms cold at once -> escalate to contractor once (if not yet ordered).
  4. performance back to expected for 12 h -> resolution note, episode closed.
Per-room follow-ups `comfort:<room>:<date>` only while the HP1 episode is open: occupied room
< 20 C for >= 3 h with window closed and valid data -> setpoint within band; temperature_cold
requests -> notify_guest heating_update (proposal, not a delivery).
Ruled out (no decision): open window, vacant/setback rooms, first hours after check-in, data gaps,
isolated lockouts, cold weather with a healthy COP.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..clock import iso, local_hm
from ..decision import make_decision
from ..messaging import compose_guest_message
from ..models.heatpump import causal_expected_cop, heatpump_frame, performance_residual
from .base import TriggerDetector, run_starts

HP = "HP1"
RESID_WINDOW = 8            # 2 h load-weighted residual
MIN_REL_DROP = 0.10         # >= 10 % less heat per kW than expected ...
SIGMA_K = 6.0               # ... and far outside the learned residual noise
DEGRADED_MIN = 12           # persists >= 3 h
RECOVERED_MIN = 48          # back to normal for 12 h -> close episode
STAY_WARMUP = pd.Timedelta(hours=2)   # rooms recover from 18 C setback after check-in
ROOM_COLD_MIN = 12          # per-room follow-up after 3 h cold while occupied
BUILDING_COLD_MIN = 4       # building comfort count uses rooms cold >= 1 h
MIN_COLD_ROOMS = 3
PREHEAT_DROP_C = 2.0        # forecast minimum this much colder than now -> pre-heat
PREHEAT_HORIZON = pd.Timedelta(hours=24)
PREHEAT_LEAD = pd.Timedelta(hours=12)
WORST_ROOMS = 5
TECH_MAX_WAIT = pd.Timedelta(hours=4)  # in-house tech only if on shift within 4 h, else contractor


def _grid(data):
    return pd.date_range(data.start, data.end, freq="15min", inclusive="left")


class ComfortAgent(TriggerDetector):
    name = "comfort"

    def __init__(self):
        self.ruled_out: list[dict] = []

    # ------------------------------------------------------------------------------------------------
    def build_triggers(self, data, state):
        band = data.building.get("constraints", {}).get("occupied_comfort_c", [20, 23])
        self.band = (float(band[0]), float(band[1]))
        grid = _grid(data)
        self.grid = grid

        # --- heat pump performance -------------------------------------------------------------------
        hp = heatpump_frame(data, HP)
        self.hp = hp
        self.weather = data.weather
        if not hp.empty and hp["normal"].any():
            expected, models = causal_expected_cop(hp)
            resid = performance_residual(hp, expected, window=RESID_WINDOW)
            sigma = pd.Series({k: m.resid_sigma for k, m in models.items()}, dtype=float)
            sigma_t = sigma.reindex(hp.index, method="ffill") if not sigma.empty else pd.Series(np.nan, index=hp.index)
            thr = -np.maximum(MIN_REL_DROP, SIGMA_K * sigma_t)
            self.models = models
        else:
            expected = resid = thr = pd.Series(np.nan, index=hp.index)
            self.models = {}
        self.expected, self.resid, self.thr = expected, resid, thr
        degraded = (resid < thr) & resid.notna()
        hits, _ = run_starts(degraded, DEGRADED_MIN)
        for ts in hits:
            yield ts, "hp_degraded", None
        healthy = (resid >= thr / 2) & resid.notna()
        rec, _ = run_starts(healthy, RECOVERED_MIN)
        for ts in rec:
            yield ts, "hp_recovered", None
        lock = hp["status"].astype(str).str.startswith("lockout") if "status" in hp else pd.Series(False, index=hp.index)
        self.lockouts = hp.index[lock.to_numpy()]

        # --- room comfort (occupied, window closed, valid data, past warm-up) ------------------------
        tel = data.telemetry
        piv = lambda col: tel.pivot_table(index="timestamp_utc", columns="room_id", values=col,  # noqa: E731
                                          aggfunc="first", dropna=False).reindex(grid)
        temp, window, setp = piv("temperature_c"), piv("window_open"), piv("setpoint_c")
        rooms = [r for r in data.room_ids if r in temp.columns]
        temp, window, setp = temp[rooms], window[rooms], setp[rooms]
        occ = np.zeros(temp.shape, dtype=bool)
        warm = np.zeros(temp.shape, dtype=bool)
        for j, r in enumerate(rooms):
            for s in state.stays.get(r, []):
                end = s.end if s.end is not None else data.end
                a, b = grid.searchsorted(s.start), grid.searchsorted(end)
                occ[a:b, j] = True
                w0 = grid.searchsorted(s.start + STAY_WARMUP)
                warm[max(a, w0):b, j] = True
        valid = temp.notna().to_numpy()
        cold = (temp.to_numpy() < self.band[0]) & valid & warm & (window.to_numpy() != 1)
        cold_df = pd.DataFrame(cold, index=grid, columns=rooms)
        self.temp, self.window, self.setp = temp, window, setp
        self.occ = pd.DataFrame(occ, index=grid, columns=rooms)
        streak1h = cold_df.rolling(BUILDING_COLD_MIN, min_periods=BUILDING_COLD_MIN).sum() == BUILDING_COLD_MIN
        n_cold = streak1h.sum(axis=1)
        self.n_cold = n_cold
        # learned building threshold: rooms cold at once beyond the usual (expanding q99 of previous days)
        daily_max = n_cold.resample("D").max()
        q = daily_max.expanding().quantile(0.99).shift(1)
        thr_c = np.maximum(MIN_COLD_ROOMS, np.ceil(q.fillna(MIN_COLD_ROOMS)) + 1)
        self.cold_thr = thr_c.reindex(grid, method="ffill").fillna(MIN_COLD_ROOMS)
        breach = n_cold >= self.cold_thr
        for ts in grid[(breach & ~breach.shift(1, fill_value=False)).to_numpy()]:
            yield ts, "comfort_breach", None
        for r in rooms:
            h, _ = run_starts(cold_df[r], ROOM_COLD_MIN)
            for ts in h:
                yield ts, "room_cold", r
        # ruled-out look-alike counters (no decisions)
        open_w = (temp.to_numpy() < self.band[0]) & occ & (window.to_numpy() == 1)
        self.ruled_out.append({"kind": "cold_with_window_open", "intervals": int(open_w.sum())})
        self.ruled_out.append({"kind": "cold_in_first_2h_of_stay", "intervals": int(
            ((temp.to_numpy() < self.band[0]) & occ & ~warm & valid).sum())})

        # --- forecasts + guest requests -------------------------------------------------------------
        for issued in data.forecast["issued_utc"].drop_duplicates():
            yield issued, "forecast", issued
        wo = data.work_orders
        for row in wo[wo.asset_id == HP].itertuples(index=False):
            yield row.created_utc, "work_order", row
        gr = data.guest_requests
        for row in gr[gr.request_type == "temperature_cold"].itertuples(index=False):
            yield row.timestamp_utc, "guest_cold", row

    # ------------------------------------------------------------------------------------------------
    def setup(self, data, state):
        self._service = {}       # episode key -> {"contractor": bool, "tech": bool}
        self._preheated: set = set()
        self._notified: set = set()
        super().setup(data, state)

    def _hp_snapshot(self, t):
        """Residual, expected vs actual COP over the last window - all data <= t."""
        hp = self.hp.loc[:t].tail(RESID_WINDOW)
        exp = self.expected.loc[:t].tail(RESID_WINDOW)
        ok = hp["normal"] & exp.notna()
        if not ok.any():
            return None
        act = float(hp.loc[ok, "heat_output_kw"].sum() / hp.loc[ok, "electric_kw"].sum())
        e = float((exp[ok] * hp.loc[ok, "electric_kw"]).sum() / hp.loc[ok, "electric_kw"].sum())
        out = float(hp.loc[ok, "outdoor_c"].mean())
        sup = float(hp.loc[ok, "supply_temp_c"].mean())
        res = float(self.resid.loc[:t].iloc[-1])
        thr = float(self.thr.loc[:t].iloc[-1])
        day = max((k for k in self.models if k <= t), default=None)
        model = self.models.get(day)
        lock24 = int(((self.lockouts > t - pd.Timedelta(hours=24)) & (self.lockouts <= t)).sum())
        return dict(actual=act, expected=e, out=out, sup=sup, resid=res, thr=thr, model=model, lock24=lock24,
                    a=hp.index[0], b=hp.index[-1])

    def _cold_rooms(self, t):
        """Occupied rooms currently below band (window closed, valid), coldest first."""
        row = self.temp.loc[:t].iloc[-1]
        occ = self.occ.loc[:t].iloc[-1]
        win = self.window.loc[:t].iloc[-1]
        m = (row < self.band[0]) & occ & (win != 1) & row.notna()
        return row[m].sort_values()

    def _price_note(self, ctx):
        tr = ctx.asof.tariff()
        if tr.empty:
            return "price unknown", None
        cur = tr[tr.timestamp_utc <= ctx.t]
        if cur.empty:
            return "price unknown", None
        p = float(cur.price_eur_kwh.iloc[-1])
        recent = cur[cur.timestamp_utc > ctx.t - pd.Timedelta(days=7)].price_eur_kwh
        high = p >= recent.quantile(0.75)
        note = (f"current price {p:.3f} EUR/kWh is HIGH (top quartile of last 7 days) - comfort wins over cost"
                if high else f"current price {p:.3f} EUR/kWh; comfort is protected regardless of price")
        return note, p

    # ------------------------------------------------------------------------------------------------
    def handle(self, ctx, ts, kind, payload):
        key = f"comfort:{HP}"
        if kind == "hp_degraded":
            if ctx.log.is_open(key):
                return
            self._open_episode(ctx, key)
        elif kind == "hp_recovered":
            if not ctx.log.is_open(key):
                return
            snap = self._hp_snapshot(ctx.t)
            res = f"{snap['resid']:+.0%}" if snap else "n/a"
            ctx.log.add(make_decision(
                t=ctx.t, affected_assets=[HP], issue_type="heating_comfort", action="flag_anomaly", priority="low",
                reason=f"{HP} delivers the expected heat per kW again for >= 12 h (residual {res} vs expected COP); "
                       "episode closed - confirm the service report / work order",
                evidence=[f"equipment.csv:{HP}:heat_output_kw/electric_kw {iso(ctx.t - pd.Timedelta(hours=12))}..{iso(ctx.t)}"],
                verification_plan="Residual stays within noise for the next cold days; any new shortfall opens a new episode",
                expected_outcome="Normal heating capacity; rooms back in 20-23 C band",
                confidence=0.7, agent=self.name), episode_key=key, prefix="CF")
            ctx.log.close(key)
            self._service.pop(key, None)
        elif kind == "work_order":
            if ctx.log.is_open(key):
                self._close_on_work_order(ctx, key, payload)
        elif kind == "comfort_breach":
            if ctx.log.is_open(key):
                self._escalate(ctx, key)
            else:
                n = int(self.n_cold.loc[:ctx.t].iloc[-1])
                self.ruled_out.append({"kind": "comfort_breach_without_hp_shortfall", "t": iso(ctx.t), "rooms": n})
        elif kind == "forecast":
            if ctx.log.is_open(key):
                self._preheat(ctx, key)
        elif kind == "room_cold":
            if ctx.log.is_open(key):
                self._room_followup(ctx, payload)
        elif kind == "guest_cold":
            if ctx.log.is_open(key):
                self._notify(ctx, payload)

    # ------------------------------------------------------------------------------------------------
    def _close_on_work_order(self, ctx, key, wo):
        snap = self._hp_snapshot(ctx.t)
        res = f"current residual {snap['resid']:+.0%} vs expected COP" if snap else "residual unknown (data gap)"
        ctx.log.add(make_decision(
            t=ctx.t, affected_assets=[HP], issue_type="heating_comfort", action="flag_anomaly", priority="low",
            reason=f"Service recorded for {HP} ({wo.work_order_id}: {wo.summary}); {res}. Episode closed - "
                   "monitor recovery, a renewed persistent shortfall opens a new episode",
            evidence=[f"work_orders.csv:{wo.work_order_id}:created {iso(wo.created_utc)}"],
            verification_plan="Residual back within noise within 24 h; occupied rooms back in 20-23 C band",
            expected_outcome="Heating capacity restored", confidence=0.75, agent=self.name), episode_key=key, prefix="CF")
        ctx.log.close(key)
        self._service.pop(key, None)

    def _service_choice(self, ctx):
        """Technician as known at t (on shift now or within TECH_MAX_WAIT), else contractor."""
        r = ctx.asof.roster()
        horizon = ctx.t + TECH_MAX_WAIT
        tech = r[(r.role == "technician") & (r.end_utc > ctx.t) & (r.start_utc <= horizon)]
        for row in tech.sort_values("start_utc").itertuples(index=False):
            when = max(ctx.t, row.start_utc)
            ok, why = ctx.state.available(row.resource_id, when, known_at=ctx.t)
            if ok:
                return row.resource_id, when, why
        absent = tech[tech.status != "scheduled"]
        why = "; ".join(f"{x.resource_id} {x.status} (known {iso(x.status_known_utc)})" for x in absent.itertuples()) \
            or f"no technician on shift within {TECH_MAX_WAIT.total_seconds() / 3600:.0f} h"
        return None, None, why

    def _open_episode(self, ctx, key):
        snap = self._hp_snapshot(ctx.t)
        if snap is None:
            return
        cold = self._cold_rooms(ctx.t)
        worst = list(cold.index[:WORST_ROOMS])
        price, _ = self._price_note(ctx)
        m = snap["model"]
        extra = ""
        if m is not None and m.extrapolating(snap["out"]):
            extra = (f" (outdoor {snap['out']:.1f} C is outside the fitted range "
                     f"{m.outdoor_range[0]:.1f}..{m.outdoor_range[1]:.1f} C - extrapolated, lower confidence)")
        lock = f"; {snap['lock24']} lockout status code(s) in last 24 h" if snap["lock24"] else ""
        reason = (f"{HP} delivers {abs(snap['resid']):.0%} less heat per kW than a healthy unit at this weather: "
                  f"COP {snap['actual']:.2f} vs expected {snap['expected']:.2f} at outdoor {snap['out']:.1f} C / supply "
                  f"{snap['sup']:.1f} C (residual {snap['resid']:+.0%}, threshold {snap['thr']:+.0%}, persistent >= 3 h){extra}"
                  f"{lock}. Cold weather alone is ruled out - the model already accounts for it. "
                  f"{len(cold)} occupied room(s) currently below {self.band[0]:.0f} C. {price}.")
        conf = 0.85 if not extra else 0.7
        conf = min(0.95, conf + (0.05 if snap["lock24"] else 0))
        ev = [f"equipment.csv:{HP}:heat_output_kw,electric_kw,supply_temp_c {iso(snap['a'])}..{iso(snap['b'])}",
              f"weather.csv:outdoor_c {iso(snap['a'])}..{iso(snap['b'])}",
              f"model:expected COP fitted on normal operation before {iso(max(k for k in self.models if k <= ctx.t))}"]
        if snap["lock24"]:
            ev.append(f"equipment.csv:{HP}:status lockout x{snap['lock24']} {iso(ctx.t - pd.Timedelta(hours=24))}..{iso(ctx.t)}")
        ev += [f"room_telemetry.csv:{r}:temperature_c {cold[r]:.1f} C at {iso(ctx.t)}" for r in worst]
        rid, when, why = self._service_choice(ctx)
        common = dict(t=ctx.t, issue_type="heating_comfort", priority="high",
                      evidence=ev, confidence=conf, agent=self.name,
                      verification_plan="Residual returns to within noise after service; occupied rooms back >= "
                                        f"{self.band[0]:.0f} C; if comfort breach grows before service -> escalate",
                      expected_outcome="Heat pump capacity restored before the coldest forecast hours")
        if rid:
            # technician works at the plant room: rooms stay in evidence, not in assets (no room entry)
            ctx.log.add(make_decision(action="dispatch_technician", affected_assets=[HP], resource_id=rid, scheduled_for_utc=when,
                                      reason=reason + f" Dispatch {rid} ({why}) to check refrigerant charge / pressures.",
                                      **common), episode_key=key, prefix="CF")
            self._service[key] = {"tech": True, "contractor": False}
        else:
            ctx.log.add(make_decision(action="order_service", affected_assets=[HP] + worst,
                                      service="hvac_contractor", notable_cost=True,
                                      reason=reason + f" In-house technician unavailable ({why}) -> order HVAC contractor.",
                                      **common), episode_key=key, prefix="CF")
            self._service[key] = {"tech": False, "contractor": True}
        self._preheat(ctx, key)
        recent = ctx.asof.guest_requests()
        recent = recent[(recent.request_type == "temperature_cold") & (recent.timestamp_utc > ctx.t - pd.Timedelta(hours=24))]
        for row in recent.itertuples(index=False):
            self._notify(ctx, row)

    def _escalate(self, ctx, key):
        svc = self._service.get(key, {})
        if svc.get("contractor"):
            return
        cold = self._cold_rooms(ctx.t)
        worst = list(cold.index[:WORST_ROOMS])
        n = int(self.n_cold.loc[:ctx.t].iloc[-1])
        price, _ = self._price_note(ctx)
        ctx.log.add(make_decision(
            t=ctx.t, affected_assets=[HP] + worst, issue_type="heating_comfort", action="order_service",
            service="hvac_contractor", notable_cost=True, priority="critical",
            reason=f"Comfort breach while {HP} is degraded: {n} occupied rooms below {self.band[0]:.0f} C for >= 1 h "
                   f"(usual maximum <= {int(self.cold_thr.loc[:ctx.t].iloc[-1]) - 1}). Escalate to HVAC contractor in addition "
                   f"to the in-house check. {price}.",
            evidence=[f"room_telemetry.csv:{r}:temperature_c {cold[r]:.1f} C at {iso(ctx.t)}" for r in worst],
            verification_plan="Number of cold occupied rooms falls below the usual level within 6 h of service",
            expected_outcome="Faster capacity restoration; guests protected", confidence=0.8, agent=self.name),
            episode_key=key, prefix="CF")
        svc["contractor"] = True
        self._service[key] = svc

    def setpoint_target(self) -> float:
        """Upper part of the comfort band (1 C headroom below the maximum), never outside it."""
        lo, hi = self.band
        return float(min(hi, max(lo, hi - 1.0)))

    def plan_preheat(self, ctx):
        """Pre-heat plan from the forecast *issued* and tariff *published* by ctx.t (None if not warranted)."""
        fc = ctx.asof.forecast()
        if fc.empty:
            return None
        ahead = fc[(fc.target_utc > ctx.t) & (fc.target_utc <= ctx.t + PREHEAT_HORIZON)]
        if ahead.empty:
            return None
        now_w = ctx.asof.window(self.weather, pd.Timedelta(hours=1))
        if now_w.empty or now_w.outdoor_c.isna().all():
            return None
        now_c = float(now_w.outdoor_c.dropna().iloc[-1])
        i = ahead.outdoor_c_forecast.idxmin()
        fmin, tmin = float(ahead.loc[i, "outdoor_c_forecast"]), ahead.loc[i, "target_utc"]
        if not fmin <= now_c - PREHEAT_DROP_C:
            return None
        tr = ctx.asof.tariff()
        cand = tr[(tr.timestamp_utc >= ctx.t.floor("h")) & (tr.timestamp_utc < tmin)
                  & (tr.timestamp_utc >= tmin - PREHEAT_LEAD)]
        if cand.empty:
            slot, price, pub = ctx.t, None, None
        else:
            j = cand.price_eur_kwh.idxmin()
            slot, price, pub = max(ctx.t, cand.loc[j, "timestamp_utc"]), float(cand.loc[j, "price_eur_kwh"]), cand.loc[j, "published_utc"]
        return dict(issued=fc.issued_utc.iloc[0], now_c=now_c, fmin=fmin, tmin=tmin, slot=slot, price=price,
                    published=pub, window_prices=cand.price_eur_kwh.describe() if not cand.empty else None)

    def _preheat(self, ctx, key):
        plan = self.plan_preheat(ctx)
        if plan is None or (key, plan["issued"]) in self._preheated:
            return
        occ = self.occ.loc[:ctx.t].iloc[-1]
        rooms = [r for r in occ.index[occ.to_numpy()] if ctx.state.occupied(r, ctx.t)]
        if not rooms:
            return
        target = self.setpoint_target()
        outside = not (self.band[0] <= target <= self.band[1])
        lead = (plan["tmin"] - ctx.t).total_seconds() / 3600
        ev = [f"weather_forecast.csv:issued {iso(plan['issued'])}: min {plan['fmin']:.1f} C forecast in {lead:.0f} h "
              f"(now {plan['now_c']:.1f} C measured at {iso(ctx.t)})"]
        if plan["price"] is not None:
            slot_h = (plan["slot"] - ctx.t).total_seconds() / 3600
            ev.append(f"tariff.csv:published {iso(plan['published'])}: cheapest hour in pre-heat window "
                      f"{plan['price']:.3f} EUR/kWh at +{slot_h:.0f} h (window max {plan['window_prices']['max']:.3f})")
            when = f"at {local_hm(plan['slot'])} local (cheapest published hour before the cold)"
        else:
            when = "now (no published price for the window yet)"
        ctx.log.add(make_decision(
            t=ctx.t, affected_assets=[HP] + rooms, issue_type="heating_comfort", action="adjust_room_setpoint",
            outside_comfort_band=outside, priority="medium", scheduled_for_utc=plan["slot"],
            reason=f"Pre-heat {len(rooms)} occupied rooms: setpoint target {target:.1f} C (inside the {self.band[0]:.0f}-{self.band[1]:.0f} C band) "
                   f"{when}: forecast drops to {plan['fmin']:.1f} C at {local_hm(plan['tmin'])} local while {HP} is degraded. "
                   "Comfort first - price only chooses the hour, never whether to heat.",
            evidence=ev,
            verification_plan="Occupied room temperatures stay >= 20 C through the forecast minimum; otherwise escalate service",
            expected_outcome="Thermal buffer in occupied rooms before the coldest hours",
            simulated_effect="SIMULATED: setpoint change in mock executor; no measured savings claimed",
            confidence=0.6, agent=self.name), episode_key=key, prefix="CF")
        self._preheated.add((key, plan["issued"]))

    def _room_followup(self, ctx, room):
        if not ctx.state.occupied(room, ctx.t):
            return
        rkey = f"comfort:{room}:{ctx.t.date()}"
        if ctx.log.is_open(rkey):
            return
        t_now = float(self.temp.loc[:ctx.t, room].iloc[-1])
        sp = self.setp.loc[:ctx.t, room].dropna()
        sp_now = float(sp.iloc[-1]) if not sp.empty else float("nan")
        target = self.setpoint_target()
        outside = not (self.band[0] <= target <= self.band[1])
        ctx.log.add(make_decision(
            t=ctx.t, affected_assets=[room, HP], issue_type="heating_comfort", action="adjust_room_setpoint",
            outside_comfort_band=outside, priority="medium",
            reason=f"{room} occupied and below {self.band[0]:.0f} C for >= 3 h ({t_now:.1f} C, window closed, setpoint "
                   f"{sp_now:.1f} C) while {HP} is degraded: setpoint target {target:.1f} C (within the comfort band)",
            evidence=[f"room_telemetry.csv:{room}:temperature_c,window_open {iso(ctx.t - pd.Timedelta(hours=3))}..{iso(ctx.t)}"],
            verification_plan=f"{room} >= {self.band[0]:.0f} C within 3 h; else offer room move / portable heater (approval)",
            expected_outcome="Room back in comfort band", confidence=0.7, agent=self.name),
            episode_key=rkey, prefix="CF")

    def _notify(self, ctx, req):
        if req.request_id in self._notified or not isinstance(req.room_id, str):
            return
        if not ctx.state.occupied(req.room_id, ctx.t):
            return
        self._notified.add(req.request_id)
        rkey = f"comfort:{req.room_id}:{ctx.t.date()}"
        msg = compose_guest_message("heating_update", req.language, room_id=req.room_id)
        ctx.log.add(make_decision(
            t=ctx.t, affected_assets=[req.room_id, HP], issue_type="heating_comfort", action="notify_guest",
            priority="high" if req.priority == "high" else "medium", guest_request_ids=[req.request_id],
            reason=f"Guest in {req.room_id} reported cold ({req.request_id}); known {HP} degradation is being serviced - "
                   "inform the guest (proposed message, delivery not confirmed)",
            evidence=[f"guest_requests.csv:{req.request_id}:{iso(req.timestamp_utc)}"], message=msg,
            verification_plan="Guest acknowledges; room temperature back in band; no repeat request within 12 h",
            expected_outcome="Guest informed before response_due", confidence=0.8, agent=self.name),
            episode_key=rkey, prefix="CF")

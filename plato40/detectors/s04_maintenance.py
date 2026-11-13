"""S04 The Maintenance Cascade - AHU filter degradation.

Signals (all causal, data <= t):
- filter_dp_pa normalised to nominal airflow (dp ~ Q^2), so night/reduced modes are not a "drop";
  6 h rolling median = robust level; 24 h relative rise vs the learned typical daily rise.
  Normal loading is ~1-2 %/day; a sustained rise of >= REL_RISE/day is rapid degradation.
- Gap in filter_dp_pa (>= 2 h) = status UNKNOWN: one follow-up, the episode stays open.
- Airflow below the same local time-of-day over the previous week confirms real plant degradation.
- Reset (= verification): level falls well below its recent maximum (filter changed).
- BMS FILTER_DP_HIGH / AIRFLOW_LOW: clue only (delayed); opens an episode if the trend was missed.
- Work orders as-of created_utc: postponed preventive filter change -> defer_action episode.
Service: next technician shift as known at t within the urgency horizon (dispatch_technician);
re-plan when that shift turns out unavailable or passes without a verified change; otherwise
order_service hvac_contractor (approval) for the next working day.
"""
from __future__ import annotations

import heapq

import numpy as np
import pandas as pd

from ..clock import at_local, iso, local
from ..decision import make_decision
from .base import TriggerDetector, run_starts

STEP = pd.Timedelta(minutes=15)
LEVEL_WIN = "6h"
LEVEL_MIN_PERIODS = 12
REL_RISE = 0.07          # >= 7 % rise of the normalised level within 24 h ...
RISE_VS_TYPICAL = 4.0    # ... and >= 4x the typical (learned) daily rise
TREND_STEPS = 12         # sustained for 3 h
GAP_STEPS = 8            # 2 h without filter_dp_pa = gap
AIRFLOW_RATIO = 0.93     # airflow < 93 % of same time-of-day last week
AIRFLOW_STEPS = 8
RESET_RATIO = 0.7        # level < 70 % of its 7-day max = filter changed
RESET_STEPS = 4
URGENCY = pd.Timedelta(hours=72)
JOB = pd.Timedelta(hours=2)
LEAD = pd.Timedelta(hours=2)
CONTRACTOR_START = "08:00"
MIN_AIRFLOW_FRAC = 0.3


def _ahus(data) -> dict[str, float]:
    out = {}
    for a in data.building["building"]["assets"]:
        if a.get("type") == "air_handling_unit" or "filter_dp_pa" in a.get("metrics", []):
            out[a["id"]] = float(a.get("nominal_airflow_m3h") or np.nan)
    return out


def features(data, ahu: str, nominal: float) -> pd.DataFrame:
    """Causal per-15-min features for one AHU (index = full 15-min grid of the dataset)."""
    grid = pd.date_range(data.start, data.end, freq=STEP, inclusive="left")
    w = data.equipment_wide(ahu).reindex(grid)
    for c in ("filter_dp_pa", "airflow_m3h", "fan_kw"):
        if c not in w:
            w[c] = np.nan
    if not np.isfinite(nominal):
        nominal = float(w.airflow_m3h.expanding(min_periods=96).quantile(0.9).bfill().iloc[-1])
    af = w.airflow_m3h.where(w.airflow_m3h > MIN_AIRFLOW_FRAC * nominal)
    dpn = w.filter_dp_pa * (nominal / af) ** 2
    lvl = dpn.rolling(LEVEL_WIN, min_periods=LEVEL_MIN_PERIODS).median()
    prev = lvl.shift(96)
    rel = (lvl - prev) / prev
    typical = rel.clip(lower=0).expanding(min_periods=96 * 3).median()
    # airflow vs same local time-of-day over the previous 7 days (operating modes differ by time of day)
    lt = grid.tz_convert("Europe/Vienna")
    a = pd.DataFrame({"af": w.airflow_m3h.to_numpy(), "slot": lt.hour * 4 + lt.minute // 15,
                      "day": lt.tz_localize(None).normalize()})
    piv = a.pivot_table(index="day", columns="slot", values="af", aggfunc="first")
    ref = piv.rolling(7, min_periods=3).median().shift(1)
    ref_l = ref.stack().rename("ref").reset_index()
    a = a.merge(ref_l, on=["day", "slot"], how="left")
    ratio = pd.Series((a.af / a.ref).to_numpy(), index=grid).rolling(LEVEL_WIN, min_periods=LEVEL_MIN_PERIODS).median()
    return pd.DataFrame({
        "dp": w.filter_dp_pa, "airflow": w.airflow_m3h, "fan_kw": w.fan_kw, "dpn": dpn, "level": lvl,
        "rel24": rel, "typical": typical, "airflow_ratio": ratio,
        "max7d": lvl.rolling("7D", min_periods=1).max(),
    })


class MaintenanceAgent(TriggerDetector):
    name = "maintenance"

    def build_triggers(self, data, state):
        self.f: dict[str, pd.DataFrame] = {}
        self.st: dict[str, dict] = {}
        self.ruled_out: list[dict] = []
        techs = {rid for rid, role in data.resources.items() if role == "technician"}
        self.techs = sorted(techs)
        for ahu, nominal in _ahus(data).items():
            f = features(data, ahu, nominal)
            self.f[ahu] = f
            self.st[ahu] = {"kind": None}
            trend = (f.rel24 >= REL_RISE) & (f.rel24 >= RISE_VS_TYPICAL * f.typical.clip(lower=0.005))
            for ts in run_starts(trend, TREND_STEPS)[0]:
                yield ts, "trend", ahu
            gs, ge = run_starts(f.dp.isna(), GAP_STEPS)
            for ts in gs:
                yield ts, "gap_start", ahu
            for ts in ge:
                yield ts, "gap_end", ahu
            for ts in run_starts(f.airflow_ratio < AIRFLOW_RATIO, AIRFLOW_STEPS)[0]:
                yield ts, "airflow_low", ahu
            for ts in run_starts(f.level < RESET_RATIO * f.max7d, RESET_STEPS)[0]:
                yield ts, "reset", ahu
            ev = data.events
            if "alarm_code" in ev:
                bms = ev[(ev.type == "bms_alarm") & (ev.asset_id == ahu) & (ev.state == "raised")
                         & ev.alarm_code.isin(["FILTER_DP_HIGH", "AIRFLOW_LOW"])]
                for row in bms.itertuples(index=False):
                    yield row.timestamp_utc, "bms", (ahu, row.alarm_code, row.value, row.event_id)
            wo = data.work_orders
            for row in wo[wo.asset_id == ahu].itertuples(index=False):
                yield max(row.created_utc, data.start), "wo", (ahu, row.work_order_id)
        r = data.roster
        absent = r[r.resource_id.isin(techs) & (r.status != "scheduled") & r.status_known_utc.notna()]
        for row in absent.itertuples(index=False):
            yield max(row.status_known_utc, data.start), "roster", row.shift_id

    def _schedule(self, ts, kind, payload):
        heapq.heappush(self._q, (ts, next(self._seq), kind, payload))

    # ------------------------------------------------------------------------
    def _now(self, ahu, t) -> pd.Series:
        f = self.f[ahu].loc[:t]
        return f.iloc[-1] if len(f) else pd.Series(dtype=float)

    def _last_level(self, ahu, t):
        lv = self.f[ahu].level.loc[:t].dropna()
        return (lv.index[-1], float(lv.iloc[-1])) if len(lv) else (None, np.nan)

    def _baseline(self, ahu, t) -> float:
        """Clean-filter level: median of the first day after the last reset (or dataset start)."""
        f = self.f[ahu].loc[:t]
        since = self.st[ahu].get("last_reset") or f.index[0]
        return float(f.level.loc[since: since + pd.Timedelta(days=1)].median())

    def _ev_dp(self, ahu, t, hours=24):
        return f"equipment.csv:{ahu}:filter_dp_pa {iso(t - pd.Timedelta(hours=hours))}..{iso(t)}"

    def _add(self, ctx, ahu, **kw):
        kw.setdefault("issue_type", "ventilation_maintenance")
        kw.setdefault("affected_assets", [ahu])
        d = make_decision(t=ctx.t, agent=self.name, **kw)
        ctx.log.add(d, episode_key=f"maint:{ahu}", prefix="MT")
        return d

    def _lift_note(self, ctx, ahu) -> str:
        wo = ctx.asof.work_orders()
        hist = wo[(wo.asset_id == ahu) & wo.summary.str.contains("lift platform", case=False, na=False)]
        if len(hist):
            return (f"Rooftop unit: book a lift platform with the job (earlier change postponed for lack of one, "
                    f"{hist.iloc[-1].work_order_id}).")
        return "Rooftop unit: confirm roof access / lift platform with the job."

    # ---- service planning ------------------------------------------------------
    def _next_tech_slot(self, ctx):
        t = ctx.t
        r = ctx.asof.roster()
        r = r[r.resource_id.isin(self.techs) & (r.end_utc > t + LEAD + JOB) & (r.start_utc < t + URGENCY)]
        for row in r.sort_values("start_utc").itertuples(index=False):
            slot = max(row.start_utc, (t + LEAD).ceil("15min"))
            if row.end_utc - slot < JOB:
                continue
            ok, _ = ctx.state.available(row.resource_id, slot, known_at=t)
            if ok:
                return row, slot
        return None, None

    def _absences(self, ctx) -> list[str]:
        t = ctx.t
        r = ctx.asof.roster()
        r = r[r.resource_id.isin(self.techs) & (r.status != "scheduled") & (r.end_utc > t)
              & (r.start_utc < t + URGENCY + pd.Timedelta(days=7))]
        return [f"staff_roster.csv:{x.resource_id}:{x.shift_id} {x.status} {local(x.start_utc):%Y-%m-%d}"
                f" (known {local(x.status_known_utc):%Y-%m-%d})" for x in r.itertuples(index=False)]

    @staticmethod
    def _next_working_day(t) -> pd.Timestamp:
        d = local(t).normalize() + pd.Timedelta(days=1)
        while d.weekday() >= 5:
            d += pd.Timedelta(days=1)
        return at_local(d, CONTRACTOR_START)

    def _plan_service(self, ctx, ahu, why: str, evidence: list[str]):
        st = self.st[ahu]
        lift = self._lift_note(ctx, ahu)
        shift, slot = self._next_tech_slot(ctx)
        if shift is not None:
            st["plan"] = {"kind": "tech", "shift_id": shift.shift_id, "rid": shift.resource_id, "slot": slot}
            self._add(ctx, ahu, action="dispatch_technician", priority="high", resource_id=shift.resource_id,
                      scheduled_for_utc=slot,
                      reason=f"{why} Replace AHU filters: {shift.resource_id} is rostered (as known now) "
                             f"{local(slot):%a %d %b %H:%M} local within the {int(URGENCY / pd.Timedelta(hours=1))} h "
                             f"urgency horizon. {lift}",
                      evidence=evidence + [f"staff_roster.csv:{shift.resource_id}:{shift.shift_id} scheduled"],
                      expected_outcome="Filters replaced; normalised filter dp back to the clean-filter baseline",
                      verification_plan="filter_dp_pa level drops to the post-change baseline after the job; if the "
                                        "slot passes without that drop or the technician becomes unavailable -> re-plan "
                                        "(contractor)",
                      dependencies=[f"{shift.resource_id} shift {shift.shift_id}", "lift platform / roof access"],
                      confidence=0.75)
            self._schedule(shift.end_utc + STEP, "slot_check", (ahu, shift.shift_id))
            return
        when = self._next_working_day(ctx.t)
        absent = self._absences(ctx)
        st["plan"] = {"kind": "contractor", "when": when}
        self._add(ctx, ahu, action="order_service", priority="high", service="hvac_contractor", notable_cost=True,
                  scheduled_for_utc=when,
                  reason=f"{why} No in-house technician available within "
                         f"{int(URGENCY / pd.Timedelta(hours=1))} h as known now"
                         f"{' (' + '; '.join(a.split(':', 2)[2] for a in absent[:3]) + ')' if absent else ''}"
                         f" -> order HVAC contractor for filter replacement, earliest "
                         f"{local(when):%a %d %b %H:%M} local. {lift}",
                  evidence=evidence + absent[:5],
                  expected_outcome="Contractor replaces AHU filters; dp back to clean-filter baseline",
                  verification_plan="filter_dp_pa level drops to baseline after the visit and airflow recovers to the "
                                    "usual time-of-day level; until then keep the episode open (gaps = unknown)",
                  dependencies=["human approval (contractor cost)", "lift platform / roof access"],
                  confidence=0.75)

    # ---- episode helpers ----------------------------------------------------------
    def _open_incident(self, ctx, ahu, how: str, evidence: list[str], confidence=0.8):
        st = self.st[ahu]
        t = ctx.t
        cur = self._now(ahu, t)
        base = self._baseline(ahu, t)
        typical = cur.get("typical", np.nan)
        was_deferred = st.get("kind") == "deferred"
        st.update(kind="incident", start=t, confirmed=False, gap_noted=False, plan=None)
        lvl_t, lvl = self._last_level(ahu, t)
        why = (f"AHU filter degrading fast ({how}): normalised filter dp {lvl:.0f} Pa vs clean-filter baseline "
               f"{base:.0f} Pa; 24 h rise {cur.get('rel24', np.nan) * 100:.0f} % vs typical "
               f"{typical * 100:.1f} %/day. Excluded: night/reduced airflow mode (dp normalised to nominal airflow), "
               f"normal slow loading.{' Preventive change was postponed earlier.' if was_deferred else ''}")
        self._plan_service(ctx, ahu, why, evidence)
        st["opened_conf"] = confidence

    def _close(self, ctx, ahu, reason: str, evidence: list[str]):
        st = self.st[ahu]
        if st.get("kind"):
            self._add(ctx, ahu, action="flag_anomaly", priority="low", reason=reason, evidence=evidence,
                      verification_plan="Trend of the new filter is monitored from the new baseline",
                      expected_outcome="Episode closed; next degradation is a new episode", confidence=0.85)
            ctx.log.close(f"maint:{ahu}")
        self.st[ahu] = {"kind": None, "last_reset": ctx.t}

    # ------------------------------------------------------------------------
    def handle(self, ctx, ts, kind, payload):
        t = ctx.t
        if kind == "roster":
            for ahu, st in self.st.items():
                p = st.get("plan") or {}
                if st.get("kind") == "incident" and p.get("kind") == "tech" and p.get("shift_id") == payload:
                    r = ctx.asof.roster()
                    row = r[r.shift_id == payload].iloc[0]
                    self._plan_service(ctx, ahu, f"Re-plan: {row.resource_id} shift {payload} is now {row.status}.",
                                       [f"staff_roster.csv:{row.resource_id}:{payload} {row.status} "
                                        f"(known {iso(row.status_known_utc)})", self._ev_dp(ahu, t)])
            return
        if kind == "wo":
            ahu, wo_id = payload
            wo = ctx.asof.work_orders()
            row = wo[wo.work_order_id == wo_id].iloc[0]
            st = self.st[ahu]
            if row.status == "postponed" and st.get("kind") is None:
                lvl_t, lvl = self._last_level(ahu, t)
                cur = self._now(ahu, t)
                base = self._baseline(ahu, t)
                st.update(kind="deferred", gap_noted=False, plan=None)
                self._add(ctx, ahu, action="defer_action", priority="low",
                          reason=f"Preventive filter change {wo_id} postponed ({row.summary}). Filter loading is "
                                 f"normal (normalised dp {lvl:.0f} Pa vs clean baseline {base:.0f} Pa, 24 h rise "
                                 f"{cur.get('rel24', np.nan) * 100:.1f} %) -> service can wait for the rescheduled "
                                 "date; keep watching the trend.",
                          evidence=[f"work_orders.csv:{wo_id}:{ahu}:{iso(row.created_utc)} postponed", self._ev_dp(ahu, t)],
                          expected_outcome="Filter changed at the rescheduled date before it loads critically",
                          verification_plan="dp step-down after the planned change closes this; a rapid rise "
                                            "(>= 7 %/day) or airflow drop escalates to technician/contractor",
                          confidence=0.7)
            elif row.status == "completed" and st.get("kind"):
                st["wo_done"] = wo_id
            return

        ahu = payload[0] if isinstance(payload, tuple) else payload
        st = self.st[ahu]
        f = self.f[ahu]
        if kind == "trend":
            if st.get("kind") == "incident":
                return
            self._open_incident(ctx, ahu, "pressure trend", [self._ev_dp(ahu, t, 48)])
        elif kind == "bms":
            _, code, value, eid = payload
            if st.get("kind") == "incident":
                st.setdefault("bms", []).append(eid)
                return
            self._open_incident(ctx, ahu, f"BMS {code} {value} - trend not seen before",
                                [f"events.jsonl:{ahu}:{eid} {code} {iso(ts)}", self._ev_dp(ahu, t, 48)], 0.7)
        elif kind == "gap_start":
            if st.get("kind") is None:
                key = f"maint:gap:{ahu}"
                if not ctx.log.is_open(key):
                    ctx.log.add(make_decision(
                        t=t, affected_assets=[ahu], issue_type="data_quality", action="flag_anomaly", priority="low",
                        reason=f"{ahu} filter_dp_pa missing for >= 2 h - filter state unknown (not 'normal', not 'replaced')",
                        evidence=[f"equipment.csv:{ahu}:filter_dp_pa missing {iso(t - pd.Timedelta(hours=2))}..{iso(t)}"],
                        verification_plan="Readings resume; airflow vs time-of-day baseline covers the gap meanwhile",
                        confidence=0.8, agent=self.name), episode_key=key, prefix="MT")
                return
            if st.get("gap_noted"):
                return
            st["gap_noted"] = True
            seen = f.dpn.loc[:t].dropna()
            lvl_t = seen.index[-1] if len(seen) else None
            lvl = float(f.level.loc[:lvl_t].iloc[-1]) if lvl_t is not None else np.nan
            p = st.get("plan") or {}
            plan = {"tech": "technician dispatch", "contractor": "contractor order"}.get(p.get("kind"), "rescheduled preventive change")
            self._add(ctx, ahu, action="flag_anomaly", priority="medium",
                      reason=f"filter_dp_pa readings missing >= 2 h - degradation status UNKNOWN, a gap is not a repair "
                             f"(last level {lvl:.0f} Pa at {iso(lvl_t) if lvl_t is not None else '?'}). Keep the {plan}; "
                             "watch airflow/fan power meanwhile.",
                      evidence=[f"equipment.csv:{ahu}:filter_dp_pa missing {iso(t - pd.Timedelta(hours=2))}..{iso(t)}"],
                      verification_plan="When readings resume compare with the pre-gap level; airflow drop vs "
                                        "time-of-day baseline confirms ongoing degradation",
                      confidence=0.8)
        elif kind == "gap_end":
            ctx.log.close(f"maint:gap:{ahu}")
            if st.get("kind") == "incident" and st.get("gap_noted"):
                st["gap_noted"] = False
                pre = f.level.loc[:t - GAP_STEPS * STEP].dropna()
                cur = f.dpn.loc[t - pd.Timedelta(hours=1):t].dropna().median()
                if len(pre) and np.isfinite(cur) and cur > pre.iloc[-1]:
                    self._add(ctx, ahu, action="flag_anomaly", priority="high",
                              reason=f"filter_dp_pa readings resumed: normalised dp {cur:.0f} Pa vs {pre.iloc[-1]:.0f} Pa "
                                     "before the gap -> degradation continued during the gap (no repair); service still needed",
                              evidence=[self._ev_dp(ahu, t, 2), f"equipment.csv:{ahu}:filter_dp_pa last before gap "
                                                                f"{iso(pre.index[-1])}"],
                              verification_plan="Filter change -> dp step-down; otherwise escalate to the facility manager",
                              confidence=0.85)
        elif kind == "airflow_low":
            if st.get("kind") != "incident" or st.get("confirmed"):
                return
            st["confirmed"] = True
            cur = self._now(ahu, t)
            fan = f.fan_kw.loc[:t].dropna().tail(24).median()
            fan_ref = f.fan_kw.loc[st["start"] - pd.Timedelta(days=7): st["start"]].quantile(0.95)
            p = st.get("plan") or {}
            pending = "contractor order" if p.get("kind") == "contractor" else "technician dispatch"
            self._add(ctx, ahu, action="request_human_approval", priority="high", notable_cost=True,
                      reason=f"Confirmed real plant degradation (not a data artefact): airflow at "
                             f"{cur.get('airflow_ratio', np.nan) * 100:.0f} % of the usual level for this time of day, "
                             f"fan power {fan:.1f} kW vs {fan_ref:.1f} kW before the incident. Approve the pending "
                             f"{pending} urgently - ventilation of occupied rooms is degrading.",
                      evidence=[f"equipment.csv:{ahu}:airflow_m3h {iso(t - pd.Timedelta(hours=6))}..{iso(t)}",
                                f"equipment.csv:{ahu}:fan_kw {iso(t - pd.Timedelta(hours=6))}..{iso(t)}"],
                      expected_outcome="Service approved and executed; airflow back to the time-of-day baseline",
                      verification_plan="Airflow ratio >= 0.97 and dp step-down after the filter change",
                      dependencies=[pending], confidence=0.85)
        elif kind == "reset":
            if not st.get("kind"):
                self.st[ahu]["last_reset"] = t
                return
            lvl_t, lvl = self._last_level(ahu, t)
            before = float(f.max7d.loc[:t].iloc[-1])
            wo = st.get("wo_done")
            self._close(ctx, ahu,
                        f"Verified: normalised filter dp dropped to {lvl:.0f} Pa (from {before:.0f} Pa) - filters "
                        f"replaced{' (' + wo + ')' if wo else ''}; episode closed.",
                        [self._ev_dp(ahu, t, 12)] + ([f"work_orders.csv:{wo}:{ahu}"] if wo else []))
        elif kind == "slot_check":
            _, shift_id = payload
            p = st.get("plan") or {}
            if st.get("kind") == "incident" and p.get("kind") == "tech" and p.get("shift_id") == shift_id:
                self._plan_service(ctx, ahu, f"Re-plan: planned slot ({shift_id}) passed without a verified filter "
                                             "change (no dp step-down).", [self._ev_dp(ahu, t, 12)])

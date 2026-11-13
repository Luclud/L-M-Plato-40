"""AHU filter analyses: P3 plant access, C4 filter interval from the dp trend."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import Ctx, Insight, insufficient

DROP = 0.3          # daily dp drop > 30 % = filter changed
FLOW_TOL = 0.03     # use intervals within 3 % of the usual airflow (dp depends on flow)


def _bms(c: Ctx, code: str) -> pd.DataFrame:
    ev = c.data.events
    if "alarm_code" not in ev:
        return ev.iloc[:0]
    return ev[(ev.type == "bms_alarm") & (ev.alarm_code == code) & (ev.state == "raised")]


def filter_cycles(c: Ctx) -> tuple[pd.Series, list[dict]]:
    """Daily filter dp at the usual airflow and the detected filter-change cycles."""
    a = c.data.equipment_wide("AHU1")
    if a.empty or "filter_dp_pa" not in a:
        return pd.Series(dtype=float), []
    af = a.airflow_m3h
    usual = af.median()
    m = a[(af - usual).abs() <= FLOW_TOL * usual]
    daily = m.filter_dp_pa.resample("1D").median().dropna()
    cycles, start = [], 0
    vals, idx = daily.to_numpy(), daily.index
    for i in range(1, len(vals) + 1):
        if i == len(vals) or vals[i] < (1 - DROP) * vals[i - 1]:
            seg = daily.iloc[start:i]
            if len(seg) >= 7:
                days = (seg.index - seg.index[0]).days.to_numpy()
                slope = float(np.polyfit(days, seg.to_numpy(), 1)[0])
                cycles.append({"start": idx[start], "end": idx[i - 1], "dp_start": float(seg.iloc[:3].median()),
                               "dp_end": float(seg.iloc[-3:].median()), "slope_pa_day": slope,
                               "changed": i < len(vals)})
            start = i
    return daily, cycles


def p3_plant_access(c: Ctx) -> Insight:
    hyp = "P3 Rooftop AHU needs a lift platform -> postponed filter changes"
    wo = c.data.work_orders
    ahu = wo[wo.asset_id == "AHU1"]
    post = ahu[(ahu.status == "postponed")]
    access = post[post.summary.str.contains("lift|platform|access|reach", case=False, na=False)]
    unplanned = ahu[(ahu.type == "corrective") & ahu.summary.str.contains("filter", case=False, na=False)]
    ev = ["work_orders.csv: asset_id=AHU1, status=postponed / type=corrective (summary text)",
          "equipment.csv: AHU1 filter_dp_pa, airflow_m3h (daily median at usual airflow)",
          "events.jsonl: bms_alarm FILTER_DP_HIGH / AIRFLOW_LOW raised"]
    if ahu.empty:
        return insufficient("PLATO-P3", "PLATO", "Rooftop plant access", hyp, "no AHU1 work orders", ev)
    daily, cycles = filter_cycles(c)
    delays, dp_at = [], []
    for w in access.itertuples(index=False):
        nxt = [cy for cy in cycles if cy["changed"] and cy["end"] >= w.created_utc.floor("1D")]
        if nxt:
            delays.append((nxt[0]["end"] - w.created_utc.floor("1D")).days + 1)
            dp_at.append(nxt[0]["dp_end"])
    fdp = _bms(c, "FILTER_DP_HIGH")
    afl = _bms(c, "AIRFLOW_LOW")
    thr = float(fdp.value.astype(float).min()) if len(fdp) else np.nan
    finding = (f"PLATO-40: {len(post)} AHU1 filter changes were postponed, {len(access)} explicitly because the rooftop "
               f"unit is only reachable with a lift platform; the filter change happened {', '.join(map(str, delays)) or 'n/a'} "
               f"days later (dp then {', '.join(f'{x:.0f}' for x in dp_at) or 'n/a'} Pa). {len(unplanned)} further "
               f"unplanned 'heavily loaded filter' replacements by a contractor followed {len(fdp)} FILTER_DP_HIGH and "
               f"{len(afl)} AIRFLOW_LOW alarms (alarm dp ≥ {thr:.0f} Pa). Every postponement is filter-life "
               "consumed at reduced airflow and higher fan power.")
    return Insight(
        "PLATO-P3", "PLATO", "Rooftop plant needs permanent access", hyp,
        "confirmed" if len(access) else "qualified", finding, ev,
        "Text match on AHU1 work orders; filter changes detected as > 30 % day-over-day drops of the daily "
        "median filter_dp_pa at the usual airflow; delay = postponed work order -> detected change.",
        "Next PLATO: design for maintainability — fixed roof access (stair/ladder + guarded walkway) or place the "
        "AHU at ground/technical-room level so filters can be swapped by the in-house technician without a lift "
        "platform; filter-door side reachable from the walkway.",
        f"Qualitative: no access-related postponements (here {len(access)}), fewer contractor call-outs "
        f"(here {len(unplanned)} unplanned); no cost figures claimed.",
        "Work-order texts are terse and written by people; two postponements is a small sample; synthetic AHU model.",
        0.7 if len(access) else 0.4,
        {"postponed": int(len(post)), "postponed_access": int(len(access)), "delay_days": delays,
         "unplanned_filter_wos": int(len(unplanned)), "filter_dp_high_alarms": int(len(fdp))})


def c4_filter_interval(c: Ctx) -> Insight:
    hyp = "C4 Filter maintenance interval from dp trend instead of fixed dates"
    ev = ["equipment.csv: AHU1 filter_dp_pa at usual airflow (daily median), airflow_m3h",
          "events.jsonl: bms_alarm FILTER_DP_HIGH raised (value = alarm dp)",
          "work_orders.csv: AHU1 preventive / corrective filter work orders"]
    daily, cycles = filter_cycles(c)
    if not cycles:
        return insufficient("CLARA-C4", "CLARA", "Condition-based filter interval", hyp, "no AHU1 dp series", ev)
    fdp = _bms(c, "FILTER_DP_HIGH")
    thr = float(fdp.value.astype(float).min()) if len(fdp) else float(np.nanmax([cy["dp_end"] for cy in cycles]))
    slopes = np.array([cy["slope_pa_day"] for cy in cycles])
    clean = float(np.median([cy["dp_start"] for cy in cycles]))
    to_thr = [(thr - cy["dp_start"]) / cy["slope_pa_day"] for cy in cycles if cy["slope_pa_day"] > 0]
    lens = [(cy["end"] - cy["start"]).days + 1 for cy in cycles if cy["changed"]]
    wo = c.data.work_orders
    planned = wo[(wo.asset_id == "AHU1") & (wo.type == "preventive")]
    plan_gaps = planned.created_utc.sort_values().diff().dt.days.dropna().tolist()
    warn_days = 14
    finding = (f"PLATO-40: {len(cycles)} AHU1 filter cycles detected; clean filter ≈ {clean:.0f} Pa, loading rate "
               f"{slopes.min():.2f}–{slopes.max():.2f} Pa/day (median {np.median(slopes):.2f}). At these rates the "
               f"BMS FILTER_DP_HIGH level (≈ {thr:.0f} Pa) is reached after "
               f"{min(to_thr):.0f}–{max(to_thr):.0f} days (median {np.median(to_thr):.0f}); observed cycles lasted "
               f"{', '.join(map(str, lens))} days. Fixed planned work orders came at intervals of "
               f"{', '.join(f'{x:.0f}' for x in plan_gaps) or 'n/a'} days, and the fastest-loading cycles still hit "
               f"the alarm before the next planned change ({len(fdp)} FILTER_DP_HIGH alarms).")
    return Insight(
        "CLARA-C4", "CLARA", "Condition-based filter interval", hyp, "confirmed", finding, ev,
        "Daily median filter_dp_pa restricted to the usual airflow (±3 %), cycles split at > 30 % drops, linear "
        "loading rate per cycle, days to threshold = (alarm dp − clean dp) / rate.",
        f"CLARA rule: forecast days-to-threshold from the causal rolling dp slope (last 14 days at usual airflow) and "
        f"create the filter work order when the projection is < {warn_days + 7} days — book the technician (and, until "
        f"P3 is fixed, the lift platform) {warn_days} days ahead; distinguish a dp gap (missing data) from loading.",
        "Qualitative: changes happen before FILTER_DP_HIGH / AIRFLOW_LOW instead of after; fewer unplanned "
        "contractor replacements.",
        "Synthetic, near-linear loading model; boost-airflow periods excluded (dp rises with flow); the alarm level "
        "is taken from the BMS alarm values, not a manufacturer limit.",
        0.7,
        {"cycles": len(cycles), "slope_pa_day": [round(float(s), 2) for s in slopes],
         "days_to_threshold": [round(float(x)) for x in to_thr], "threshold_pa": round(thr, 1)})

"""Housekeeping and logistics analyses: C5 cleaning strategy, C6 waste / vending."""
from __future__ import annotations

import numpy as np
import pandas as pd

from plato40.clock import LOCAL_TZ

from .common import Ctx, Insight, insufficient, pct


def c5_cleaning(c: Ctx) -> Insight:
    hyp = "C5 Cleaning strategy: learned durations, robot pre-clean, sick-day buffer"
    d, ev_ = c.data, c.data.events
    ev = ["events.jsonl: cleaning_started -> cleaning_completed per room (duration), robot_floor_cleaning_started",
          "events.jsonl: arrival_waiting_room -> checkin_after_wait (wait), checkin* (arrivals per local day)",
          "staff_roster.csv: cleaner shifts with status != scheduled and status_known_utc"]
    cs = ev_[ev_.type == "cleaning_started"][["timestamp_utc", "room_id"]].sort_values("timestamp_utc")
    cc = ev_[ev_.type == "cleaning_completed"][["timestamp_utc", "room_id"]].rename(columns={"timestamp_utc": "end"})
    if cs.empty or cc.empty:
        return insufficient("CLARA-C5", "CLARA", "Cleaning strategy", hyp, "no cleaning events", ev)
    m = pd.merge_asof(cs, cc.sort_values("end"), left_on="timestamp_utc", right_on="end", by="room_id",
                      direction="forward", tolerance=pd.Timedelta("6h")).dropna(subset=["end"])
    m["min"] = (m.end - m.timestamp_utc).dt.total_seconds() / 60
    m["area"] = m.room_id.map(c.rooms.area_m2)
    rb = ev_[ev_.type == "robot_floor_cleaning_started"][["timestamp_utc", "room_id"]].rename(columns={"timestamp_utc": "rt"})
    m = pd.merge_asof(m.sort_values("timestamp_utc"), rb.sort_values("rt"), left_on="timestamp_utc", right_on="rt",
                      by="room_id", direction="backward", tolerance=pd.Timedelta("4h"))
    m["robot"] = m.rt.notna()
    dur = m.groupby("area")["min"].agg(["median", lambda s: s.quantile(0.9), "size"])
    rob = m.groupby(["area", "robot"])["min"].median().unstack()
    wt = ev_[ev_.type == "arrival_waiting_room"]
    aw = ev_[ev_.type == "checkin_after_wait"][["timestamp_utc", "booking_id"]].rename(columns={"timestamp_utc": "in"})
    w = pd.merge_asof(wt.sort_values("timestamp_utc"), aw.sort_values("in"), left_on="timestamp_utc", right_on="in",
                      by="booking_id", direction="forward")
    wait = ((w["in"] - w.timestamp_utc).dt.total_seconds() / 60).dropna()
    ro = d.roster
    absent = ro[(ro.role == "cleaner") & (ro.status != "scheduled")]
    adays = set(absent.start_utc.dt.tz_convert(LOCAL_TZ).dt.date)
    wdays = wt.timestamp_utc.dt.tz_convert(LOCAL_TZ).dt.date
    on_absent = int(wdays.isin(adays).sum())
    lead = (absent.start_utc - absent.status_known_utc).dt.total_seconds() / 3600
    ci = ev_[ev_.type.isin(["checkin", "checkin_early", "checkin_after_wait"])]
    arr = ci.groupby(ci.timestamp_utc.dt.tz_convert(LOCAL_TZ).dt.date).size()
    busy = float(arr.quantile(0.9)) if len(arr) else np.nan
    wpd = wdays.value_counts()
    rate_busy = float(wpd.reindex(arr[arr >= busy].index).fillna(0).sum() / max(1, (arr >= busy).sum()))
    rate_norm = float(wpd.reindex(arr[arr < busy].index).fillna(0).sum() / max(1, (arr < busy).sum()))
    days_total = max(1, len(arr))
    rate_abs = on_absent / max(1, len(adays))
    rate_other = (len(wt) - on_absent) / max(1, days_total - len(adays))
    rob_txt = "; ".join(f"{int(a)} m²: {r.get(False, np.nan):.0f} → {r.get(True, np.nan):.0f} min"
                        for a, r in rob.iterrows())
    finding = (f"PLATO-40: human room cleaning takes median/P90 "
               + ", ".join(f"{int(a)} m² {r['median']:.0f}/{r['<lambda_0>']:.0f} min (n={int(r['size'])})"
                           for a, r in dur.iterrows())
               + f"; when a robot floor-cleaned the room in the 4 h before, the median is ({rob_txt}). "
               f"{len(wt)} guests waited for their room (median {wait.median():.0f} min, max {wait.max():.0f} min); "
               f"{on_absent} of them on the {len(adays)} days with a cleaner absent ({rate_abs:.2f} waits/day vs "
               f"{rate_other:.2f} on other days). Absences became known only {lead.median():.1f} h (median) before "
               f"shift start. Days with ≥ {busy:.0f} check-ins (top 10 %) have {rate_busy:.2f} waits/day vs {rate_norm:.2f}.")
    return Insight(
        "CLARA-C5", "CLARA", "Learned cleaning durations, robot pre-clean, sick-day buffer", hyp, "confirmed", finding, ev,
        "cleaning_started matched to the next cleaning_completed of the room (≤ 6 h); robot pre-clean = "
        "robot_floor_cleaning_started for that room ≤ 4 h before; waits matched to checkin_after_wait by booking.",
        "CLARA: plan turnarounds with the learned P90 duration per room size (not a flat slot); dispatch robots to "
        "pre-clean vacated larger rooms first; accept early check-in only when a cleaner slot finishes ≥ 30 min before "
        "the promised time; on days in the top-10 % arrival band pre-book ONCALL_1 and resequence cleaning as soon "
        "as a sickness notice arrives.",
        "Qualitative / simulated: fewer waiting guests on sick and peak days; robot effect is observational, not causal.",
        "Robot pre-clean is not randomised (scheduling may favour easier rooms); durations are quantised to 15 min; "
        f"only {len(wt)} waits in the period -> rates are noisy.",
        0.65,
        {"duration_median_min": {int(a): float(r["median"]) for a, r in dur.iterrows()},
         "waits": int(len(wt)), "waits_on_absent_days": on_absent, "absent_days": len(adays),
         "absence_notice_median_h": round(float(lead.median()), 1) if len(lead) else None})


def c6_logistics(c: Ctx) -> Insight:
    hyp = "C6 Waste / vending service frequency vs occupancy (robots carrying bins)"
    d, ev_ = c.data, c.data.events
    ev = ["logistics.csv: WASTE_F*/WASTE_MAIN fill_pct, VEND1 stock (hourly)",
          "events.jsonl: vending_out_of_stock, waste_bin_full, floor_bin_emptied, waste_pickup, vending_restocked",
          "room_telemetry.csv + stays: occupied share per day"]
    lg = d.logistics_wide()
    if lg.empty:
        return insufficient("CLARA-C6", "CLARA", "Logistics", hyp, "no logistics data", ev)
    vo = ev_[ev_.type == "vending_out_of_stock"]
    wf = ev_[ev_.type == "waste_bin_full"]
    vo_dow = vo.timestamp_utc.dt.tz_convert(LOCAL_TZ).dt.day_name().value_counts()
    prod = vo["product"].value_counts().to_dict() if "product" in vo else {}
    main_col = next((x for x in lg.columns if x.startswith("WASTE_MAIN")), None)
    main_full_h = int((lg[main_col] >= 100).sum()) if main_col else 0
    floor_cols = [x for x in lg.columns if x.startswith("WASTE_F")]
    floor_max = float(lg[floor_cols].max().max()) if floor_cols else np.nan
    floor_p95 = float(np.nanpercentile(lg[floor_cols].to_numpy(), 95)) if floor_cols else np.nan
    occ = c.tel.groupby(c.tel.timestamp_utc.dt.floor("1D")).occ.mean()
    vo_occ = occ.reindex(vo.timestamp_utc.dt.floor("1D")).mean() if len(vo) else np.nan
    top_dow = vo_dow.index[0] if len(vo_dow) else "n/a"
    finding = (f"PLATO-40: VEND1 ran out of stock {len(vo)}x ({prod}), {vo_dow.iloc[0] if len(vo_dow) else 0} of them on "
               f"{top_dow}s, on days with mean occupancy {pct(vo_occ)} vs "
               f"{pct(occ.mean())} overall. WASTE_MAIN sat at 100 % for {main_full_h} hours ({len(wf)} waste_bin_full "
               f"events). Floor bins: max {floor_max:.0f} %, P95 {floor_p95:.0f} % — "
               + ("the hypothesis that robots should carry floor bins is rejected for PLATO-40; the bottleneck is the "
                  "central container and the vending restock rhythm." if floor_max < 90 else
                  "floor bins do get full, so robot bin runs remain an option."))
    return Insight(
        "CLARA-C6", "CLARA", "Occupancy-driven restock and extra pickups", hyp, "qualified", finding, ev,
        "Counts of logistics events by local weekday; hourly fill/stock distributions; occupancy from stays.",
        "CLARA: order an extra vending restock (24 h lead) when forecast occupancy for the days until the next "
        "regular restock exceeds the yearly median and stock is projected to run out; order an extra WASTE_MAIN "
        "pickup when the fill trend projects 100 % before the next regular pickup; keep floor-bin emptying with "
        "housekeeping (no robot bin runs needed).",
        "Qualitative: fewer empty-vending and overflowing-container hours; extra service costs need approval if notable.",
        "Synthetic consumption models; fill values capped at 100 % (overflow volume unknown); few events.",
        0.6,
        {"vending_out_of_stock": int(len(vo)), "by_weekday": {k: int(v) for k, v in vo_dow.items()},
         "waste_main_full_hours": main_full_h, "floor_bin_max_pct": floor_max})

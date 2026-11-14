"""Room comfort analyses: P1 floor-1 cold, P2 summer overheating, C3 pre-heating."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import (COMFORT_LO, HOT_C, MIN_N, SUMMER, WINTER, Ctx, Insight, insufficient, months_label,
                     pct)

OUT_BINS = [-40, -5, 0, 5, 10, 40]


def _cold(s: pd.Series) -> float:
    s = s.dropna()
    return float((s < COMFORT_LO).mean()) if len(s) else float("nan")


def p1_floor_cold(c: Ctx) -> Insight:
    hyp = "P1 Floor 1 (above the open car park) runs colder in winter"
    tel = c.tel
    w = tel[tel.month.isin(WINTER) & tel.occ & tel.temperature_c.notna() & (tel.window_open != 1)]
    ev = [f"room_telemetry.csv: temperature_c, window_open=0, {months_label(WINTER)}, {c.period}",
          "events.jsonl: stays (checkin*/stay_in_progress -> checkout*) via plato40.state.State = occupied",
          "building.json: floor, orientation, area_m2 per room"]
    if len(w) < MIN_N or w.floor.nunique() < 2:
        return insufficient("PLATO-P1", "PLATO", "Floor above car park runs cold", hyp, "no occupied winter data", ev)
    lowest = int(w.floor.min())
    by_floor = w.groupby("floor").temperature_c.apply(_cold)
    f1 = float(by_floor[lowest])
    others = by_floor.drop(lowest)
    # stratify by orientation x area so the room mix cannot explain the difference
    strata = []
    for (ori, area), g in w.groupby(["orientation", "area_m2"]):
        a, b = g[g.floor == lowest], g[g.floor != lowest]
        if len(a) >= MIN_N // 5 and len(b) >= MIN_N // 5:
            strata.append((ori, int(area), _cold(a.temperature_c), _cold(b.temperature_c), len(a)))
    worse = [s for s in strata if s[2] - s[3] > 0.02]
    w_diff = (sum((s[2] - s[3]) * s[4] for s in strata) / max(1, sum(s[4] for s in strata))) if strata else np.nan
    sp = w.groupby(w.floor == lowest).setpoint_c.mean()
    mean_t = w.groupby(w.floor == lowest).temperature_c.mean()
    # does the gap persist across outdoor temperatures (envelope) or only in cold spells?
    wx = c.data.weather.set_index("timestamp_utc").outdoor_c
    dm = wx.groupby(wx.index.tz_convert("Europe/Vienna").date).mean()
    hs = tel[tel.occ & tel.temperature_c.notna() & (tel.window_open != 1)].copy()
    hs["dout"] = hs.date.map(dm)
    hs = hs[hs.dout < 12]
    hs["ob"] = pd.cut(hs.dout, OUT_BINS)
    tab = hs.groupby(["ob", hs.floor == lowest], observed=True).temperature_c.apply(_cold).unstack()
    bins_txt = "; ".join(f"{iv.left:g}..{iv.right:g} °C: {pct(r.get(True))} vs {pct(r.get(False))}"
                         for iv, r in tab.iterrows() if np.isfinite(r.get(True, np.nan)))
    diff = f1 - float(others.mean())
    if diff > 0.03 and len(worse) >= 0.75 * len(strata):
        status = "confirmed"
    elif diff > 0.03:
        status = "qualified"
    else:
        status = "rejected"
    strata_txt = "; ".join(f"{o}/{a} m²: {pct(x)} vs {pct(y)}" for o, a, x, y, _ in strata)
    finding = (f"PLATO-40: occupied floor-{lowest} rooms are below {COMFORT_LO:g} °C in {pct(f1)} of winter intervals "
               f"vs {pct(others.min())}–{pct(others.max())} on floors {', '.join(str(int(f)) for f in others.index)} "
               f"(n={len(w):,} occupied, window-closed intervals). Controlled for orientation x room size the floor-"
               f"{lowest} excess is {100 * w_diff:+.1f} pp (worse in {len(worse)}/{len(strata)} strata: {strata_txt}). "
               f"Setpoints do not explain it (mean {sp.get(True, np.nan):.2f} °C floor {lowest} vs "
               f"{sp.get(False, np.nan):.2f} °C others; mean temperature {mean_t.get(True, np.nan):.2f} vs "
               f"{mean_t.get(False, np.nan):.2f} °C). By daily mean outdoor temperature (heating season): {bins_txt}.")
    if status == "qualified":
        finding += " The effect is not uniform across strata -> hypothesis qualified, not fully confirmed."
    return Insight(
        "PLATO-P1", "PLATO", "Floor above the car park runs cold", hyp, status, finding, ev,
        "Share of occupied 15-min intervals with temperature_c < 20 °C, occupancy from stays (not setpoint "
        "proxy), open-window intervals excluded, stratified by orientation x area_m2, cross-tab by daily mean "
        "outdoor temperature.",
        f"Next PLATO: insulate the floor slab over the open car park (soffit insulation / heated soffit) and give "
        f"floor-{lowest} modules a larger emitter or own heating zone; CLARA meanwhile: +0.5 °C setpoint offset "
        f"for floor-{lowest} occupied rooms in the heating season (stays within the 20–23 °C band).",
        f"Qualitative: fewer cold intervals on floor {lowest} (would close most of a {100 * diff:.1f} pp gap); "
        "no savings claimed.",
        "Synthetic, uncalibrated thermal model; floor effect is confounded with any other floor-1 specific factor "
        "(e.g. entrance/lobby proximity) that the data does not describe; motion-free sleeping guests still count "
        "as occupied (correct for comfort).",
        0.75 if status == "confirmed" else 0.55 if status == "qualified" else 0.4,
        {"cold_share_by_floor": {int(k): round(float(v), 4) for k, v in by_floor.items()},
         "stratified_excess_pp": round(100 * float(w_diff), 2), "strata_worse": len(worse), "strata": len(strata),
         "n_intervals": int(len(w))})


def p2_summer_overheating(c: Ctx) -> Insight:
    hyp = "P2 Summer overheating in a heating-only building (south worse)"
    tel = c.tel
    s = tel[tel.month.isin(SUMMER) & tel.temperature_c.notna()]
    so = s[s.occ]
    ev = [f"room_telemetry.csv: temperature_c, {months_label(SUMMER)}, {c.period}",
          "events.jsonl: stays via State = occupied; bms_alarm ROOM_TEMP_HIGH raised",
          "guest_requests.csv: request_type temperature_hot / temperature_cold by room",
          "building.json: orientation; assets (no cooling asset)"]
    if len(so) < MIN_N:
        return insufficient("PLATO-P2", "PLATO", "Summer overheating", hyp, "no occupied summer data", ev)
    hot = float((so.temperature_c > HOT_C).mean())
    hot_vac = float((s[~s.occ].temperature_c > HOT_C).mean())
    by_ori = so.groupby("orientation").temperature_c.apply(lambda x: float((x > HOT_C).mean()))
    by_floor = so.groupby("floor").temperature_c.apply(lambda x: float((x > HOT_C).mean()))
    above_band = float((so.temperature_c > 23.0).mean())
    hot_days = so[so.temperature_c > HOT_C].groupby(["room_id", "date"]).ngroups
    gr = c.data.guest_requests.join(c.rooms[["orientation"]], on="room_id")
    n_hot = int((gr.request_type == "temperature_hot").sum())
    n_cold = int((gr.request_type == "temperature_cold").sum())
    hot_req_ori = gr[gr.request_type == "temperature_hot"].orientation.value_counts().to_dict()
    ori_rooms = c.rooms.orientation.value_counts().to_dict()
    ev_ = c.data.events
    rth = ev_[(ev_.type == "bms_alarm") & (ev_.get("alarm_code") == "ROOM_TEMP_HIGH") & (ev_.get("state") == "raised")]
    south_worse = by_ori.get("S", np.nan) - by_ori.get("N", np.nan)
    status = "confirmed" if hot > 0.15 and south_worse > 0.03 else "qualified" if hot > 0.15 else "rejected"
    finding = (f"PLATO-40 (heating only, no cooling asset): occupied rooms exceed {HOT_C:g} °C in {pct(hot)} of summer "
               f"intervals and are above the 23 °C comfort band in {pct(above_band)} (n={len(so):,}); "
               f"{hot_days:,} occupied room-days with > {HOT_C:g} °C. South {pct(by_ori.get('S'))} vs north "
               f"{pct(by_ori.get('N'))}; by floor "
               + ", ".join(f"{int(k)}: {pct(v)}" for k, v in by_floor.items())
               + f". Vacant rooms also reach > {HOT_C:g} °C in {pct(hot_vac)} -> the envelope/solar gain, not guests, "
               f"drives it. Guests sent {n_hot} temperature_hot vs {n_cold} temperature_cold requests "
               f"(hot by orientation {hot_req_ori}, rooms per orientation {ori_rooms}); BMS raised "
               f"{len(rth)} ROOM_TEMP_HIGH alarms.")
    return Insight(
        "PLATO-P2", "PLATO", "Summer overheating without cooling", hyp, status, finding, ev,
        "Share of occupied summer intervals above 26 °C and above 23 °C; split by orientation and floor; "
        "vacant rooms as control; guest temperature requests by orientation.",
        "Next PLATO: external shading (fixed overhangs / exterior blinds) on south facades as standard, plus a "
        "passive/low-energy cooling option in the module catalogue (e.g. ceiling fans, reversible heat pump with "
        "night free cooling). CLARA meanwhile: forecast-driven night flushing and closing blinds on south rooms "
        "before hot afternoons.",
        "Qualitative: fewer hot complaints and alarm-free summer comfort; no savings or temperature reductions "
        "claimed (replayed telemetry cannot react).",
        "Synthetic solar/thermal model; 26 °C is our comfort threshold (the BMS alarms at ~28 °C); guest request "
        "counts are small per room.",
        0.8 if status == "confirmed" else 0.6,
        {"hot_share_occupied": round(hot, 4), "hot_share_vacant": round(hot_vac, 4),
         "hot_share_by_orientation": {k: round(float(v), 4) for k, v in by_ori.items()},
         "temperature_hot": n_hot, "temperature_cold": n_cold, "room_temp_high_alarms": int(len(rth))})


def c3_preheating(c: Ctx) -> Insight:
    hyp = "C3 Pre-heating curve before forecast cold snaps"
    d = c.data
    ev = [f"weather.csv / weather_forecast.csv: outdoor_c vs outdoor_c_forecast by lead time, {c.period}",
          "equipment.csv: HP1 heat_output_kw / electric_kw (COP), status lockout_*",
          "room_telemetry.csv + stays: occupied share < 20 °C by daily mean outdoor temperature",
          "work_orders.csv: HP1 corrective (refrigerant) work orders"]
    tel = c.tel
    wx = d.weather.set_index("timestamp_utc").outdoor_c
    dm = wx.groupby(wx.index.tz_convert("Europe/Vienna").date).mean()
    hs = tel[tel.occ & tel.temperature_c.notna()].copy()
    hs["dout"] = hs.date.map(dm)
    hs = hs[hs.dout < 12]
    if len(hs) < MIN_N:
        return insufficient("CLARA-C3", "CLARA", "Pre-heating before cold snaps", hyp, "no heating-season data", ev)
    hs["ob"] = pd.cut(hs.dout, OUT_BINS)
    cold_by_bin = hs.groupby("ob", observed=True).temperature_c.apply(_cold)
    mild = cold_by_bin[[iv.left >= 5 for iv in cold_by_bin.index]].mean()
    # forecast skill (forecast rows are issued before their targets -> fair to evaluate after the fact)
    f = d.forecast.copy()
    f["lead_h"] = (f.target_utc - f.issued_utc).dt.total_seconds() / 3600
    obs = wx.resample("1h").mean()
    f["err"] = (f.outdoor_c_forecast - obs.reindex(f.target_utc).to_numpy()).abs()
    mae = f.groupby(pd.cut(f.lead_h, [0, 12, 24, 48]), observed=True).err.mean()
    # heat pump COP vs outdoor temperature and lockouts
    hp = d.equipment_wide("HP1")
    hp["out"] = wx.reindex(hp.index)
    run = hp[hp.get("status") == "running"] if "status" in hp else hp
    cop = (run.heat_output_kw / run.electric_kw).replace([np.inf, -np.inf], np.nan)
    cop_bin = cop.groupby(pd.cut(run.out, OUT_BINS), observed=True).median()
    lock = hp[hp.get("status", pd.Series(dtype=str)).astype(str).str.startswith("lockout")] if "status" in hp else hp.iloc[:0]
    wo = d.work_orders
    hp_wo = wo[(wo.asset_id == "HP1") & (wo.type == "corrective")]
    leads = []
    for w in hp_wo.itertuples(index=False):
        prior = lock[(lock.index <= w.created_utc) & (lock.index > w.created_utc - pd.Timedelta(days=21))]
        if len(prior):
            leads.append((w.created_utc - prior.index.min()).total_seconds() / 86400)
    lock_out = lock.out.median() if len(lock) else np.nan
    cold_bins = [iv for iv, v in cold_by_bin.items() if np.isfinite(mild) and v >= 1.5 * mild and iv.right <= 5]
    trig = max((iv.right for iv in cold_bins), default=None)
    finding = (f"PLATO-40: the occupied share below {COMFORT_LO:g} °C rises as days get colder ("
               + "; ".join(f"{iv.left:g}..{iv.right:g} °C: {pct(v)}" for iv, v in cold_by_bin.items())
               + "), while HP1 COP falls ("
               + "; ".join(f"{iv.left:g}..{iv.right:g} °C: {v:.2f}" for iv, v in cop_bin.items() if np.isfinite(v))
               + "). The 06:00-local forecast is good enough to act on: MAE "
               + ", ".join(f"{iv.left:g}–{iv.right:g} h: {v:.2f} K" for iv, v in mae.items())
               + f". HP1 logged {len(lock)} lockout intervals (median outdoor {lock_out:.1f} °C); "
               f"{len(leads)}/{len(hp_wo)} corrective HP1 work orders were preceded by lockouts "
               f"{', '.join(f'{x:.1f}' for x in leads)} days earlier.")
    rec = ("CLARA rule: when the latest forecast shows a daily mean outdoor temperature "
           + (f"≤ {trig:g} °C" if trig is not None else "in the coldest bin")
           + " within 12–24 h, pre-heat occupied and arriving rooms to the upper comfort band (22–23 °C) during "
           "the cheapest hours before the drop, run DHW charging ahead of the cold period, and keep vacant rooms at "
           "setback. Treat a first HP1 lockout_* status in a cold spell as an FDD trigger for a technician request "
           "(approval) instead of waiting for guest complaints.")
    return Insight(
        "CLARA-C3", "CLARA", "Pre-heating before forecast cold snaps", hyp, "confirmed" if cold_bins else "qualified",
        finding, ev,
        "Occupied cold share binned by local daily mean outdoor temperature; COP = heat_output_kw / electric_kw "
        "while running; forecast MAE vs measured hourly mean by lead time; lockouts in the 21 days before HP1 "
        "corrective work orders.",
        rec,
        "Simulated/qualitative: fewer occupied cold intervals during cold snaps and earlier refrigerant-leak "
        "detection; energy cost impact not quantified (pre-heating can shift load to cheap hours but may add kWh).",
        "Correlational (cold days also coincide with HP faults in this dataset); pre-heat effect cannot be "
        "measured on replayed telemetry; synthetic heat-pump and forecast-error models.",
        0.6 if cold_bins else 0.45,
        {"cold_share_by_outdoor_bin": {f"{iv.left:g}..{iv.right:g}": round(float(v), 4) for iv, v in cold_by_bin.items()},
         "forecast_mae_k": {f"{iv.left:g}-{iv.right:g}h": round(float(v), 2) for iv, v in mae.items()},
         "hp_lockout_intervals": int(len(lock)), "lockout_lead_days": [round(x, 1) for x in leads]})

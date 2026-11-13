"""Water analyses: P4 sub-metering, C1 legionella / room rotation."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .common import Ctx, Insight, insufficient

BASE_DAYS = 28
MIN_EXCESS_L = 250.0
MIN_RUN_DAYS = 3


def gap_episodes(c: Ctx) -> tuple[pd.DataFrame, list[dict]]:
    """Daily main - sum(rooms); episodes where the gap exceeds its trailing baseline."""
    d = c.data
    rw = d.room_water.set_index("timestamp_utc").use_l
    wm = d.water_main.set_index("timestamp_utc").use_l
    rooms = rw.groupby(rw.index.floor("1D")).sum(min_count=1)
    main = wm.resample("1D").sum(min_count=1)
    g = pd.DataFrame({"main": main, "rooms": rooms}).dropna()
    g["gap"] = g.main - g.rooms
    base = g.gap.rolling(BASE_DAYS, min_periods=7).median().shift(1)          # trailing: history only
    mad = (g.gap - base).abs().rolling(BASE_DAYS, min_periods=7).median().shift(1)
    g["base"] = base
    g["excess"] = g.gap - base
    g["flag"] = g.excess > np.maximum(MIN_EXCESS_L, 4 * mad)
    eps, run = [], []
    for day, f in g.flag.items():
        if f:
            run.append(day)
            continue
        if len(run) >= MIN_RUN_DAYS:
            eps.append(run)
        run = []
    if len(run) >= MIN_RUN_DAYS:
        eps.append(run)
    out = []
    for r in eps:
        seg = g.loc[r[0]:r[-1]]
        before = g.loc[:r[0]].iloc[-BASE_DAYS - 1:-1]
        out.append({"start": r[0], "end": r[-1], "days": len(r), "excess_l": float(seg.excess.sum()),
                    "excess_l_day": float(seg.excess.mean()), "base_l_day": float(seg.base.mean()),
                    "rooms_ratio": float(seg.rooms.mean() / before.rooms.mean()) if len(before) else np.nan})
    return g, out


def p4_submetering(c: Ctx) -> Insight:
    hyp = "P4 Water sub-metering per riser / technical room"
    ev = ["water_main.csv + room_water.csv: daily use (valid readings, resets = NaN) -> gap = main − Σ rooms",
          "work_orders.csv: corrective work orders on WM_MAIN / rooms (leak texts)",
          "events.jsonl: bms_alarm WATER_NIGHT_FLOW raised"]
    g, eps = gap_episodes(c)
    if g.empty:
        return insufficient("PLATO-P4", "PLATO", "Sub-metering", hyp, "no meter data", ev)
    wo = c.data.work_orders
    main_wo = wo[(wo.asset_id == "WM_MAIN") & (wo.type == "corrective")]
    room_leaks = wo[wo.asset_id.str.match(r"R\d{3}") & wo.summary.str.contains("leak|burst|fill valve|running",
                                                                                case=False, na=False)]
    evs = c.data.events
    wnf = evs[(evs.type == "bms_alarm") & (evs.get("alarm_code") == "WATER_NIGHT_FLOW") & (evs.get("state") == "raised")]
    matched = []
    for e in eps:
        hit = main_wo[(main_wo.created_utc >= e["start"]) & (main_wo.created_utc <= e["end"] + pd.Timedelta(days=3))]
        alarms = wnf[(wnf.timestamp_utc >= e["start"]) & (wnf.timestamp_utc <= e["end"] + pd.Timedelta(days=1))]
        matched.append((e, hit.work_order_id.tolist(), len(alarms)))
    ep_txt = "; ".join(f"{e['start']:%Y-%m-%d}..{e['end']:%Y-%m-%d} ({e['days']} d, +{e['excess_l_day']:.0f} L/day over a "
                       f"{e['base_l_day']:.0f} L/day baseline, ≈{e['excess_l']:.0f} L total, room meters "
                       f"{100 * (e['rooms_ratio'] - 1):+.0f} % vs before, work orders {wos or 'none'}, "
                       f"WATER_NIGHT_FLOW {na})" for e, wos, na in matched)
    explained = sum(1 for _, wos, _ in matched if wos)
    finding = (f"PLATO-40: the unmetered gap main − Σ rooms is {g.gap.median():.0f} L/day (median, IQR "
               f"{g.gap.quantile(.25):.0f}–{g.gap.quantile(.75):.0f}). {len(eps)} multi-day gap excursions: {ep_txt or 'none'}. "
               f"{explained}/{len(eps)} match a WM_MAIN repair — a leak in common pipework that no room meter sees and "
               f"the BMS night-flow rule did not flag. Room-side leaks ({len(room_leaks)} work orders) are visible on room "
               f"meters, common-area leaks only as a few hundred L/day on a meter that also carries irrigation.")
    return Insight(
        "PLATO-P4", "PLATO", "Sub-meter risers and the technical room", hyp,
        "confirmed" if explained else ("qualified" if eps else "rejected"), finding, ev,
        f"Daily gap vs trailing {BASE_DAYS}-day median (shifted, history only); excursion = ≥ {MIN_RUN_DAYS} days with "
        f"excess > max({MIN_EXCESS_L:.0f} L, 4×MAD); matched to WM_MAIN work orders created within the excursion + 3 days.",
        "Next PLATO: sub-meter each riser, the technical room (DHW circulation / plant) and irrigation separately, so "
        "every litre of the main meter is attributed to a zone; add a leak sensor in the technical room. CLARA "
        "meanwhile: daily gap-baseline rule (this method) as a water-guard trigger.",
        "Qualitative: common-area leaks localised in hours instead of days; no water/cost savings claimed.",
        "Gap baseline drifts with season (irrigation, common use); meter jitter/resets add noise; one confirmed "
        "gap leak is a small sample.",
        0.75 if explained else 0.4,
        {"gap_median_l_day": round(float(g.gap.median()), 1), "episodes": [
            {"start": str(e["start"].date()), "end": str(e["end"].date()), "excess_l": round(e["excess_l"])} for e in eps],
         "explained_by_wm_main_wo": explained, "water_night_flow_alarms": int(len(wnf))})


def c1_room_rotation(c: Ctx) -> Insight:
    hyp = "C1 Room allocation rotation to avoid long vacancy (legionella flushes)"
    wo = c.data.work_orders
    leg = wo[wo.summary.str.contains("legionella", case=False, na=False)]
    ev = ["work_orders.csv: 'Legionella prevention: taps flushed in long-vacant rooms ...' (room ids in text)",
          "events.jsonl: stays via State (vacancy runs per room)", "building.json: area_m2 (room type)"]
    tel = c.tel
    runs = []
    for room, gg in tel.sort_values("timestamp_utc").groupby("room_id"):
        vac = (~gg.occ.to_numpy()).astype(int)
        ts = gg.timestamp_utc.to_numpy()
        edges = np.flatnonzero(np.diff(np.r_[0, vac, 0]))
        for a, b in zip(edges[::2], edges[1::2]):
            runs.append((room, pd.Timestamp(ts[a]), pd.Timestamp(ts[b - 1]), (b - a) / 96))
    runs = pd.DataFrame(runs, columns=["room", "start", "end", "days"])
    if runs.empty:
        return insufficient("CLARA-C1", "CLARA", "Room rotation", hyp, "no telemetry", ev)
    long_days = 7
    long = runs[runs.days >= long_days]
    flushed = [r for s in leg.summary for r in re.findall(r"R\d{3}", s)]
    # rotation potential: during a long vacancy, was a same-type room checked in that could have used it?
    ev_ = c.data.events
    ci = ev_[ev_.type.isin(["checkin", "checkin_early", "checkin_after_wait"])].copy()
    ci["area"] = ci.room_id.map(c.rooms.area_m2)
    avoidable = 0
    for r in long.itertuples(index=False):
        area = c.rooms.area_m2.get(r.room)
        cut = r.start + pd.Timedelta(days=long_days - 1)
        others = ci[(ci.area == area) & (ci.room_id != r.room) & (ci.timestamp_utc >= r.start) & (ci.timestamp_utc <= cut)]
        avoidable += bool(len(others))
    occ_share = tel.groupby("room_id").occ.mean()
    top = pd.Series(flushed).value_counts().head(4) if flushed else pd.Series(dtype=int)
    finding = (f"PLATO-40: {len(leg)} legionella flushing work orders covered {len(flushed)} room flushes in "
               f"{len(set(flushed))} rooms (most often {', '.join(f'{k} {v}x' for k, v in top.items()) or 'n/a'}). "
               f"Occupancy from stays shows {len(long)} vacancy runs ≥ {long_days} days in {long.room.nunique()} rooms "
               f"(longest {runs.days.max():.1f} days) although room occupancy shares only range "
               f"{occ_share.min():.0%}–{occ_share.max():.0%}; in {avoidable}/{len(long)} of these runs another room of "
               f"the same size received a check-in before day {long_days}, i.e. rotation could have used the idle room.")
    return Insight(
        "CLARA-C1", "CLARA", "Rotate room allocation to avoid stagnant water", hyp,
        "confirmed" if len(leg) and avoidable else "qualified", finding, ev,
        "Vacancy runs = consecutive unoccupied 15-min intervals per room (stays from events); rotation potential = a "
        f"same-area_m2 check-in elsewhere within the first {long_days} days of the run (simulation, not replayed).",
        f"CLARA rule: when allocating a booking, prefer the clean, released room of the requested type with the "
        f"longest current vacancy; if a room still reaches {long_days - 2} vacant days, schedule an autonomous tap "
        "flush during the next housekeeping visit instead of a separate contractor visit.",
        f"Simulated estimate: up to {avoidable} of {len(long)} long vacancies avoidable by rotation (labelled "
        "simulation on historic allocations; no change to replayed data).",
        "Allocation constraints (guest preferences, accessibility, room blocks) are not in the data; flush policy "
        "threshold of the operator is unknown (work orders list rooms only).",
        0.6,
        {"legionella_wos": int(len(leg)), "room_flushes": len(flushed), "long_vacancies": int(len(long)),
         "avoidable_simulated": int(avoidable)})

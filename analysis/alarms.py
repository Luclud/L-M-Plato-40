"""BMS alarm and sensor-network analyses: C2 alarm tuning, P5 gateway reliability."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import COMFORT_LO, Ctx, Insight, insufficient, pct
from .common import occupancy_mask


def _alarms(c: Ctx) -> pd.DataFrame:
    ev = c.data.events
    if "alarm_code" not in ev:
        return ev.iloc[:0].assign(alarm_code=[], state=[], asset_id=[])
    return ev[ev.type == "bms_alarm"]


def c2_alarm_tuning(c: Ctx) -> Insight:
    hyp = "C2 BMS alarm tuning: volume vs real incidents"
    ev = ["events.jsonl: bms_alarm (alarm_code, state, asset_id, value)",
          "room_telemetry.csv + stays: occupied room-days below 20 °C",
          "work_orders.csv: leak / burst / fill-valve / refrigerant work orders"]
    a = _alarms(c)
    if a.empty:
        return insufficient("CLARA-C2", "CLARA", "BMS alarm tuning", hyp, "no BMS alarms", ev)
    raised = a[a.state == "raised"]
    counts = raised.alarm_code.value_counts().to_dict()
    all_counts = a.alarm_code.value_counts().to_dict()
    low = raised[raised.alarm_code == "ROOM_TEMP_LOW"]
    occ_low = occupancy_mask(c.state, low.asset_id, low.timestamp_utc.dt.floor("15min"), c.data.end)
    low_thr = float(low.value.astype(float).max()) if len(low) else np.nan
    tel = c.tel
    cold = tel[tel.occ & (tel.temperature_c < COMFORT_LO)]
    cold_days = cold.groupby(["room_id", "date"]).ngroups
    covered = 0
    if len(low):
        ld = set(zip(low.asset_id, low.timestamp_utc.dt.tz_convert("Europe/Vienna").dt.date))
        cd = set(zip(cold.room_id, cold.date))
        covered = len(ld & cd)
    wo = c.data.work_orders
    leaks = wo[wo.summary.str.contains("leak on|burst|fill valve|continuous running", case=False, na=False)
               & ~wo.asset_id.eq("HP1")]
    wnf = raised[raised.alarm_code == "WATER_NIGHT_FLOW"]
    caught = 0
    for w in leaks.itertuples(index=False):
        caught += bool(((wnf.timestamp_utc <= w.created_utc) & (wnf.timestamp_utc > w.created_utc - pd.Timedelta(days=14))).any())
    double = sum(all_counts.values()) == 2 * sum(counts.values())
    finding = (f"PLATO-40: the BMS raised {sum(counts.values())} alarms ({counts}); "
               + (f"the often-quoted {sum(all_counts.values())} counts raised + cleared events twice. " if double else "")
               + f"only {pct(occ_low.mean() if len(low) else np.nan)} of ROOM_TEMP_LOW alarms (fires at ≤ {low_thr:.1f} °C) "
               f"concern an occupied room — the rest are setback rooms; meanwhile occupied rooms were below "
               f"{COMFORT_LO:g} °C on {cold_days:,} room-days, of which only {covered} had a ROOM_TEMP_LOW alarm. "
               f"WATER_NIGHT_FLOW fired {len(wnf)}x and preceded {caught}/{len(leaks)} leak repairs within 14 days.")
    return Insight(
        "CLARA-C2", "CLARA", "Retune BMS comfort and water alarms", hyp, "confirmed", finding, ev,
        "ROOM_TEMP_LOW raised events joined to stays at the alarm time; occupied cold room-days from telemetry; "
        "WATER_NIGHT_FLOW raised within 14 days before leak work orders (text match).",
        f"CLARA: make comfort alarms occupancy-aware — suppress ROOM_TEMP_LOW for vacant setback rooms (unless < 12 °C "
        f"frost risk) and alarm occupied rooms at < {COMFORT_LO:g} °C for ≥ 1 h (and arriving rooms 2 h before check-in); "
        "replace the single main-meter night-flow rule by per-room continuous-flow and main-minus-rooms gap rules "
        "(see PLATO-P4) evaluated by the water agent; keep BMS alarms as evidence, not as triggers.",
        "Qualitative: far fewer irrelevant comfort alarms, earlier detection of real occupied-room cold and leaks; "
        "alarm volume after retuning not simulated.",
        "BMS alarm logic is unknown (delayed/incomplete by design); leak work-order matching by text; 'cold' uses our "
        "20 °C band threshold.",
        0.8,
        {"raised_by_code": {k: int(v) for k, v in counts.items()},
         "room_temp_low_occupied_share": round(float(occ_low.mean()), 4) if len(low) else None,
         "occupied_cold_room_days": int(cold_days), "cold_days_with_alarm": int(covered),
         "leak_wos": int(len(leaks)), "leaks_preceded_by_night_flow": int(caught)})


def p5_gateways(c: Ctx) -> Insight:
    hyp = "P5 Gateway reliability per floor -> placement / redundancy"
    ev = ["room_telemetry.csv: intervals where all rooms of a gateway lack temperature_c (gateway outage)",
          "building.json: sensors[].gateway (room -> gateway)",
          "events.jsonl: bms_alarm SENSOR_COMM_LOSS raised by room"]
    gw = c.rooms.gateway.dropna()
    if gw.empty:
        return insufficient("PLATO-P5", "PLATO", "Gateway reliability", hyp, "no gateway mapping", ev)
    t = c.tel.assign(na=c.tel.temperature_c.isna(), gw=c.tel.room_id.map(gw))
    full = t.groupby(["gw", "timestamp_utc"]).na.mean().unstack(0).fillna(0) == 1.0
    stats = {}
    for g in sorted(gw.unique()):
        s = full[g].astype(int) if g in full else pd.Series(dtype=int)
        stats[g] = {"events": int(((s.diff() == 1).sum()) + int(len(s) and s.iloc[0] == 1)), "hours": float(s.sum() / 4),
                    "room_missing_share": float(t[t.gw == g].na.mean())}
    a = _alarms(c)
    cl = a[(a.alarm_code == "SENSOR_COMM_LOSS") & (a.state == "raised")] if len(a) else a
    cl_gw = cl.asset_id.map(gw).value_counts().to_dict() if len(cl) else {}
    worst = max(stats, key=lambda k: stats[k]["hours"])
    total_h = sum(v["hours"] for v in stats.values())
    share_worst = stats[worst]["hours"] / total_h if total_h else np.nan
    status = "confirmed" if total_h and share_worst > 0.5 else ("qualified" if total_h else "rejected")
    finding = (f"PLATO-40: gateway outages (all rooms of a gateway silent) — "
               + "; ".join(f"{g}: {v['events']} outages, {v['hours']:.1f} h, room-level missing {pct(v['room_missing_share'], 2)}"
                           for g, v in stats.items())
               + f". {worst} accounts for {pct(share_worst)} of outage hours and {cl_gw.get(worst, 0)}/{len(cl)} "
               "SENSOR_COMM_LOSS alarms. A floor gateway is a single point of failure: during an outage every room "
               "on that floor is unknown (no comfort, presence or leak evidence).")
    return Insight(
        "PLATO-P5", "PLATO", "Redundant sensor gateways per floor", hyp, status, finding, ev,
        "Outage = 15-min interval where every room mapped to the gateway has temperature_c missing; "
        "SENSOR_COMM_LOSS raised alarms mapped room -> gateway.",
        f"Next PLATO: two gateways per floor with overlapping coverage (or a meshed sensor network), gateway on UPS, "
        f"and check the radio path/placement of {worst} before copying the floor layout; CLARA: treat a gateway "
        "outage as 'unknown' for every room on the floor and raise one data-quality episode, not per-room alarms.",
        "Qualitative: no floor-wide blind spots; fewer per-room comm-loss alarms; no cost figures claimed.",
        "Cause of outages (placement, power, interference) is not in the data; synthetic outage model; few events.",
        0.65 if status == "confirmed" else 0.45,
        {"gateways": {g: {k: round(v, 3) if isinstance(v, float) else v for k, v in s.items()} for g, s in stats.items()},
         "comm_loss_by_gateway": {k: int(v) for k, v in cl_gw.items()}})

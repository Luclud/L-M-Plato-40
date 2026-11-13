#!/usr/bin/env python3
"""Offline evaluation: how many incidents did we catch, how early, and how much noise?

    python eval/evaluate.py out/qs.jsonl --labels eval/labels_quickstart.json
    python eval/evaluate.py out/dev.jsonl --data data/dev          # weak labels from work orders/events

This is a development aid only. Labels are NOT ground truth, and the pipeline
(plato40/) must never import or read anything from eval/.

Matching rule: an incident is detected if a decision has the same canonical
issue_type, shares at least one asset (or the incident asset is BUILDING), and its
timestamp lies in [start, end]. Latency = first matching decision - start.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROUTINE = {"data_quality", "energy_optimization", "logistics", "guest_request"}  # not counted as false alarms


def weak_labels(data_dir: str) -> list[dict]:
    """Derive approximate incidents for any dataset from work orders + events (dev use)."""
    from plato40 import io  # local import: eval may use the loader, never the reverse

    d = io.load(data_dir)
    inc = []
    rules = [
        ("Burst flexible hose", "water_leak", "S02-loud", 3),
        ("Toilet fill valve", "water_leak", "S02-subtle", 10),
        ("Leak on DHW circulation pipe", "water_leak", "S02-gap", 14),
        ("refrigerant leak", "heating_comfort", "S03", 7),
        ("Heavily loaded AHU1 filters", "ventilation_maintenance", "S04", 14),
    ]
    for w in d.work_orders.itertuples(index=False):
        for key, issue, scen, days in rules:
            if key.lower() in str(w.summary).lower():
                assets = [w.asset_id] if w.asset_id != "WM_MAIN" else ["WM_MAIN", "BUILDING"]
                if w.asset_id.startswith("R"):
                    assets.append(f"WM_{w.asset_id}")
                inc.append({"id": f"WO-{w.work_order_id}", "scenario": scen, "issue_type": issue, "assets": assets,
                            "start_utc": str(w.created_utc - pd.Timedelta(days=days)), "end_utc": str(w.created_utc),
                            "notes": w.summary})
    ev = d.events[d.events.type == "arrival_waiting_room"]
    for e in ev.itertuples(index=False):
        inc.append({"id": f"WAIT-{e.event_id}", "scenario": "S01", "issue_type": "room_turnaround",
                    "assets": [e.room_id], "start_utc": str(e.timestamp_utc - pd.Timedelta(hours=12)),
                    "end_utc": str(e.timestamp_utc), "notes": "guest waited for room"})
    # surge: local days with many arrivals and a sick cleaner that day
    b = d.bookings[d.bookings.status == "confirmed"]
    arr = b.groupby(b.arrival_utc.dt.tz_convert("Europe/Vienna").dt.date).size()
    thr = max(18, int(arr.quantile(0.95)))
    sick = d.roster[(d.roster.role == "cleaner") & (d.roster.status == "sick")]
    sick_days = set(sick.start_utc.dt.tz_convert("Europe/Vienna").dt.date)
    for day, n in arr.items():
        if n >= thr and (day in sick_days or (pd.Timestamp(day) + pd.Timedelta(days=1)).date() in sick_days):
            t = pd.Timestamp(day).tz_localize("Europe/Vienna").tz_convert("UTC")
            inc.append({"id": f"SURGE-{day}", "scenario": "S05", "issue_type": "occupancy_surge", "assets": ["BUILDING"],
                        "start_utc": str(t - pd.Timedelta(days=2)), "end_utc": str(t + pd.Timedelta(days=1, hours=12)),
                        "notes": f"{n} arrivals, cleaner sick"})
    return inc


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("decisions")
    p.add_argument("--labels", help="labels json (e.g. eval/labels_quickstart.json)")
    p.add_argument("--data", help="data dir to derive weak labels from work orders/events")
    a = p.parse_args(argv)
    if not (a.labels or a.data):
        p.error("give --labels or --data")
    incidents = json.loads(Path(a.labels).read_text())["incidents"] if a.labels else weak_labels(a.data)

    rows = [json.loads(line) for line in open(a.decisions, encoding="utf-8") if line.strip()]
    for r in rows:
        r["_t"] = pd.Timestamp(r["timestamp_utc"])

    matched_eps = set()
    results = []
    for inc in incidents:
        s, e = pd.Timestamp(inc["start_utc"]), pd.Timestamp(inc["end_utc"])
        assets = set(inc["assets"])
        hits = [r for r in rows if r.get("issue_type") == inc["issue_type"] and s <= r["_t"] <= e
                and ("BUILDING" in assets or assets & set(r.get("affected_assets", [])))]
        hits.sort(key=lambda r: r["_t"])
        for h in hits:
            matched_eps.add(h.get("episode_id") or h["event_id"])
        first = hits[0] if hits else None
        good_by = pd.Timestamp(inc["good_by_utc"]) if inc.get("good_by_utc") else None
        results.append({
            "id": inc["id"], "scenario": inc["scenario"], "detected": bool(first),
            "latency_h": round((first["_t"] - s).total_seconds() / 3600, 1) if first else None,
            "in_time": (first["_t"] <= good_by) if (first and good_by is not None) else None,
            "first": f"{first['event_id']} {first['action']} @ {first['timestamp_utc']}" if first else "",
            "decisions": len(hits),
        })

    eps = defaultdict(list)
    for r in rows:
        eps[r.get("episode_id") or r["event_id"]].append(r)
    unmatched = Counter()
    for ep, rs in eps.items():
        it = rs[0].get("issue_type")
        if it not in ROUTINE and ep not in matched_eps:
            unmatched[it] += 1

    det = sum(r["detected"] for r in results)
    print(f"\nIncidents detected: {det}/{len(results)}")
    print(f"{'incident':28} {'scen':10} {'det':4} {'lat_h':>6} {'in_time':8} {'#dec':>4}  first decision")
    for r in results:
        it = "" if r["in_time"] is None else ("yes" if r["in_time"] else "LATE")
        print(f"{r['id'][:28]:28} {r['scenario']:10} {'Y' if r['detected'] else '-':4} "
              f"{'' if r['latency_h'] is None else r['latency_h']:>6} {it:8} {r['decisions']:>4}  {r['first']}")
    print(f"\nEpisodes not matching any label (possible false alarms - inspect!): {sum(unmatched.values())} "
          f"{dict(unmatched)}")
    print("Routine issue types not counted:", ", ".join(sorted(ROUTINE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

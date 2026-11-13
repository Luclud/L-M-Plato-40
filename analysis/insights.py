#!/usr/bin/env python3
"""Level 3 learning loop (ticket T11): regenerate insights.md + insights.jsonl from a dataset.

    python analysis/insights.py data/dev --out insights.md --jsonl insights.jsonl

Deterministic (no clock, no randomness, no network). Never reads eval/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import alarms, comfort, maintenance, operations, water  # noqa: E402
from analysis.common import Ctx, Insight, build  # noqa: E402

ANALYSES = [
    comfort.p1_floor_cold, comfort.p2_summer_overheating, maintenance.p3_plant_access, water.p4_submetering,
    alarms.p5_gateways,
    water.c1_room_rotation, alarms.c2_alarm_tuning, comfort.c3_preheating, maintenance.c4_filter_interval,
    operations.c5_cleaning, operations.c6_logistics,
]


def run_all(c: Ctx) -> list[Insight]:
    out = []
    for fn in ANALYSES:
        try:
            out.append(fn(c))
        except Exception as e:  # one failing analysis must not drop the others (e.g. holdout schema quirks)
            out.append(Insight(fn.__name__.upper(), "CLARA" if fn.__name__.startswith("c") else "PLATO", fn.__name__,
                               fn.__name__, "insufficient data",
                               f"PLATO-40: analysis failed on this dataset ({type(e).__name__}: {e}).",
                               [], "n/a", "Fix the analysis for this dataset.", "none claimed", "untested", 0.0))
    return out


ARCHITECTURE = """\
## Target architecture (CLARA Future for the next PLATO building)

Starting point: `docs/ARCHITECTURE.md` §3, adapted with the findings above (insight ids in brackets).

```
 Data layer (event time + availability time on every record)
  PMS/bookings · ACS doors · BMS/BACnet (HP, AHU, room controllers) · meters: room + riser + technical room
  + irrigation + main [PLATO-P4] · redundant floor gateways [PLATO-P5] · energy (PV, battery, grid) · weather +
  day-ahead price APIs · roster/HR · CMMS (work orders, contractors) · guest messaging · robot fleet · bin/vending
                │
                ▼
 Shared state + as-of store (occupancy from stays, housekeeping, availability as known at t, data-quality map)
                │
   ┌────────────┼──────────────────────────────────────────────────────────────────────────────┐
   ▼            ▼               ▼                ▼                 ▼               ▼             ▼
 Housekeeping  Energy &        Maintenance/FDD  Water guard       Guest service   Logistics     Data quality
 planner       comfort         (HP lockouts,    (room flow +      (multilingual,  (restock /    (gateway outage
 (P90 durations, (forecast pre-  dp-trend filter  main−Σrooms gap   consent-aware)  pickup from   = floor unknown)
 robot pre-clean, heat, shading/ forecast) [C4,  per zone) [C2,P4]                 occupancy     [P5]
 rotation) [C1,C5] night flush) [C3,P2] C3]                                         trend) [C6]
   └────────────┴───────────────┴────────────────┴─────────────────┴───────────────┴─────────────┘
                │ decisions (policy: autonomous / approval / approval + consent)
                ▼
 Operations coordinator → mock executor / robots (floor cleaning, occupancy lock-out) / human approval inbox
 (technician, contractor, lift platform until P3 fixed, room block, cost) / guest consent requests → audit trail
                │ verification signals (telemetry, meters, work-order completion) → escalation timers
                ▼
 Learning loop: this script yearly (and rolling) → CLARA rule updates [C1–C6] + PLATO design feedback [P1–P5]
```

- **Interfaces:** BACnet/Modbus to the BMS, REST/webhooks to PMS, CMMS and the robot fleet, MQTT for meters and
  gateways, messaging API for guests; every agent writes decisions in the `decisions.jsonl` contract.
- **Design changes the findings add to the baseline:** occupancy-aware alarms instead of raw BMS thresholds (C2),
  condition-based maintenance from trends (C4), zone water metering (P4), gateway redundancy (P5), maintainable
  plant access (P3), envelope/shading per module orientation and floor (P1, P2).
"""

LIMITATIONS = """\
## Limitations

- All findings concern the fictional **PLATO-40** and one synthetic, uncalibrated dataset — not real PLATO
  buildings or CLARA installations. Effects are correlational; nothing here was tested by intervention.
- This is a retrospective analysis of the whole period (allowed for learning, unlike the decision pipeline);
  the CLARA rules it proposes are written so they can run causally (trailing baselines, forecasts as issued).
- Expected benefits are qualitative or labelled *simulated*; no savings are claimed.
- Thresholds (20 °C, 26 °C, 7-day vacancy, gap baseline) are our choices; numbers are regenerated from the data
  each run, so the holdout or next year's data can confirm or overturn them.
- BMS alarms and work orders are human/rule outputs (late, terse, incomplete) and only weak evidence.
"""


def render_md(c: Ctx, ins: list[Insight]) -> str:
    d = c.data
    lines = [
        "# PLATO-40 — Level 3 feedback (learning loop)", "",
        "> All findings concern the fictional **PLATO-40** and its synthetic dataset — not real PLATO buildings.",
        f"> Generated by `python analysis/insights.py <dataset dir> --out insights.md --jsonl insights.jsonl` from "
        f"the **{d.manifest.get('profile')}** dataset (`{d.path.name}`, "
        f"{c.period}). Do not edit by hand — rerun the script.", "",
        "Format per insight: finding → evidence (file, filter, method, numbers) → recommendation → expected benefit → "
        "uncertainty / confidence. Status says whether the starting hypothesis was confirmed, qualified or rejected.", "",
        "| id | target | topic | status | confidence |", "|---|---|---|---|---|",
    ]
    lines += [f"| {i.insight_id} | {i.target} | {i.topic} | {i.status} | {i.confidence:.2f} |" for i in ins]
    for target, title in (("PLATO", "For PLATO (build the next building differently)"),
                          ("CLARA", "For CLARA (operate differently next year)")):
        lines += ["", f"## {title}"]
        for i in (x for x in ins if x.target == target):
            lines += ["", f"### {i.insight_id} — {i.topic}", "",
                      f"- **Hypothesis:** {i.hypothesis} → **{i.status}**",
                      f"- **Finding:** {i.finding}",
                      "- **Evidence:** " + "; ".join(f"`{e}`" for e in i.evidence),
                      f"- **Method:** {i.method}",
                      f"- **Recommendation:** {i.recommendation}",
                      f"- **Expected benefit:** {i.expected_benefit}",
                      f"- **Uncertainty:** {i.uncertainty} Confidence {i.confidence:.2f}."]
    lines += ["", ARCHITECTURE, LIMITATIONS]
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="PLATO-40 Level 3 insights")
    p.add_argument("data_dir")
    p.add_argument("--out", default="insights.md")
    p.add_argument("--jsonl", default="insights.jsonl")
    a = p.parse_args(argv)
    t0 = time.perf_counter()
    c = build(a.data_dir)
    ins = run_all(c)
    Path(a.out).write_text(render_md(c, ins), encoding="utf-8")
    Path(a.jsonl).write_text("".join(json.dumps(i.record(), ensure_ascii=False) + "\n" for i in ins), encoding="utf-8")
    n = {t: sum(1 for i in ins if i.target == t) for t in ("PLATO", "CLARA")}
    print(f"[insights] {n} -> {a.out}, {a.jsonl} in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

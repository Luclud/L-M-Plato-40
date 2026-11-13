# T11 — Level 3 insights: CLARA rules + PLATO design feedback + target architecture

**Lane:** C presentation · **Priority:** P0 · **Depends on:** none to start (agent outputs improve it) · **Score impact:** learning loop (10), target architecture (5)

## Goal
Turn the dev year into evidence-based recommendations: for **CLARA** (thresholds, cleaning strategy, pre-heating, maintenance
intervals, alarm tuning) and for **PLATO** (what to build differently). Each: finding → evidence (file, filter, method, numbers) →
recommendation → expected benefit (qualitative or labelled simulated) → uncertainty/confidence.

## Starting hypotheses (verify or reject — rejected ones are also valuable, say so)
- **P1** Floor 1 (above the open car park) runs colder in winter (preliminary 19–22 % of occupied intervals < 20 °C vs 9–13 %).
  Control for occupancy (use `State` stays, not setpoint proxy), orientation, room size, window_open.
- **P2** Summer overheating in a heating-only building (25–39 % of summer occupied intervals > 26 °C, south worse;
  201 temperature_hot vs 29 cold requests) → shading / passive cooling.
- **P3** Rooftop AHU needs a lift platform → postponed filter changes (dev WOs 23 Feb, 4 Jun) → plant access design.
- **P4** Water sub-metering per riser / technical room (DHW pipe leak 18 Jul only visible on WM_MAIN).
- **P5** Gateway reliability per floor (SENSOR_COMM_LOSS 318, GW_F3 outages) → gateway placement/redundancy.
- **C1** Room allocation rotation to avoid long vacancy (11 legionella flushes of long-vacant rooms).
- **C2** BMS alarm tuning: ROOM_TEMP_LOW/HIGH volume vs real incidents; WATER_NIGHT_FLOW misses (use T12 output).
- **C3** Pre-heating curve before forecast cold snaps (from T02 results / data).
- **C4** Filter maintenance interval from dp trend (days to threshold after change) instead of fixed dates.
- **C5** Cleaning strategy: learned durations, robot pre-clean, early-checkin acceptance rule; sick-day buffer.
- **C6** Waste/vending service frequency vs occupancy (T08).

## Interfaces
`analysis/insights.py data/dev --out insights.md --jsonl insights.jsonl` (deterministic, < 2 min), small helper modules in
`analysis/`. `insights.jsonl` per contract: `insight_id, target, topic, finding, evidence[], recommendation, expected_benefit,
confidence`. `insights.md` also contains the **target architecture** (start from docs/ARCHITECTURE.md §3, adapt with findings)
and a limitations section (synthetic, uncalibrated data; PLATO-40 only).

## Acceptance criteria
- ≥ 4 PLATO and ≥ 4 CLARA insights with concrete numbers and reproducible evidence; at least one rejected/qualified hypothesis.
- Script regenerates both files from data; committed outputs at repo root.
- Every finding says "PLATO-40" and its uncertainty.

## Files to touch
`analysis/*`, `insights.md`, `insights.jsonl`.

# T03 — S04 Maintenance cascade agent (AHU1 filter)

**Lane:** A detection · **Priority:** P0 · **Depends on:** none · **Score impact:** detection (10), robustness (7), coordination (8)

## Goal
Detect filter degradation from the pressure trend, keep the incident alive through sensor gaps (a gap is not a repair),
confirm with airflow, and organise a realistic service given technician availability and access constraints.

## Context
Brief S04: "Filter degradation, missing sensor readings and an unavailable technician. Tell a data gap from real plant
degradation and organise a realistic service." Quickstart: filter_dp 92 → 165 Pa (14–16 Jan), gap 16 Jan 22:30Z → 21 Jan
03:15Z, then 256 Pa and airflow drop; TECH_1 on vacation 19–23 Jan (known in December); contractor replaced 23 Jan.
Dev: postponed filter changes because "rooftop unit only reachable with lift platform".

## Inputs
`data.equipment_wide("AHU1")` (`filter_dp_pa`, `airflow_m3h`, `fan_kw`, `supply_air_c`), BMS FILTER_DP_HIGH / AIRFLOW_LOW
(clue), `state.available("TECH_1", when, known_at=t)`, work orders as-of (preventive, postponed, completed filter changes =
reset of the trend), building.json AHU nominal airflow 4200 m³/h.

## Suggested approach
1. Daily/6-hourly robust level of filter_dp; trend (slope over last 3–7 days) relative to the post-replacement baseline
   (baseline resets after a completed filter-change work order known at t, or a step down in dp).
2. Trigger early when level exceeds baseline by X % and slope is positive (before BMS threshold); confirm with airflow
   below nominal / fan_kw rising.
3. Gap handling: if `filter_dp_pa` missing ≥ 2 h, add (once) a follow-up "readings missing — degradation status unknown,
   keep service plan" and keep the episode open. Never close on missing data.
4. Service: find the next working slot for TECH_1 (as known at t; mind vacation/sick, shift 08:00–16:30 Mon–Fri local);
   if none within the urgency horizon → `order_service` contractor (approval, `service="hvac_contractor"`,
   `scheduled_for_utc` ≥ next working day, note lift-platform requirement). Use `defer_action` with a reason when
   service can wait. Close episode when dp drops after replacement (verification).

## Interfaces
`plato40/detectors/s04_maintenance.py` (`MaintenanceAgent`, name `maintenance`), issue_type `ventilation_maintenance`,
episode key `maint:AHU1`.

## Acceptance criteria
- Quickstart: **QS-S04-AHU1-filter detected by 16 Jan 12:00Z**; one episode spanning the gap (no close/re-open during
  16–21 Jan); a contractor `order_service` with approval because TECH_1 is on vacation (as known at t).
- Dev: AHU1 incidents (WO 29 Jul, 24 Oct heavily loaded) detected; postponed preventive WOs reflected; ≤ 3 unmatched episodes.
- No technician scheduled when unavailable (validator). Tests `tests/test_maintenance.py`.

## Out of scope
Fan curve physics.

## Pitfalls
Airflow also varies with operating mode; post-change baseline differs; BMS alarms delayed (21 Jan) — don't wait for them.

## Files to touch
`plato40/detectors/s04_maintenance.py`, `tests/test_maintenance.py`.

# T01 — S02 Water leak agent (loud, subtle, gap)

**Lane:** A detection · **Priority:** P0 · **Depends on:** none · **Score impact:** detection (10), robustness (7), safe autonomy (10)

## Goal
Detect water leaks early — loud bursts, subtle running toilets, and leaks only visible as main-meter minus room-meters
gap — while ignoring guests, cleaning and meter artefacts; escalate safely.

## Context
Brief S02: "Unusual consumption — sometimes loud, sometimes barely above normal, sometimes only in the gap between the main
meter and the room meters. Separate it from guests, cleaning and meter artefacts; inspect and escalate safely."
The BMS raised only 2 WATER_NIGHT_FLOW alarms in the whole dev year → beating the BMS is a headline demo point.

## Inputs
- `data.room_water` / `data.water_main`: valid readings only; `use_l`, `span_intervals`, `rate_l_per_interval`, `reset`
  (already cleaned in `io.py`; resets → NaN; jitter → 0).
- Occupancy: `state.occupied(room, t)`, `telemetry.presence`, `housekeeping_state` (`in_progress` = cleaning),
  events `cleaning_started/_completed`, `checkin*/checkout*`.
- `work_orders` (via `ctx.asof.work_orders()`), guest requests `water_issue` (corroborating evidence, via as-of).
- `events` `bms_alarm` WATER_NIGHT_FLOW (clue only).

## Suggested approach
1. **Loud:** per room, sustained high rate (e.g. ≥ N consecutive intervals above a high per-room percentile and a
   physical floor like ≥ 8 L/15 min) while vacant or at night, or continuous flow with no zero interval for ≥ 2 h.
   Real usage is bursty (showers 10–20 min); leaks are continuous.
2. **Subtle:** per room, share of non-zero intervals and minimum flow in the local night window (01:00–05:00 local,
   DST-aware) over the last k nights vs the room's own history (expanding baseline, warm-up ≥ 3 nights). Running
   toilet = small but almost always > 0, also when vacant.
3. **Gap:** hourly main − Σ rooms; night-time gap vs its own rolling baseline; persistent rise for ≥ 2 nights →
   leak in common areas / risers / technical room (affected_assets `["WM_MAIN","BUILDING"]`).
4. **Look-alikes to rule out:** occupied + bursty pattern (shower/bath, bathtub rooms use more), cleaning in progress,
   meter reset/exchange (skip window around `reset`), single-interval spikes, legionella flushing in vacant rooms
   (short, during working hours, often several rooms — see work orders "Legionella prevention").
5. **Escalation ladder (one episode per room/leak):**
   `flag_anomaly` (autonomous, immediately) → `request_inspection` (approval; `enters_occupied_room=True` if occupied →
   consent) → for loud leaks also propose `water_shutoff` (approval + consent; `safety_relevant=True`) → close when
   flow returns to baseline (verification).
   Add `notify_guest` with `messaging.compose_guest_message("maintenance_consent_request", lang, ...)` if occupied.

## Interfaces
- File: `plato40/detectors/s02_water.py`, class `WaterAgent`, `name="water"`, issue_type **`water_leak`**.
- Episode keys: `water:<room>` / `water:gap`. Evidence like
  `room_water.csv:R104:2026-01-12T17:00:00Z..2026-01-12T19:00:00Z:~27 L/15min continuous, vacant`.
- `verification_plan`: e.g. "Room meter flow returns to ≤ baseline within 1 h after repair; escalate to shutoff request if
  rate increases or water_issue request arrives".

## Acceptance criteria
- Quickstart (`eval/evaluate.py --labels eval/labels_quickstart.json`): **QS-S02-R104-burst detected, in_time (≤ 19:00Z 12 Jan)**;
  **QS-S02-R305-toilet detected before 22 Jan 08:50Z** (guest report).
- Dev (`--data data/dev`): ≥ 4 of the 5 room leaks (WO burst R201, R401; toilet R403, R208; + any others found) and the
  **WM_MAIN DHW pipe gap leak (18 Jul)** detected; ≤ 5 `water_leak` episodes not matching a label (inspect each; list them in PR).
- No decision inside ±2 h of a meter reset for that meter; no decision for legionella flush windows.
- validate PASS on quickstart + dev; tests `tests/test_water.py`: R104 detected; a shower-only occupied night not flagged;
  causality test (truncate data at t, same first decision).

## Out of scope
Actual valve control; plumbing cost estimates beyond "notable cost → approval".

## Pitfalls
- DST: local night window shifts by 1 h in summer. Use `plato40.clock`.
- Cumulative meters over gaps: use `rate_l_per_interval`, never sum NaN as 0.
- Main meter includes roof-garden irrigation (summer!) → gap baseline must be seasonal/rolling.
- Open question (docs/DATA.md #1): R305 subtle leak not visible in main − Σrooms. Document what you find.

## Files to touch
`plato40/detectors/s02_water.py`, `tests/test_water.py`, (docs/DATA.md open question notes).

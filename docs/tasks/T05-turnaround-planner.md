# T05 — S01 Turnaround agent + shared housekeeping planner

**Lane:** B operations · **Priority:** P0 · **Depends on:** none (uses `plato40/messaging.py` stub) · **Score impact:**
prioritisation & constraints (8), cross-domain coordination (8), guest communication (6), safe autonomy (10)

## Goal
Plan who cleans which room when, so arrivals (incl. early check-in requests) get clean rooms; give guests realistic
times; never release an uncleaned room; never schedule absent staff; re-plan when a cleaner calls in sick.
The planner is **shared** with T06 (surge) and T08 (robots/bins).

## Context
Brief S01: "An early departure, an early check-in request and a tight cleaning shift create a turnaround conflict. Never release
an uncleaned room; prioritise cleaning; give the guest a realistic time." Housekeeping 07:00–15:30 local, check-out until 11:00,
check-in from 15:00. Robots shorten human turnover (floors) but cannot clean bathrooms, release or enter occupied rooms.
ONCALL_1 15:30–22:00 local, ~45 min to site, one room at a time, costly, waiting guests only.

## Inputs
Events (`checkout`, `checkout_early`, `checkin*`, `cleaning_started/_completed` with `resource_id` — use them to **learn
cleaning durations** per room size from history, `robot_floor_cleaning_*`, `arrival_waiting_room`), bookings (as-of by
`created_utc`; arrival = booked date at 15:00 local), guest requests (`early_checkin` with requested time in text →
`messaging.parse_requested_time`), roster via `state.available(..., known_at=t)`, housekeeping state via `state.housekeeping`.

## Suggested approach
1. `plato40/planning/housekeeping.py`: `plan_day(t, state, asof, demand)` → list of `(room, resource, start, end)`,
   using learned duration (human only vs with robot floor pre-clean), shift windows, availability as known at t; greedy by
   deadline (earliest arrival / requested early check-in first, then rooms with guests waiting); capacity check reports
   shortfall instead of inventing capacity.
2. Re-plan triggers: 06:30 local daily, each `checkout_early`, each new `early_checkin` request, roster change becoming
   known (`status_known_utc`), robot out_of_service, arrival_waiting_room.
3. Decisions: `schedule_cleaning` (resource_id, scheduled_for_utc), `dispatch_robot` (floor pre-clean, vacant rooms only),
   `release_room` only when `state.can_release` is True at that time (usually at the `cleaning_completed` tick),
   `notify_guest` with realistic ETA (`early_checkin_possible` / `not_possible` / `waiting_update`),
   `call_oncall_housekeeper` (approval) only for waiting guests in the on-call window, `reassign_room` (approval) if another
   clean vacant room of the same type exists and the guest would wait > 1 h.
4. Episodes per arrival conflict: `turnaround:<room>:<local_date>`; don't emit plans for rooms without conflict
   (normal operation = silence, or one daily `schedule_cleaning` summary decision per day — keep volume low).

## Interfaces
`plato40/planning/__init__.py`, `plato40/planning/housekeeping.py` (pure functions, unit-testable; T06 & T08 import it),
`plato40/detectors/s01_turnaround.py` (`TurnaroundAgent`, name `turnaround`), issue_type `room_turnaround`.

## Acceptance criteria
- Quickstart: **QS-S01-R308-0108** and **QS-S01-R207-0123** detected before the waiting time (`good_by`); each early_checkin
  request gets a `notify_guest` with an ETA consistent with the plan (ETA ≥ planned cleaning end).
- Dev (`--data data/dev`): ≥ 60 % of `WAIT-*` incidents have a `room_turnaround` decision ≥ 1 h before the wait event.
- Zero validator violations: no release of non-clean rooms, no absent/off-shift resource, robots never release.
- Planner unit tests: sick cleaner known at 05:15 removes capacity from 05:15 on but not before; shortfall reported.
- Total `room_turnaround` decisions on dev ≤ ~3 per conflict on average (no spam).

## Pitfalls
DST; cleaning durations differ by room size/bathtub; `stay_in_progress` at data start; no-shows (rooms stay clean);
room_reassigned events; guests may be asleep with presence=0 (use stays, not presence).

## Files to touch
`plato40/planning/*`, `plato40/detectors/s01_turnaround.py`, `tests/test_planner.py`, `tests/test_turnaround.py`.

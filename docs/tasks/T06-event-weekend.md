# T06 — S05 Event-weekend (surge) agent

**Lane:** B operations · **Priority:** P0 · **Depends on:** T05 planner · **Score impact:** coordination (8), prioritisation (8), guest communication (6)

## Goal
See occupancy surges coming from bookings (as-of), plan cleaners + robots + on-call ahead, re-plan the moment a cleaner calls in
sick, inform guests with realistic times, and **never invent capacity** — escalate for extra staff instead.

## Context
Quickstart: 25 arrivals 23 Jan, 20 on 26 Jan; CLEANER_1 sick 24 Jan known 05:30Z; 6 guests waiting on 24 Jan; ONCALL_1 called twice.
Dev weak labels: days with ≥ max(18, p95) arrivals and a sick cleaner (`eval/evaluate.py` SURGE-*).

## Suggested approach
1. Daily (e.g. 13:00 local, after tariff publication too) compute next 3 days: departures + arrivals (confirmed, known by
   created_utc), learned cleaning minutes → demand vs capacity from roster (as known) incl. robots.
2. If demand > capacity·margin: open episode `surge:<first_local_date>` — `occupancy_surge`, decisions:
   `request_human_approval` (extra cleaner shift, notable cost), robot plan (`dispatch_robot` floor pre-cleaning early),
   prioritisation rule (arrivals with early-checkin requests and long stays first), proactive `notify_guest` to early-checkin
   requesters with realistic times.
3. On roster change known (sick) → re-plan via T05 planner, follow-up decision in the same episode with the new shortfall.
4. Close after the surge days.

## Interfaces
`plato40/detectors/s05_surge.py` (`SurgeAgent`, name `surge`), issue_type `occupancy_surge`, uses `plato40.planning`.

## Acceptance criteria
- Quickstart: **QS-S05-event-weekend** detected by 23 Jan 06:00Z, re-plan decision at/after 24 Jan 05:30Z (not before — no future
  knowledge), escalation for extra staff with approval, no invented capacity.
- Dev: ≥ 3 of the SURGE-* weak labels detected; ≤ 6 unmatched surge episodes per year.
- Tests `tests/test_surge.py` (incl. sick-known-time causality).

## Files to touch
`plato40/detectors/s05_surge.py`, `tests/test_surge.py`.

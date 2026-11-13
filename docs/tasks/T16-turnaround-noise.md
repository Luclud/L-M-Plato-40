# T16 — Turnaround: only real conflicts become `room_turnaround` incidents

**Lane:** B operations · **Priority:** P1 (do before the `pre-holdout` tag) · **Depends on:** T05 (merged) ·
**Score impact:** false alarms / alert spam (automated check), robustness (7), safe autonomy (10)

## Goal
Cut the `room_turnaround` episodes that are not real conflicts, without losing any S01 detection.
Today (main @ dba0be4) the turnaround agent opens **162 `room_turnaround` episodes on dev**; `eval/evaluate.py --data data/dev`
reports **137 of them as "not matching any label"** (quickstart: 15). Almost all are early check-in requests the plan could
meet anyway (142 episodes with the shape `schedule_cleaning → notify_guest → release_room`, no wait, no shortfall).
An answer to a request that we can meet is guest service, not an incident.

## Context
- Routine replies (late check-out, arrival delay, feasible early check-in without plan risk) already go out as
  `issue_type="guest_request"` with `episode_key=f"guest:{request_id}"` — reuse that path.
- S01 in the brief: early departure + early check-in request + tight cleaning shift → turnaround **conflict**.
  Detection must stay at **2/2 quickstart S01** and **24/24 dev `WAIT-*`**, each before the wait event.

## Suggested approach
1. Classify each early check-in / arrival at plan time (as known at *t*):
   - **feasible with slack** — planned clean end ≤ requested time − 30 min and no capacity shortfall that day
     → `guest_request` (notify with the realistic time; schedule the cleaning as today, but in the guest episode).
   - **conflict** — planned end > requested time, slack < 30 min, cleaning depends on an early checkout, a shortfall in the
     daily re-plan, a guest already waiting, on-call or reassign needed → `room_turnaround` episode (as today).
2. If a feasible request later becomes a conflict (sick cleaner known, checkout slips), open the `room_turnaround` episode
   at that moment with the new evidence (no future data).
3. Make the slack threshold a named constant with a one-line physical justification; no tuning to single incidents.

## Acceptance criteria
- Quickstart: QS-S01-R308-0108 and QS-S01-R207-0123 still detected in time; unmatched `room_turnaround` ≤ 5.
- Dev: all 24 `WAIT-*` still detected ≥ 1 h before the wait event; unmatched `room_turnaround` episodes ≤ 40.
- No change in the number of guests that get an answer (every early_checkin request still gets exactly one `notify_guest`).
- `validate.py` PASS on quickstart + dev, pytest green; paste before/after `evaluate.py` tables in the PR.

## Out of scope
Planner algorithm changes (`plato40/planning/housekeeping.py`) — T06 may be using it in parallel. If you must touch it, say so
in the team chat first.

## Files to touch
`plato40/detectors/s01_turnaround.py`, `tests/test_turnaround.py`.

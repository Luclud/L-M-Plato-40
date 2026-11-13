# T18 — Incidents list + incident story page

**Lane:** C · **Priority:** P0 · **Depends on:** T17 · **Parallel with:** T19, T20, T21 ·
**Score impact:** decision & action trail (7), scenario demo (8), limitations & verification (5)

## Goal
Implement `docs/FRONTEND_PLAN.md` §5.2 and §5.3: real incidents as cards, and one page per incident that tells the
4-step story. Use only `viewer/labels.py` / `viewer/components.py` for wording and chips.

## Do
- **List:** incident issue types only by default (`room_turnaround, water_leak, heating_comfort, ventilation_maintenance,
  occupancy_surge`); filter chips with counts, "needs approval" toggle, search by room/asset, sort (most severe | newest).
  Card: icon + friendly type + asset name + local date/time span + priority chip + autonomy status of the episode
  (strictest level in it) + headline (first decision's reason) + one-line "We: action → action → action".
  Collapsed section "Routine work handled automatically" with aggregates per routine type (guest_request, energy_optimization,
  logistics, data_quality) and a paginated list.
- **Story page** (`?episode=<id>` query param so it can be linked from Building, Approvals and the tour):
  header (friendly title, priority, strictest autonomy, local span, "detected X after it started" when labels exist,
  confidence word + number), then 1 What we saw (evidence chart(s) via `core` + new markers), 2 Why we think so (evidence
  detail text as bullets, look-alikes ruled out if present), 3 What we did (numbered `decision_step`s with local time, friendly
  action, resource name, autonomy chip, reason, guest message bubble, scheduled time, mock Approve/Reject for pending ones),
  4 How we'll know (verification plan + expected outcome; simulated effects with SIMULATED badge), `tech_details` at the end.
- Pagination: 20 cards per page (dev has ~100 incidents).

## Acceptance criteria
- Quickstart: all 7 labelled incidents reachable from the list in ≤ 2 clicks; story page shows all 4 steps.
- Dev: list loads < 1 s after cache; routine aggregates correct (counts equal decisions file).
- No raw codes outside Technical details. AppTest: list + one story page per incident type on quickstart and dev.
- PR with screenshots of list + R104 story + AHU1 story.

## Files to touch
`viewer/views/incidents.py`, `viewer/views/incident.py`, `tests/test_viewer_incidents.py`.

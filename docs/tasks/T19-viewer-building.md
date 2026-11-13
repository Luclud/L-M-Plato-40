# T19 — Building view (start page): floor plan at a chosen moment

**Lane:** C · **Priority:** P0 · **Depends on:** T17 · **Parallel with:** T18, T20, T21 ·
**Score impact:** scenario demo (8), cross-domain coordination (8)

## Goal
Implement `docs/FRONTEND_PLAN.md` §5.1: the first screen shows the building "live" at one 15-minute moment of the replay.

## Do
- `viewer/twin.py` (pure, cached per local day): room state at time t from `room_telemetry.csv`
  (`housekeeping_state`, `presence`; missing → unknown), open incident episodes per asset at t (first ≤ t ≤ last decision),
  plant status (HP1, AHU1, WM_MAIN, PV/battery: open incident or data gap), staff availability **as known at t**
  (`staff_roster.csv`, `status_known_utc ≤ t`), "today so far" routine counts.
- Planned cleanings: `schedule_cleaning` decisions with `scheduled_for_utc` later that local day → outlined tile with planned
  time + resource ("cleaning 11:45 · Cleaner 3"); actual state stays the filled colour.
- `viewer/views/building.py`: day picker + 15-min time slider (local time), 4 floors × 10 room tiles (colour by state, red dot
  for an open incident; legend), plant tiles, staff strip, "Needs attention now" cards (open incidents + pending approvals at t)
  linking to the story page (`?episode=`). Click a room → panel with that day's temperature/water chart and its incidents.
- Default moment: start of the highest-priority incident in the dataset (quickstart: R104 burst). Keep selection in the URL
  (`?t=2026-01-12T18:00Z`) so the tour (T22) can deep-link.
- Use `st.columns` / HTML-CSS grid for tiles; keep it fast (render < 1 s per time step on dev).

## Acceptance criteria
- Quickstart at the R104 burst: Room 104 shows the incident dot, "Needs attention now" lists the leak; at 24 Jan 06:00 local
  the staff strip shows Cleaner 1 sick only from its `status_known_utc` on.
- Rooms with missing telemetry show "no data", never "vacant clean".
- AppTest on quickstart, dev, empty decisions. PR with screenshots at 3 moments.

## Files to touch
`viewer/twin.py`, `viewer/views/building.py`, `tests/test_viewer_building.py`.

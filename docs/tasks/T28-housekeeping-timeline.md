# T28 — Housekeeping & robot timeline (Gantt per day)

**Lane:** C presentation · **Priority:** P1 · **Depends on:** viewer on main (T17–T25) · **Viewer only — the pipeline is
frozen** · **Score impact:** cross-domain coordination of staff, robots (8), prioritisation & constraints (8), demo (8)

## Goal
One picture per day that shows who cleans which room when, where the deadlines are, and how Steward re-planned when
someone called in sick. Today this lives only in decision text; S01 (turnaround) and S05 (event weekend) become
visible at a glance. Everything is read from existing outputs — no new pipeline fields.

## Data (all already there)
| What | Source | Notes |
|---|---|---|
| Shifts | `staff_roster.csv`: `resource_id, role, start_utc, end_utc, status, status_known_utc` | `status` ≠ scheduled (sick, vacation, out_of_service) = absence |
| Actual cleanings | `events.jsonl`: `cleaning_started` / `cleaning_completed` (`room_id`, `resource_id`) | pair per room + resource in time order |
| Actual robot runs | `robot_floor_cleaning_started` / `_completed` | same pairing |
| On-call | `oncall_housekeeping_called` events + `call_oncall_housekeeper` decisions | |
| Planned work | decisions `schedule_cleaning`, `dispatch_robot`, `call_oncall_housekeeper`: `affected_assets`, `resource_id`, `scheduled_for_utc`, `episode_id`, `reason` | planned window = regex `(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2}) local` in `reason` (e.g. "plan: CLEANER_1 11:00-12:00 local"); on-call: "on site ~16:45, room clean ~17:30"; fallback = `scheduled_for_utc` + median actual duration for that room size seen **before** that moment. Checked on quickstart: 14 of 26 plan decisions carry a window; the rest are **guest-service tasks** ("Guest request GR… (extra_towels) for R209: CLEANER_1 on shift at 09:45 local; handed over at the door") → short task block (15 min) at `scheduled_for_utc`, labelled "Towels · Room 209" / "Cleaning request · Room 410"; and **surge robot plans** ("Surge day …: robot floor pre-cleaning … (11:00 local on)") → a bracket "robot pre-cleans vacated rooms from 11:00" on that robot's lane, the actual runs come from events |
| Re-plans | decisions whose `reason` starts with "Re-plan" or contains "daily re-plan" | vertical markers |
| Deadlines | check-in 15:00 local per arrival (`bookings.csv`), early check-in requested time (`guest_requests.csv` `early_checkin`, parsed with `plato40.messaging.parse_requested_time`, import only), `checkout` / `checkout_early` events, `arrival_waiting_room` (guest waiting), `release_room` decisions | |

## Do
1. **`viewer/schedule.py`** (pure functions, cached per local day, unit-testable, no Streamlit):
   `shifts(day)`, `absences(day)` (with `known_at`), `actual_blocks(day)`, `planned_blocks(day, as_of=None)` (latest
   plan per room/resource as known at `as_of`; earlier superseded plans kept with `superseded=True`), `deadlines(day)`,
   `replans(day)`, `day_summary(day)` (rooms to turn over, planned vs available cleaner minutes, guests waiting,
   on-call calls). Times in Europe/Vienna for display, UTC inside.
2. **`viewer/views/housekeeping.py`** — new page **"Housekeeping"** (router entry after Building, icon
   `:material/cleaning_services:`), shares the day/time with Building via `?t=`:
   - Plotly timeline, one lane per resource in this order: Cleaner 1–3, Robot 1–2, On-call housekeeper
     (friendly names from `viewer/labels.py`); x = 06:00–22:00 local.
   - Shift = light background bar; absence = red hatched bar labelled "sick — known 06:15" with a small marker at
     `known_at`; actual cleaning = filled block labelled with the room ("Room 308"); robot run = filled block in the
     robot colour; planned block = outlined block (dashed) with the planned room; superseded plan = faint outline,
     shown only with the toggle **"Show plan changes"**.
   - Deadline row on top: check-in 15:00 line, diamonds for early check-in requests at the requested time, red marker
     for "guest waiting", green tick for "room released".
   - Vertical lines: the selected moment (from `?t=`) and every re-plan, labelled with its short reason
     ("Cleaner 1 sick became known — re-planned").
   - Toggle **"Only what was known at the selected time"** (default on): hides actual blocks after `t` and shows the
     plan as known at `t` — consistent with the no-future rule; off = the full day as it happened.
   - Hover: resource, room, local start–end, planned/actual, linked incident; a list under the chart
     ("Today's turnovers") with one row per room and an **Open** link to the incident story (`?episode=`).
   - Summary strip above the chart from `day_summary` (e.g. "16 rooms to turn over · 1,860 min of cleaning vs
     1,530 min of staff · 1 guest waited · on-call called once").
   - Empty states in plain words (no shifts that day, no data), never a stack trace.
3. **Incident story embed:** for `room_turnaround` and `occupancy_surge` incidents, add a section "The day's plan"
   between "What we did" and "How we'll know" that renders the same chart for that day, highlighting the incident's
   room(s) and resource(s). Shared function, no copy of the chart code.
4. **Docs:** one paragraph + screenshot in `docs/DEMO.md` (Housekeeping on Thu 8 Jan and Sat 24 Jan), README Viewer
   list, `docs/img/viewer_housekeeping.png`.

## Acceptance criteria (quickstart unless noted)
- **Thu 8 Jan** (R308 early check-in): Cleaner 1 shows the sick absence with its known time; the re-plan marker sits at
  that moment, not before; Robot 2 pre-cleans R308 before Cleaner 3; R308 released before the guest's check-in; the
  "only what was known" toggle at 06:00 local shows no actual blocks after 06:00.
- **Sat 24 Jan** (event weekend): the sick cleaner, the re-plan, robot runs and both on-call calls appear; the summary
  shows demand above capacity.
- Every block's resource was on shift (absences never carry work blocks) — a unit test asserts this on quickstart.
- Dev: any day renders in < 1 s after cache; a day without data shows the empty state.
- `tests/test_viewer_schedule.py`: pairing, absence + known_at, plan-window regex (incl. on-call wording), as-of
  filtering, superseded plans; AppTest smoke test of the page on quickstart, dev and an empty decisions file; the
  turnaround story page renders the embedded chart. pytest green (viewer tests skip without streamlit).
- No change outside the files below; pipeline output unchanged. PR: screenshots of both days (toggle on and off).

## Files to touch
`viewer/schedule.py`, `viewer/views/housekeeping.py`, `viewer/app.py` (one router entry), `viewer/views/incident.py`
(embed section only), `viewer/labels.py` (labels if needed), `tests/test_viewer_schedule.py`, `docs/DEMO.md`,
`docs/img/viewer_housekeeping.png`, README Viewer section.

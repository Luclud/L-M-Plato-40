# Frontend plan — from developer viewer to an operator & jury tool

Status: **draft, Fri 13 Nov 21:15** — re-check after the runtime changes land (see §9). Tickets: T17–T23.

## 1. Problem
The T10 viewer works but is built for developers, not for an operator or a jury that has 30 seconds per screen:
- Sidebar shows file paths; first screen shows validator output and an eval table.
- Raw codes everywhere: `water_leak`, `EP-00001`, `D000002`, `request_inspection`, `approval_and_consent`, UTC timestamps.
- Charts: decisions are dotted vertical lines with rotated text that pile up on top of each other.
- No hierarchy: on dev there are ~2,100 episodes, but only **~103 are real incidents** (64 turnaround, 20 heating, 9 surge,
  6 water, 4 ventilation). The rest is routine work: 1,577 guest replies, 362 daily energy plans, 25 logistics, 53 data-quality.
  Timeline and approval inbox drown in it (146 approval decisions in 132 episodes).
- No story: the jury has to assemble "what happened → why → what we did → how we check" themselves.
- Level 3 insights (15 points) are not in the viewer at all.

## 2. Goal and test
**Anyone understands each screen in 5 seconds and reaches any hero incident in ≤ 3 clicks from the start screen.**
The live demo (15:00–16:00) and the 3-minute video run through the built-in guided tour without typing anything.

## 3. Principles
1. **Plain language first, codes second.** Main UI shows friendly names, local time (Europe/Vienna), room names.
   Ids, UTC, raw evidence strings and JSON live in a collapsed "Technical details" box on every card.
2. **Incidents are the product; routine work is a number.** Default views show only incident types; routine work is shown as
   aggregates ("1,577 guest requests answered in 6 languages") with a drill-down.
3. **Every incident is a 4-step story:** What we saw → Why we think so → What we did → How we'll know it worked.
4. **Honesty labels stay, but readable:** "Proposed message — not sent", "Simulated", "Mock approval — nothing is executed",
   "Guest consent needed — not granted". Never remove them.
5. **One look:** one theme, cards (`st.container(border=True)`), status chips with one colour code, Material icons, no raw tables
   outside the Proof page.
6. **Same code for every dataset:** quickstart, dev and holdout. Heroes come from labels when present, otherwise auto-picked.

## 4. Information architecture (top navigation, `st.navigation(position="top")`)
| Page | Purpose | Ticket |
|---|---|---|
| **Building** (start page) | Live floor plan at a chosen moment: rooms, plant, staff, what needs attention now | T19 |
| **Incidents** | List of real incidents as cards → incident story page | T18 |
| **Approvals (n)** | What a human must decide, grouped per incident, mock Approve/Reject | T20 |
| **What we learned** | Level 3: PLATO and CLARA recommendations + target architecture | T21 |
| **Proof** | Trust: validator, detection vs labels, BMS vs us, data quality, limitations, downloads | T21 |
| **▶ Guided demo** (button, top right + on Building) | 8-step tour for the live demo / video | T22 |

Dataset picker (Quickstart · Dev year · Holdout · Custom…) in the top bar or a small popover; file paths only under "Custom".
Streamlit toolbar minimal (no "Deploy" button), wide layout.

## 5. Screens (wireframes)

### 5.1 Building (start page)
```
[Dataset: Quickstart ▾]   Mon 12 Jan 2026  [◀ day ▶]  time ━━━━●━━━━ 19:30      [▶ Guided demo]
┌ Needs attention now ───────────────────────────────────────────────────────────────┐
│ 💧 Water leak · Room 104 · since 19:00 · waiting for approval  [Open]               │
│ 🌀 Ventilation filter · AHU1 · technician sick, contractor requested  [Open]        │
└────────────────────────────────────────────────────────────────────────────────────┘
Floor 4  [401][402][403]...[410]     legend: ■ occupied ■ vacant clean ■ needs cleaning
Floor 3  [301][302]...                       ■ cleaning ■ no data (unknown)  ● incident
Floor 2  ...
Floor 1  [101][102][103][104●]...
Plant:  [Heat pump HP1 ✓] [Ventilation AHU1 ⚠] [Water main ✓] [PV / battery ✓]
Staff today (as known at 19:30): Cleaner 1 ✓ · Cleaner 2 sick (known 05:15) · Robot 2 ✓ · On-call 15:30–22:00
Today so far: 14 guest replies · 1 energy plan (simulated) · 3 cleanings scheduled
```
- Room tile colour from `room_telemetry.housekeeping_state` + `presence` at the selected 15-min step; grey = missing data.
- Red dot = an incident episode for that room is open at that time (first ≤ t ≤ last decision). Click tile → room panel
  (today's temperature + water chart, incidents of this room).
- Staff strip from `staff_roster.csv` **as known at t** (`status_known_utc ≤ t`), consistent with the no-future story.
- Default moment: start of the first critical incident (quickstart: R104 burst), so the first screen already tells a story.

### 5.2 Incidents
```
Filter: [All] [🛏 Room turnaround 64] [💧 Water leak 6] [🌡 Heating 20] [🌀 Ventilation 4] [📈 Busy weekend 9]
        [Needs approval ☐]  Search room/asset [____]   Sort [Most severe ▾]
┌───────────────────────────────────────────────────────────────────────────────────┐
│ 💧 Water leak · Room 104 · Mon 12 Jan, 19:00–19:30          CRITICAL  Needs approval + consent │
│ "Continuous high flow ~27 L per 15 min, no shower pattern → likely burst hose"     │
│ We: flagged it → asked for an inspection → proposed water shut-off (needs approval)│
└───────────────────────────────────────────────────────────────────────────────────┘
▸ Routine work handled automatically: 1,577 guest replies · 362 energy plans (simulated) · 25 logistics · 53 data issues
```
### 5.3 Incident story (one incident)
```
💧 Water leak in Room 104                                  CRITICAL · Needs approval + guest consent
Mon 12 Jan 19:00 – 19:30 · detected 45 min after it started · confidence high (0.8)

1 What we saw     [chart: room water L/15 min, incident period shaded, ① ② ③ markers on the line, data gaps grey]
2 Why we think so • Room meter: ~27 L per 15 min since 18:00, normally 0 at night
                  • No shower pattern (no pauses) · guest not reporting yet · BMS alarm only hours later
3 What we did     ① 19:00 Flagged a problem                     Done automatically
                  ② 19:15 Asked for an inspection (plumber)     Needs approval          [Approve] [Reject]
                  ③ 19:30 Proposed water shut-off for Room 104  Needs approval + guest consent
                     💬 Message to guest (DE) — proposed, not sent: "Guten Abend, ..."
4 How we'll know  Flow back to night baseline within 1 h after shut-off; else escalate to contractor.
                  Expected: ~X L avoided (SIMULATED)
▸ Technical details (episode id, decision ids, UTC, evidence strings, raw JSON)
```
### 5.4 Approvals
One card per incident (not per decision), most urgent first, paginated (20/page), filter Pending / Decided.
Card = plain question built from action + asset + time ("Send the technician to the ventilation unit on Fri 16 Jan 08:00?"),
one-line why, cost/risk hint (`notable cost`), consent banner, buttons Approve / Reject (mock → `out/approvals.json`), link
"Open incident". Nav badge shows pending count.

### 5.5 What we learned
Two columns **For the next PLATO building** / **For CLARA next year**; one card per `insights.jsonl` row: topic, finding
(first sentence, "show full evidence" expander), recommendation, expected benefit (labelled estimate), confidence chip.
Below: target architecture (render the `insights.md` section + a simple diagram) and Limitations.

### 5.6 Proof
Validator result (run on button click, cached), detection vs labels (friendly table, only when labels exist, "dev aid —
labels are not ground truth"), BMS vs us (T12 output: "BMS raised N water alarms, we detected M leaks"), data quality
summary + gateway outages, known misses/limitations, download buttons for `decisions.jsonl` and `insights.md`.

### 5.7 Guided demo (≈ 3 min)
Stepper with Back / Next, step dots, a 2-line talking-point box on top of the real component:
1. Building at a glance (the moment of the R104 burst) · 2. Water leak (S02) · 3. Heat pump in a cold snap (S03) ·
4. Early check-in vs tight cleaning (S01) · 5. Event weekend + sick cleaner (S05) → approve the staffing request ·
6. Filter vs data gap (S04) · 7. What we learned (1 PLATO + 1 CLARA card) · 8. Proof (validator PASS, detection, BMS vs us).
Heroes: quickstart via `eval/labels_quickstart.json` (viewer may read eval/, the pipeline may not); dev/holdout: highest-priority
episode per scenario class. Talking points in `viewer/tour.toml` so humans can edit them without code.

## 6. Shared building blocks (T17)
- `viewer/labels.py` — single dictionary for friendly names + Material icons: issue types, actions (incl. extra actions),
  autonomy levels, priorities, resources (`CLEANER_1` → "Cleaner 1", `ONCALL_1` → "On-call housekeeper"), assets
  (`R104` → "Room 104", `HP1` → "Heat pump", `AHU1` → "Ventilation unit", `WM_MAIN` → "Main water meter"), `APP_NAME`
  constant (product name still to be chosen — must not be PLATO/CLARA).
  Autonomy chips: **Done automatically** (green) · **Needs approval** (amber) · **Needs approval + guest consent** (red).
- `viewer/components.py` — `status_chip`, `incident_card`, `decision_step`, `message_bubble` (language flag + "proposed — not
  sent"), `simulated_badge`, `tech_details(obj)`, `local_time(ts)` ("Mon 12 Jan, 19:30"), `headline(reason)` (first clause,
  ≤ 120 chars).
- Chart fix in `core.figure`: replace dotted vertical lines + rotated text with numbered markers on the series at decision
  times (hover = action + time), shade incident period, grey data gaps, local-time axis, friendly y-axis units.
- `viewer/.streamlit/config.toml` (or repo-root `.streamlit/`): theme, `client.toolbarMode = "minimal"`.
- `viewer/app.py` becomes a thin router; pages in `viewer/views/*.py` (one file per page → tickets can run in parallel).

## 7. Tickets and order
| Ticket | What | Files | Depends | Parallel |
|---|---|---|---|---|
| **T17** | Foundation: router + top nav, labels, components, theme, dataset picker, chart marker fix; move the existing screens into `views/` unchanged so nothing is lost | `viewer/app.py`, `viewer/labels.py`, `viewer/components.py`, `viewer/core.py` (figure only), `viewer/views/*` (moved), `.streamlit/config.toml`, `tests/test_viewer_labels.py` | — | **alone, first** |
| **T18** | Incidents list + incident story page | `viewer/views/incidents.py`, `viewer/views/incident.py`, `tests/test_viewer_incidents.py` | T17 | yes |
| **T19** | Building view (floor plan, time scrubber, plant, staff, needs-attention) | `viewer/views/building.py`, `viewer/twin.py`, `tests/test_viewer_building.py` | T17 | yes |
| **T20** | Approvals grouped per incident | `viewer/views/approvals.py`, `tests/test_viewer_approvals.py` | T17 | yes |
| **T21** | What we learned + Proof | `viewer/views/learned.py`, `viewer/views/proof.py`, `tests/test_viewer_learned_proof.py` | T17 | yes |
| **T22** | Guided demo tour | `viewer/views/tour.py`, `viewer/tour.toml`, `tests/test_viewer_tour.py` | T18–T21 | after |
| **T23** | Polish + docs: remove old pages, DEMO.md rewrite (tour = script), screenshots, README "Viewer", Docker check, perf | `docs/DEMO.md`, `docs/img/*`, README Viewer section, `viewer/*` small fixes | T22 | last |

Estimated with Devin: T17 ~45 min, T18–T21 in parallel ~45–60 min, T22 ~30 min, T23 ~30 min → done in ~3 h.

## 8. Acceptance criteria (all tickets)
- Works on quickstart, dev and an empty decisions file; Streamlit `AppTest` smoke test per page for all three.
- Dev loads in < 5 s after the first (cached) load; switching pages < 1 s; no subprocess runs on page load (validator only on
  button click, cached).
- No raw code (issue_type, action id, episode/decision id, UTC, file path) outside "Technical details", Proof and Custom dataset.
  Test: every `ISSUE_TYPES` and `ALL_ACTIONS` entry (import from `plato40.decision`) has a label; every resource and asset in
  `building.json` has a label.
- All honesty labels present (proposed / simulated / mock / consent needed ≠ granted).
- The viewer stays read-only over pipeline outputs; it never changes `plato40/`, `run.py`, `validate.py`, `eval/`.
- Each PR: screenshots of the changed pages in the PR description.

## 9. Open points to re-check (after the runtime changes)
- Decision fields / counts may change with the runtime work → re-check §1 numbers, field names used by components, load time.
- Product name (`APP_NAME`) — humans decide.
- Whether the Building view should use the real `housekeeping_state` or the pipeline's own plan (`schedule_cleaning`) for future
  slots — proposal: real state for "now", planned cleanings as outlined tiles.

# T32 — Final demo video: every tab, Holdout year, subtitles only

**Lane:** C presentation · **Priority:** P0 · **Depends on:** T27 (`scripts/make_video.py`), viewer on `main` (PR #64–#68) ·
**Score impact:** demo & presentation; the submission needs a video of at most 3:00

## Goal
One command renders `out/video/steward_final.mp4`: about 2:55, every viewer tab once, on the **Holdout** year, no voice,
burned-in subtitles that say what happens on screen. The subtitle text and order are fixed below: copy them verbatim.
Source of the script: the doc "Steward demo video script (2:50)" (Luca).

## Command (the result of this ticket)
```bash
pip install -r viewer/requirements.txt -r video/requirements.txt && python -m playwright install chromium   # + ffmpeg
python scripts/make_video.py --storyboard video/storyboard_final.yaml --cut final --voice none
# -> out/video/steward_final.mp4, final.srt, final_contact_sheet.png
```

## Context
- `scripts/make_video.py` (T27) already records the real viewer scene by scene with Playwright, burns in captions
  from an SRT and assembles with ffmpeg. Read `video/README.md` first. Reuse it; do not write a second recorder.
- The old `video/storyboard.yaml` uses the Guided demo (`/tour`). That tab can be switched off
  (`STEWARD_GUIDED_DEMO=0`), so the new storyboard uses the **real pages** with deep links instead.
- Page URLs: Building `/`, Housekeeping `/housekeeping`, Incidents `/incidents` (`?episode=<id>` opens the incident
  story), Approvals `/approvals`, What we learned `/learned`, Ask the building `/ask`, Proof `/proof`.
  `?t=<UTC, e.g. 2026-03-09T16:30Z>` sets the selected moment on Building and Housekeeping.
- The viewer opens on the dataset named by `PLATO40_DEFAULT_DATASET` (default `Holdout`).

## Holdout data (read this before you start)
`data/holdout/` is git-ignored and not in your checkout. `decisions_holdout.jsonl` (repo root) is committed.
- If `data/holdout/` is missing: **ask Luca for the folder** (he has it unpacked). Never commit it.
- Until you have it, develop and test against the dev year (`--decisions decisions.jsonl --data data/dev` via a
  temporary copy of the storyboard), but the final render must be on Holdout.
- **Never regenerate or overwrite `decisions_holdout.jsonl`** (frozen, organizer rule). The final storyboard sets
  `run_pipeline: false`; make `make_video.py` skip its `python run.py …` step when that flag is false.

## Do
1. Copy the storyboard below to `video/storyboard_final.yaml` (new file; leave `video/storyboard.yaml` and its tests alone).
2. Extend `scripts/make_video.py` (backwards compatible, the old storyboard must still render):
   - `--cut` accepts any cut name defined in the storyboard (not only `short` / `submission`).
   - `viewer.run_pipeline: false` → skip the pipeline run; use `viewer.decisions` / `viewer.data` as they are.
     Start the viewer with `PLATO40_DEFAULT_DATASET=off` so the `--decisions/--data` arguments decide the dataset,
     and with `STEWARD_GUIDED_DEMO=0`.
   - Scene field `subtitles: [{at: <s from scene start>, text: "…"}]`: when present, these lines are the captions for
     that scene, verbatim, each shown from `at` until the next line's `at` (the last one until the scene ends). With
     `--voice none` no TTS runs. Lines already fit 2 × 42 characters; if one doesn't, wrap it, never shorten it.
   - New actions (same style as the existing ones, all with the visible cursor dot):
     `click_text: "<visible text>"` (click a tab, pill, toggle label, expander title, room tile, link or button by its
     text) · `click_key: "<streamlit key>"` (click the element inside `.st-key-<key>`, e.g. `ask_suggest_0`,
     `tile_R202`) · `type_text: {into: "<placeholder or label>", text: "…", submit: true|false}` (type visibly,
     ~25 chars/s; `submit` presses Enter) · `scroll_by: <px>`.
   - `click_button_near_text` on Approvals: after the click, highlight the line containing `(mock)` (the Approvals page
     shows "Approved (mock), nothing was executed", not "recorded:").
   - Title card: change `TITLE` to `Steward · Runs the building, answers to people` (no em dash) and keep `TITLE_SUB`.
3. Render, then watch the whole video once. Fix every `WARN` the script prints (a missed selector = wrong video).
4. Check the facts on screen (table at the end). If one does not match, remove only the number from that subtitle and
   say so in the PR.
5. Add a row to README "Changes after pre-holdout" (files touched, "None — demo tooling, pipeline untouched").

## Storyboard (copy verbatim to `video/storyboard_final.yaml`)
```yaml
# Final submission video (T32): real pages, Holdout year, subtitles only (no voice).
viewer:
  base_url: http://localhost:8501
  decisions: decisions_holdout.jsonl   # committed holdout run; never regenerate it here
  data: data/holdout
  run_pipeline: false
  viewport: {width: 1920, height: 1080}
  zoom: 1.15
  approvals_file: out/video_approvals.json   # deleted before recording: every approval pending on camera
cuts:
  final: [building, incident, incidents, housekeeping, approvals, ask, learned, proof, outro]
placeholders: {}
scenes:
  - id: building
    url: "/?t=2026-03-09T16:30Z"          # Mon 9 Mar, 17:30 local
    seconds: 30
    actions:
      - wait_for_text: "Needs attention now"
      - hover: header "Holdout"
      - highlight_text: "Needs attention now"
      - scroll_to_text: "Floor 2"
      - hover: side panel "Plant"
      - click_key: tile_R202
      - wait_for_text: "Water use"
      - scroll_to_text: "Water use"
    subtitles:
      - {at: 0,  text: "Steward runs PLATO-40, a fictional 40-room self-check-in hotel."}
      - {at: 5,  text: "This is the unseen holdout year, run with code frozen before its release."}
      - {at: 10, text: "The Building page shows the hotel at one moment, using only what was known then."}
      - {at: 15, text: "Open incidents come first. Here: a water leak in Room 202."}
      - {at: 20, text: "Every room, the heating and ventilation plant, and who is on shift."}
      - {at: 25, text: "Clicking a room shows its day. The water meter never stops."}
  - id: incident
    url: "/incidents?episode=WL-00322"
    seconds: 32
    actions:
      - wait_for_text: "What we saw"
      - pause: 4
      - scroll_to_text: "What we saw"
      - pause: 3
      - scroll_to_text: "Why we think so"
      - pause: 3
      - scroll_to_text: "What we did"
      - pause: 3
      - highlight_text: "Shut off"
      - scroll_to_text: "How we'll know it worked"
    subtitles:
      - {at: 0,  text: "Every incident opens as a story: what we saw, why, what we did, how we check."}
      - {at: 5,  text: "About 28 litres every 15 minutes for an hour, in an empty room."}
      - {at: 10, text: "Showers, cleaning and meter resets are ruled out first."}
      - {at: 15, text: "At 17:15 Steward flags the leak and asks for an inspection."}
      - {at: 21, text: "An hour later water still runs, so it proposes shutting it off. That needs a manager and the guest."}
      - {at: 27, text: "The meter confirms the repair, and the incident closes on 11 March."}
  - id: incidents
    url: "/incidents"
    seconds: 16
    actions:
      - wait_for_text: "Most severe"
      - click_text: "Water leak"
      - click_text: "Needs approval"
      - type_text: {into: "Search room or asset", text: "202", submit: true}
      - pause: 1
      - click_text: "Routine work handled automatically"
    subtitles:
      - {at: 0,  text: "All 168 incidents of the year, most severe first."}
      - {at: 5,  text: "Filter by type, by what needs approval, or search a room."}
      - {at: 10, text: "Routine work runs on its own: 1,920 proposed guest replies in 6 languages, 361 simulated energy plans."}
  - id: housekeeping
    urls: ["/housekeeping?t=2026-04-24T04:00Z", "/housekeeping?t=2026-04-24T04:30Z"]   # 06:00 and 06:30 local
    seconds: 22
    actions:
      - wait_for_text: "Today's turnovers"
      - pause: 4
      - next_url_at_seconds: 6
      - wait_for_text: "Today's turnovers"
      - pause: 4
      - click_text: "Show plan changes"
      - pause: 3
      - click_text: "Only what was known at the selected time"
    subtitles:
      - {at: 0,  text: "Housekeeping shows who cleans which room, and every check-in deadline."}
      - {at: 6,  text: "At 06:15 a cleaner calls in sick. Steward re-plans with the staff it still has."}
      - {at: 12, text: "The old plan stays visible, so every change can be traced."}
      - {at: 17, text: "Switch this off to see how the day really went."}
  - id: approvals
    url: "/approvals"
    seconds: 15
    actions:
      - wait_for_text: "Pending"
      - pause: 3
      - click_button_near_text: {button: "Approve", text: "Fri 24 Apr"}
    subtitles:
      - {at: 0,  text: "Anything costly or risky waits for a manager here."}
      - {at: 5,  text: "Here: an extra cleaner for 24 April, because one called in sick."}
      - {at: 11, text: "In this prototype approving is a mock. Nothing is executed."}
  - id: ask
    url: "/ask"
    seconds: 17
    actions:
      - wait_for_text: "What would you like to know?"
      - click_key: ask_suggest_0
      - pause: 3
      - type_text: {into: "Ask about a room", text: "Show the heat pump on 26 Jan", submit: true}
      - pause: 3
      - scroll_by: 400
    subtitles:
      - {at: 0,  text: "Ask the building answers from the decision log and the building data."}
      - {at: 6,  text: "Every answer cites its decisions. Measurements come with a chart."}
      - {at: 13, text: "It is read-only: it cannot approve, decide or change anything."}
  - id: learned
    url: "/learned"
    seconds: 15
    actions:
      - wait_for_text: "For CLARA next year"
      - highlight_text: "For the next PLATO building"
      - highlight_text: "For CLARA next year"
      - click_text: "Show full evidence"
    subtitles:
      - {at: 0,  text: "After a year, Steward turns operations into lessons."}
      - {at: 5,  text: "Left: design advice for the next PLATO building. Right: operating rules for CLARA."}
      - {at: 10, text: "Each lesson shows how it was checked and how sure we are."}
  - id: proof
    url: "/proof"
    seconds: 18
    actions:
      - wait_for_text: "Safety & contract check"
      - click_button: "Run check"
      - wait_for_text: "PASS"
      - pause: 3
      - scroll_to_text: "BMS vs us"
      - scroll_to_text: "Data quality"
      - scroll_to_text: "Downloads"
    subtitles:
      - {at: 0,  text: "Proof runs the independent validator on every decision."}
      - {at: 5,  text: "PASS: 2,775 decisions, zero safety violations."}
      - {at: 10, text: "It compares itself with the building's own alarms and lists every data gap."}
      - {at: 15, text: "All decisions and lessons can be downloaded."}
  - id: outro
    url: "/?t=2026-03-09T16:30Z"
    seconds: 5
    actions:
      - wait_for_text: "Needs attention now"
    subtitles:
      - {at: 0, text: "Steward. Runs the building, answers to people."}
```
Total: 170 s of scenes + 1.5 s title + fades ≈ 2:55 (must stay ≤ 3:00; the script already fails if longer).

## Facts to check on screen (Holdout)
| Subtitle | Expected on screen |
|---|---|
| "All 168 incidents" | Incidents filter pill shows `All 168` |
| "1,920 proposed guest replies in 6 languages, 361 simulated energy plans" | Routine work expander title |
| Water leak Room 202, 17:15, closes 11 March | Incident WL-00322 story (flag + inspection Mon 9 Mar 17:15, shut-off proposed 18:15, closed Wed 11 Mar) |
| Cleaner sick at 06:15 on 24 April | Housekeeping 24.04.2026: absence marker "known 06:15", re-plan line |
| "PASS: 2,775 decisions, zero safety violations" | Proof shows PASS with 0 violations; `wc -l decisions_holdout.jsonl` = 2775 |

## Acceptance criteria
- `out/video/steward_final.mp4` exists, ≤ 3:00, 1920×1080, all 9 scenes in order, subtitles exactly as above.
- The script prints no `WARN` for the final cut; the contact sheet shows the right page in every scene.
- The Guided demo tab is not visible anywhere; every approval is pending at the start of the Approvals scene.
- `decisions_holdout.jsonl`, `plato40/`, `run.py`, `validate.py`, `analysis/`, `insights.*` unchanged (`git diff --stat`).
- `python -m pytest -q tests/test_storyboard.py` still green (old storyboard untouched); add a test that
  `video/storyboard_final.yaml` loads, every scene has `subtitles` sorted by `at` < `seconds`, and the cut is ≤ 180 s.
- The mp4 files are **not** committed (they live in `out/`, git-ignored). Attach the mp4 to the PR / send it to Luca.

## Out of scope
Voice-over, music, new viewer features, any change to the viewer's look.

## Files to touch
`video/storyboard_final.yaml` (new) · `scripts/make_video.py` (demo tooling only) · `video/README.md` (one section:
"Final cut") · `tests/test_storyboard_final.py` (new) · `README.md` (row in "Changes after pre-holdout").

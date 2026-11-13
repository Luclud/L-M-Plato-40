# T22 — Guided demo tour (live demo + video)

**Lane:** C · **Priority:** P0 · **Depends on:** T18, T19, T20, T21 · **Score impact:** demo & presentation (20)

## Goal
Implement `docs/FRONTEND_PLAN.md` §5.7: an 8-step tour that runs the whole demo in ≈ 3 minutes with Back/Next only.

## Do
- `viewer/views/tour.py`: step dots, Back / Next, 2-line talking-point box above the real component of each step (reuse the
  page functions from T18–T21 with a fixed episode / moment). Steps: Building at the R104 burst → water leak story → heat pump
  cold snap → early check-in vs tight cleaning (S01) → event weekend + sick cleaner (S05) incl. approving the staffing request →
  filter vs data gap (S04) → What we learned (1 PLATO + 1 CLARA card) → Proof (validator PASS, detection, BMS vs us).
- Hero selection: quickstart via `eval/labels_quickstart.json` matching (existing `core.hero_presets` / `match_episodes`);
  dev / holdout: highest-priority episode per scenario class; a step without a match is skipped with a note.
- Talking points in `viewer/tour.toml` (editable by humans, no code), one entry per step, ≤ 2 sentences.
- Keyboard-free: works with mouse only; deep links `?tour=3`.

## Acceptance criteria
- Quickstart: all 8 steps show the right incident; dev and an empty file: tour runs and skips gracefully.
- A full click-through takes < 3 min at a normal pace (time it, note it in the PR).
- AppTest walks all steps on quickstart and dev. PR with a GIF or screenshots of every step.

## Files to touch
`viewer/views/tour.py`, `viewer/tour.toml`, `tests/test_viewer_tour.py`.

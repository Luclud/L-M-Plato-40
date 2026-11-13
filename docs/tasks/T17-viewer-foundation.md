# T17 — Viewer foundation: navigation, plain-language labels, components, theme

**Lane:** C presentation · **Priority:** P0 · **Depends on:** none · **Must merge before T18–T21** ·
**Score impact:** scenario demo & visualisation (8), decision & action trail (7)

## Goal
Turn `viewer/` into a multipage app with a top navigation and shared building blocks, so T18–T21 can build pages in
parallel in separate files. Read `docs/FRONTEND_PLAN.md` §3, §4, §6 first — this ticket implements §6.

## Do
1. `viewer/app.py` → thin router with `st.navigation([...], position="top")`: Building, Incidents, Approvals (n),
   What we learned, Proof, plus a "▶ Guided demo" entry. Until T18–T22 land, each new page may show the moved old screen.
2. Move every existing screen (Overview, Timeline, Incident card, Approval inbox, Data quality, Hero scenarios) into
   `viewer/views/*.py` **unchanged in behaviour**, reachable under Proof → "Classic views" until T23 removes them.
3. `viewer/labels.py`: friendly names + Material icons (`:material/water_drop:` style) for all `plato40.decision.ISSUE_TYPES`,
   `ALL_ACTIONS`, autonomy levels, priorities, resources (from `building.json` staff), assets (rooms, HP1, AHU1, PV1, meters),
   and `APP_NAME = "PLATO-40 Operations"` (placeholder; humans will rename — must not be PLATO/CLARA as product name).
   Helpers `label(kind, code)` that fall back to the code, never crash.
4. `viewer/components.py`: `status_chip(autonomy)`, `priority_chip`, `incident_card(episode_df)`, `decision_step(row, n)`,
   `message_bubble(msg)` ("Proposed — not sent"), `simulated_badge(value)`, `tech_details(obj)` (collapsed expander with ids,
   UTC, raw evidence, JSON), `local_time(ts)` → "Mon 12 Jan, 19:30", `headline(reason)` (first clause, ≤ 120 chars).
5. `core.figure`: replace dotted vertical lines with rotated text by numbered markers on the series at decision times
   (hover: friendly action + local time), shade the episode period, keep grey data gaps, local-time x-axis.
6. Dataset picker (Quickstart · Dev year · Holdout · Custom…): auto-discover pairs `out/*.jsonl` ↔ `data/<profile>` via the
   data manifest; file path inputs only under Custom. Keep the existing CLI args working (`-- --decisions ... --data ...`).
7. `.streamlit/config.toml` at repo root (Streamlit reads it from the working dir): light theme, `client.toolbarMode = "minimal"`,
   `browser.gatherUsageStats = false`, `server.headless = true`.

## Acceptance criteria
- `streamlit run viewer/app.py -- --decisions out/qs.jsonl --data data/quickstart` opens with the top nav, no sidebar paths,
  no Deploy button; every old screen still reachable.
- `tests/test_viewer_labels.py`: every ISSUE_TYPE, ALL_ACTIONS entry, resource and asset has a label; `label()` falls back.
- AppTest smoke test: every page renders on quickstart, dev and an empty decisions file without exceptions.
- No change outside `viewer/`, `.streamlit/`, tests. PR has screenshots.

## Files to touch
`viewer/app.py`, `viewer/labels.py`, `viewer/components.py`, `viewer/core.py` (figure + helpers only), `viewer/views/*`,
`.streamlit/config.toml`, `tests/test_viewer_labels.py`, `tests/test_viewer.py` (update).

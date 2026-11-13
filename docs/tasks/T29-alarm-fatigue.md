# T29 — Alarms vs Steward (alarm-fatigue picture)

**Branch:** feature branch from `extras`, PR into `extras` (see `docs/EXTRAS.md`) · **Priority:** P1 ·
**Viewer only** · **Score impact:** robustness to bad data & look-alikes (7), demo (8), limitations & verification (5)

## Goal
One picture that shows the operator's problem: the building alarm system floods people with alarms and still misses
leaks, while Steward raises far fewer, better incidents. Every number is computed from the loaded run; nothing is
typed into the code.

## Data
- Building alarms: `events.jsonl` `type == "bms_alarm"` (`alarm_code`, `state` raised/cleared, `asset_id`, `value`).
  Pair raise/clear into alarm episodes with the existing logic in `analysis/bms_compare.py` (import its functions;
  do not copy them). Dev: 926 alarm events — ROOM_TEMP_LOW 318, SENSOR_COMM_LOSS 318, ROOM_TEMP_HIGH 250,
  FILTER_DP_HIGH 20, AIRFLOW_LOW 18, WATER_NIGHT_FLOW 2.
- Steward incidents: incident episodes of the loaded decisions (`viewer.core` episodes, incident types only).
- Ground truth for the "caught" row: quickstart hand labels / dev weak labels via the existing `viewer.core`
  evaluate helpers (Proof page) — labelled "dev aid, labels are not ground truth".

## Do
1. `viewer/alarm_stats.py` (pure, cached): `alarm_episodes(data)`, `alarms_by_week(data)` (per alarm code),
   `incidents_by_week(df)`, `coverage(data, df, labels)` → per scenario class: labelled incidents, caught by an
   alarm, caught by Steward, median lead time of Steward vs the first matching alarm.
2. `viewer/views/alarms.py`, page **"Alarms vs Steward"** in the Proof group of the router:
   - Headline numbers (metric row): building alarm episodes · Steward incidents · water leaks caught by alarms vs by
     Steward (e.g. "2 vs 5" on dev).
   - Chart 1: weekly stacked bars of building alarms by code (muted colours) with Steward incidents per week as a line
     on top — "the flood vs the signal".
   - Chart 2: coverage per scenario (grouped bars: labelled · caught by alarms · caught by Steward), with lead time
     in the hover.
   - Short "why the alarms are noisy" box from the data: share of SENSOR_COMM_LOSS during known gateway outages
     (data-quality log), ROOM_TEMP_LOW on vacant rooms (telemetry presence) — computed, with the method in
     Technical details.
   - Honest note: alarms and labels are imperfect evidence; Steward also misses things (list the undetected labels).
3. Link card on the Proof page: "See alarms vs Steward →" (one line in `viewer/views/proof.py`).

## Acceptance criteria
- Dev: the six alarm-code counts above reproduce; water: 2 alarm-detected vs Steward's count from the evaluator.
- Quickstart and an empty decisions file render without errors (empty state in plain words).
- Every number on the page is computed at runtime; a test asserts the dev counts.
- pytest green (viewer tests skip without streamlit); pipeline output unchanged. PR with screenshots (dev + quickstart).

## Files to touch
`viewer/alarm_stats.py`, `viewer/views/alarms.py`, `viewer/app.py` (one router line), `viewer/views/proof.py`
(one link line), `viewer/labels.py` (alarm-code labels), `tests/test_viewer_alarms.py`.

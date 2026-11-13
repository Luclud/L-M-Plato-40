# T10 — Demo viewer (Streamlit): evidence → risk → decision → autonomy → verification

**Lane:** C presentation · **Priority:** P0 · **Depends on:** none (works with any decisions.jsonl; richer as agents land) ·
**Score impact:** scenario demo & visualisation (8), decision & action trail (7), limitations & verification (5)

## Goal
A local web UI for the live demo (track judging 15:00–16:00 is a **live demo**) and the 3-minute video. "A dashboard alone is not
enough" — the viewer must show grounded decisions with their evidence and autonomy, not just charts.

## Screens
1. **Overview** — dataset, period, #decisions, #episodes, by scenario/issue_type, approvals pending, consents needed,
   validator result (run `validate.py` and show PASS/violations), evaluate table if labels exist.
2. **Timeline** — episodes as bars over time (colour by issue_type), click → incident.
3. **Incident card** — for one episode: all decisions in order; for each: timestamp (UTC + local), action, priority, autonomy badge
   (autonomous / approval / approval+consent), reason, evidence (with a small chart of the referenced series, e.g. room water
   rate, HP1 COP vs expected, AHU filter_dp with gap shaded), expected outcome, verification plan, guest message (language,
   text, "proposed — not delivered"), resource, simulated effects labelled SIMULATED.
4. **Approval inbox** — decisions with `requires_human_approval`, consent flags, `approval_note` text; buttons Approve/Reject that
   only write to a local `out/approvals.json` (mock executor — label as simulation).
5. **Data quality & look-alikes** — DQ log (`<out>_dq.json`), gateway outages, "ruled out" look-alikes if agents export them,
   BMS vs ours (T12 output).
6. **Hero scenarios** — pre-selected quickstart incidents: R104 burst, AHU1 cascade, event weekend, R305 subtle leak.

## Interfaces
`viewer/app.py`, `viewer/requirements.txt` (streamlit, plotly or altair — MIT/BSD/Apache licences), run:
`streamlit run viewer/app.py -- --decisions out/qs.jsonl --data data/quickstart`. Add `viewer/Dockerfile` +
`docker-compose.yml` service `viewer` (port 8501) so it can run on our server; no secrets.

## Acceptance criteria
- Works on quickstart and dev outputs; loads dev in < 10 s (cache data loading).
- The four hero incidents can be shown in ≤ 30 s each (write the click path in `docs/DEMO.md`).
- Every displayed simulated value labelled SIMULATED; every message labelled "proposed".
- Screenshot(s) committed to `docs/img/` for the README.

## Files to touch
`viewer/*`, `docker-compose.yml`, `docs/DEMO.md`, `docs/img/*`, README "Viewer" section.

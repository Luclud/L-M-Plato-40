# Demo script — PLATO-40 viewer (T10)

Live demo (track judging) and the 3-minute video. Story per incident:
**evidence → risk → decision → autonomy → verification.** Everything is an offline replay of the
fictional PLATO-40 dataset; guest messages are *proposed — not delivered*; effects are *SIMULATED*;
Approve/Reject is a mock that only writes `out/approvals.json`.

## Start

```bash
pip install -r viewer/requirements.txt
python run.py data/quickstart --out out/qs.jsonl
streamlit run viewer/app.py -- --decisions out/qs.jsonl --data data/quickstart     # http://localhost:8501
# dev year:   python run.py data/dev --out out/dev.jsonl
#             streamlit run viewer/app.py -- --decisions out/dev.jsonl --data data/dev
# server:     docker compose up --build viewer      (generates out/qs.jsonl if missing)
```

Until the scenario agents (T01–T06) land, the pipeline output contains only data-quality episodes, so the
hero tabs show the data and the labelled window but no episode. To rehearse the click paths, use the
**illustrative** sample (hand-placed on the quickstart incident map, every reason starts with
`ILLUSTRATIVE`, agent `viewer_sample`, flags from the real policy, passes `validate.py`):

```bash
python viewer/sample_decisions.py      # regenerates viewer/sample/quickstart_illustrative.jsonl
streamlit run viewer/app.py -- --decisions viewer/sample/quickstart_illustrative.jsonl --data data/quickstart
```

Never present the sample as pipeline output.

Before the demo: open the viewer once (first load warms the cache: quickstart ~1–2 s, dev ~5 s), then
click through Overview so `validate.py`/`evaluate.py` results are cached.

## Opening (≤ 20 s) — Overview

Sidebar **Screen → Overview**. Point at: dataset + period, decisions vs episodes (one episode per
incident, no alert spam), *Need human approval* / *Guest consent needed (needed ≠ granted)*, the green
`validate.py: PASS` box, and the *Detection vs labels* table.

## Hero 1 — Burst hose (S02 loud), ≤ 30 s

1. Sidebar **Screen → Hero scenarios**, tab **Burst hose (S02 loud)** (first tab, already open).
2. Chart: room water rate jumps to ~27 L/15 min; yellow band = labelled window; red dotted lines = our
   decisions (first one ~1 h after onset — BMS night-flow alarm only hours later).
3. Scroll to the incident card: `flag_anomaly` (autonomous) → `request_inspection` (approval; consent if
   occupied) → `water_shutoff` (APPROVAL + CONSENT badge, `SIMULATED` avoided volume) + guest message
   box labelled **PROPOSED — not delivered**.
4. Say: "needs approval, consent is *needed*, not granted — nothing is executed."

## Hero 2 — AHU filter cascade (S04), ≤ 30 s

1. Tab **AHU filter cascade (S04)**.
2. Filter Δp chart: rising trend 92 → 165 Pa, then the **grey band = data gap (unknown, not repaired)**,
   then 256 Pa. Airflow drop in the second chart.
3. Incident card: early `flag_anomaly` before the gap, episode kept open through the gap, `order_service`
   via approval because the technician is on vacation (roster `status_known_utc`, not future data).

## Hero 3 — Event weekend (S05), ≤ 30 s

1. Tab **Event weekend (S05)**.
2. Bar chart: confirmed arrivals per local day — the peak is visible days ahead (bookings by `created_utc`).
3. Incident card: capacity plan before the weekend → re-plan when the cleaner calls in sick → robots for
   floors (autonomous), on-call / extra staff via `request_human_approval`; guest messages PROPOSED;
   `SIMULATED` time freed.
4. Optional: sidebar **Approval inbox** → click **Approve** on the staffing request → banner shows it was
   written to `out/approvals.json` (mock executor).

## Hero 4 — Subtle toilet leak (S02 subtle), ≤ 30 s

1. Tab **Subtle toilet leak (S02 subtle)**.
2. Room water chart: ~1 L/15 min, mostly at night; our first decision lands before the guest complaint.
3. Incident card: `flag_anomaly` → `request_inspection` with consent flag; mention the open question
   (main − Σ rooms is *not* conclusive here, see `docs/DATA.md`).

## Other screens (if asked)

- **Timeline** — every episode as a bar (colour = issue type). Click a bar (or pick it in the selector)
  → **Incident card**.
- **Incident card** — any episode: decisions in order with UTC + Europe/Vienna time, autonomy badge,
  evidence + chart of the referenced series (data gaps shaded), expected outcome, verification plan,
  resource, guest message (proposed), simulated effects (SIMULATED).
- **Approval inbox** — all `requires_human_approval` decisions, consent flags, approval note, mock
  Approve/Reject.
- **Data quality & look-alikes** — cleaning log (`<out>_dq.json`), gateway outages (rooms behind an
  outage = unknown, not normal), ruled-out look-alikes / BMS comparison files if exported next to the
  decisions file, BMS alarms in the dataset.

On dev/holdout (no hand labels) the hero screen shows the four most severe episodes instead.

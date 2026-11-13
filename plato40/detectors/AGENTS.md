# Detector rules (applies to files in plato40/detectors/)

- One domain agent per file, class name `<Domain>Agent`, unique `name` (used by `--detectors`).
- Register it in `plato40/detectors/__init__.py` (already done for the planned agents).
- `build_triggers` must be **causal**: every trigger at time ts is computable from data <= ts.
  Use `rolling(window)` (right-aligned, default) or `expanding()`; baselines from history only.
  Typical warm-up: if not enough history yet, do not trigger (or trigger with low confidence).
- Read availability-timed data through `ctx.asof` in `handle` (roster, tariff, forecast, work orders,
  bookings, guest requests). Telemetry/meters: rows with timestamp <= ctx.t.
- Episode keys: `"<agent>:<asset>"` (+ `:<date>` if incidents can repeat on the same asset).
  Close the episode (`ctx.log.close(key)`) when the condition clears so a later recurrence is a new episode.
- Escalation ladder (typical): `flag_anomaly` (autonomous) → `request_inspection` /
  `dispatch_technician` / `order_service` (approval) → `water_shutoff` / occupied-room entry
  (approval + consent). Each step is a new decision in the same episode, only when warranted.
- Look-alikes are part of the score: explicitly skip and (optionally) note them in `reason` of the
  decision you *do* take, e.g. "excluded: occupied + shower pattern". Never emit a decision for a
  look-alike you ruled out (that would be a false alarm). Keep a counter for the viewer/insights:
  `self.ruled_out.append({...})` and expose it in `finish()` (write to `out/<agent>_ruled_out.json`
  only if `ctx.data` path is writable; optional).
- Data gaps: consult `DataQualityAgent` logic — treat rooms/assets with missing data as unknown.
- Performance: whole dev year for your agent < 15 s. Precompute vectorised in `build_triggers`;
  keep `handle` cheap.
- Tests: add `tests/test_<agent>.py` with at least one quickstart incident detected, one look-alike
  not flagged, and no future-data use (e.g. truncate data at t and check the same decision appears).

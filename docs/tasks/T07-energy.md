# T07 — Energy optimisation agent (battery / load shifting, simulated)

**Lane:** A detection · **Priority:** P1 · **Depends on:** T02 `models/heatpump.py` optional · **Score impact:** energy/comfort trade-offs (6)

## Goal
Use day-ahead prices (as published), PV and weather forecasts to plan battery charge/discharge and heat-pump pre-heating,
without leaving the comfort band. All effects are **simulated** and labelled.

## Inputs
`energy.csv` (`building_load_kw`, `hvac_electric_kw`, `pv_kw`, `battery_discharge_kw_signed` (+ = discharge), `battery_soc_kwh`,
`grid_import_kw`, `grid_export_kw`), `ctx.asof.tariff()`, `ctx.asof.forecast()` (temperature only; PV must be estimated
from history/cloud cover), BAT1 limits (100 kWh usable, ±40 kW, SoC 10–95 kWh, η 0.9).

## Suggested approach
Once per day after tariff publication (13:00 local): simple LP/greedy over the next day's hours: charge in cheapest hours /
PV surplus, discharge in most expensive hours, respecting limits; compare with the observed baseline (what actually happened)
→ `simulated_effect` "SIMULATED: −€x.xx vs observed schedule". Pre-heat suggestions coordinate with T02 (do not duplicate setpoint
decisions — call T02's helper or emit `optimize_energy` only). One `optimize_energy` decision per day max (+ exceptions).

## Interfaces
`plato40/detectors/energy.py` (`EnergyAgent`), issue_type `energy_optimization`, episode `energy:<local_date>`.

## Acceptance criteria
- One plan per day max on dev (≤ 366 decisions), each with price window evidence (published_utc ≤ t), SoC bounds respected
  (unit test), `simulated_effect` labelled SIMULATED, total simulated saving reported in run output (not claimed as real).
- No comfort-band violations caused (no occupied-room setpoint outside 20–23 °C).

## Files to touch
`plato40/detectors/energy.py`, `tests/test_energy.py`.

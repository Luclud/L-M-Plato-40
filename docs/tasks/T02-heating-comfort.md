# T02 — S03 Comfort & heat-pump agent (energy vs comfort)

**Lane:** A detection · **Priority:** P0 · **Depends on:** none · **Score impact:** detection (10), trade-offs (6), safe autonomy (10)

## Goal
Protect occupants during cold weather with reduced heating performance and dynamic prices: detect heat-pump degradation
(beyond what the cold itself explains), pre-heat using the forecast, organise service — never trade comfort for cost.

## Context
Brief S03: "Cold weather, dynamic prices and reduced heating performance meet occupied rooms. Protect occupants, use the
forecast, organise service — don't blindly minimise cost." Quickstart: cold snap 14–17 Jan, COP 2.8 → 1.6,
`lockout_high_pressure`, refrigerant leak fixed 17 Jan 23:00Z, TECH_1 sick 15 Jan. Look-alike: COP falls naturally in the cold.

## Inputs
- `data.equipment_wide("HP1")`: `heat_output_kw`, `electric_kw`, `supply_temp_c`, `cop` (if present), `status`.
- `data.weather` (`outdoor_c`, `cloud_cover_pct`), forecast via `ctx.asof.forecast()` (issued 06:00 local), tariff via
  `ctx.asof.tariff()` (published 13:00 local day before).
- Room telemetry temperature/setpoint/window_open; occupancy from `state`; comfort band 20–23 °C (building.json constraints).
- Roster TECH_1 via `state.available`; work orders as-of; guest requests `temperature_cold/hot` (as-of).

## Suggested approach
1. **Expected-COP model** in `plato40/models/heatpump.py`: fit COP (or heat output per kW electric) vs outdoor temperature
   (and supply temp) on a rolling/expanding window of "normal" operation only (status running, no lockout), e.g. robust
   linear/quantile regression per outdoor-temperature bin. Residual = actual − expected; degradation = persistent negative
   residual (e.g. ≥ 6 h below the 10th percentile of historical residuals) and/or lockout status codes.
2. **Comfort risk:** count occupied rooms below 20 °C (with window closed, data valid); aggregate into ONE building-level
   episode (`heating_comfort`, affected HP1 + worst rooms) instead of per-room alarms; per-room follow-ups only for rooms
   that stay cold ≥ 3 h while occupied.
3. **Actions:** `dispatch_technician` (approval) if TECH_1 available (as known), else `order_service` contractor (approval,
   `service="hvac_contractor"`); `adjust_room_setpoint` within band (autonomous) / pre-heat before forecast cold during cheap
   hours known from the published tariff (autonomous if within band; record price window + forecast in evidence);
   `notify_guest` heating_update for affected occupied rooms with temperature_cold requests (`guest_request_ids`).
4. **Trade-off rule:** if prices are high and comfort is at risk → comfort wins; state that explicitly in `reason`.
5. Summer: overheating (heating-only building) → `heating_comfort` episodes with night-flush recommendation
   (`optimize_energy` / ventilation) — optional P1.

## Interfaces
- `plato40/detectors/s03_comfort.py` (`ComfortAgent`, name `comfort`), `plato40/models/heatpump.py` (reusable by T07/T11).
- issue_type `heating_comfort`; episode keys `comfort:HP1`, `comfort:<room>:<date>`.

## Acceptance criteria
- Quickstart: **QS-S03-HP1-coldsnap detected by 15 Jan 12:00Z** with a service action (technician/contractor, approval)
  in the same episode; reason shows COP residual vs expected at that outdoor temperature (not raw COP).
- Dev: both HP1 refrigerant-leak incidents (WO 27 Jan, 8 Dec) detected; ≤ 10 unmatched `heating_comfort` episodes per year.
- No setpoint outside 20–23 °C for occupied rooms without approval (validator + test); pre-heat decisions only use
  forecast/tariff rows with `issued_utc/published_utc <= t` (test).
- ROOM_TEMP_LOW-style per-room spam avoided: ≤ 1 building episode per cold-snap + bounded per-room follow-ups.
- Tests `tests/test_comfort.py`.

## Out of scope
Real thermal simulation; claiming measured savings (label any € estimate as simulated).

## Pitfalls
Defrost cycles are normal; lockout codes may be noisy; COP undefined when electric_kw≈0; window_open makes rooms cold
(look-alike); setback 18 °C in vacant rooms is not a comfort problem.

## Files to touch
`plato40/detectors/s03_comfort.py`, `plato40/models/__init__.py`, `plato40/models/heatpump.py`, `tests/test_comfort.py`.

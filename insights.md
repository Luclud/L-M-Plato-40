# PLATO-40 — Level 3 feedback (draft)

> All findings concern the fictional **PLATO-40** and its synthetic dataset — not real PLATO buildings.
> Status: **draft hypotheses** from a first look at the dev year. Each must be confirmed with the
> analysis notebook before it counts; numbers below are preliminary.

Format per recommendation: finding → evidence (file, rows, method) → recommendation → uncertainty.

## For PLATO (build the next building differently)

### P1 — First floor above the open car park runs cold in winter (hypothesis)
- **Finding (preliminary):** in Dec–Feb, floor-1 rooms spend ~19–22 % of occupied intervals below 20 °C vs ~9–13 % on floors 2–4.
- **Evidence:** `room_telemetry.csv` temperature_c, occupancy proxy setpoint_c ≥ 19 °C. TODO: redo with stays from events.
- **Recommendation:** extra insulation of the floor slab exposed to outside air / heated soffit for the parking-level floor.
- **Uncertainty:** occupancy proxy; confounded by room mix and orientation.

### P2 — Summer overheating in a heating-only building (hypothesis)
- **Finding (preliminary):** 25–39 % of summer occupied intervals above 26 °C, south rooms worst; 201 `temperature_hot` vs 29 `temperature_cold` requests.
- **Recommendation:** external shading on south modules / passive cooling option in the module catalogue.

### P3 — Rooftop AHU needs a lift platform for filter service
- **Evidence:** `work_orders.csv` "AHU1 filter change postponed - rooftop unit only reachable with lift platform".
- **Recommendation:** design for maintainability: access route / fixed platform to rooftop plant.

### P4 — Water sub-metering (TODO after S02)

## For CLARA (operate differently next year)

### C1 — Legionella flushing in long-vacant rooms → rotate room allocation (TODO: quantify)
### C2 — BMS alarm tuning (TODO: compare BMS alarms vs our episodes — missed / false)
### C3 — Pre-heating strategy before forecast cold snaps (TODO after S03)
### C4 — Filter maintenance interval from pressure trend (TODO after S04)

## Target architecture

TODO: data sources, agents (operations, housekeeping, energy, maintenance, guest service),
robots, interfaces (PMS, ACS, BMS, roster, contractor portal), human approval loop.

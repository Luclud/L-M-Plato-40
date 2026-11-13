# T08 — Cleaning & logistics agent (waste, vending, robot bin runs)

**Lane:** B operations · **Priority:** P1 · **Depends on:** T05 planner (for robot slots) optional · **Score impact:** coordination (8)

## Goal
Order extra waste pickups and vending restocks **before** overflow / stock-out (~24 h lead time), and use robots to carry floor
bins to the central container (capability exists but "not used in current operations").

## Inputs
`data.logistics_wide()` columns `WASTE_F1..F4.fill_pct`, `WASTE_MAIN.fill_pct`, `VEND1.stock_drinks`, `VEND1.stock_snacks`
(hourly; values capped at 100 % — a full bin may be overflowing), events `waste_pickup`, `waste_bin_full`, `floor_bin_emptied`,
`vending_restocked`, `vending_out_of_stock`, bookings/departures (as-of) as demand driver; schedules: municipal pickup Tue+Fri
07:00 local, vendor restock Mon+Thu 10:00 local.

## Suggested approach
Forecast fill/stock to the next regular service using recent hourly rates scaled by expected departures/occupancy; if predicted
≥ 95 % (waste) / ≤ 0 (stock) before the next regular service and ≥ 24 h lead time remains → `order_service`
(`service="waste_pickup"` / `"vending_restock"` → autonomous). Floor bins ≥ 80 % → `dispatch_robot` (bin run, ~20 min, robot on shift,
not in occupied rooms) or housekeeping slot.

## Interfaces
`plato40/detectors/logistics.py` (`LogisticsAgent`), issue_type `logistics`, episodes `logistics:<asset>:<local_date>`.

## Acceptance criteria
- Dev: ≥ 70 % of `vending_out_of_stock` (20) and `waste_bin_full` (3) events preceded by an order ≥ 24 h earlier;
  unnecessary orders (no predicted shortfall materialising within 48 h) ≤ 30 % of orders (report in PR).
- Robots only dispatched when on shift (validator). Tests `tests/test_logistics.py`.

## Files to touch
`plato40/detectors/logistics.py`, `tests/test_logistics.py`.

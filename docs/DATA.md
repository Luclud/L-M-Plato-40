# Data guide

Full column reference: `data/dev/README.md` (organiser). This file adds what we learned.

## Datasets in this repo
| Path | Profile | Period (UTC) | Use |
|---|---|---|---|
| `data/quickstart/` | quickstart | 2026-01-05 → 2026-01-26 (21 days, January) | develop; every scenario class at least once |
| `data/dev/` | dev | 2026-01-01 → 2027-01-01 (365 days) | seasonality, repeats, look-alikes; produce `decisions.jsonl` |
| `data/raw/PLATO40_v2_holdout_encrypted.7z` | holdout | unknown | password released Sat ~11:00; run unchanged |

The datasets are **independent synthetic runs — never concatenate them**. Original archives + checksums in `data/raw/`.

## Building (building.json)
- 40 rooms R101–R410, 4 floors × (5 south R1x01–05 + 5 north R1x06–10); 22/28/32 m², bathtub in 32 m² rooms, max 2 guests.
- Assets: `HP1` air-source heat pump 95 kW th @7 °C (space heating + DHW), `AHU1` 4200 m³/h with heat recovery
  (rooftop — needs lift platform for filter change), `PV1` 60 kWp, `BAT1` 100 kWh usable, ±40 kW, SoC 10–95 kWh,
  η 0.9, `WM_MAIN` + `WM_Rxxx`, `ACS1` access control, `WASTE_F1–F4` (240 L), `WASTE_MAIN` (1100 L, pickup Tue+Fri 07:00
  local, extra ~24 h lead), `VEND1` (100 drinks / 60 snacks, restock Mon+Thu 10:00 local, extra ~24 h), gateways `GW_F1–F4`.
- **Heating only, no cooling** (summer: night flushing via ventilation).
- Staff: `CLEANER_1–3`, `TECH_1` (08:00–16:30 Mon–Fri local), `ROBOT_1–2` (floors only, no bathrooms, cannot release,
  never enter occupied rooms, can carry a floor bin ~20 min — "not used in current operations"),
  `ONCALL_1` (15:30–22:00 local, ~45 min to site, one room at a time, costly, waiting guests only).
- Operations: check-in from 15:00, check-out until 11:00, housekeeping 07:00–15:30 local. Setpoint 18 °C setback
  unoccupied, 21 °C default occupied, guests 19–24 °C. Comfort band occupied **20–23 °C**.
- Release requires: vacant, clean, not blocked, inspection passed if required.

## Time
- All timestamps UTC; local Europe/Vienna (UTC+1 winter, +2 summer — DST!). Use `plato40.clock`.
- Telemetry 15-min; logistics hourly; tariff hourly, published 13:00 local previous day; forecast issued daily
  06:00 local for 48 h hourly; roster `status_known_utc`; work orders `created_utc` (written by people — late/terse).

## Cleaning applied by `plato40/io.py` (logged in `<out>_dq.json`)
| Issue | Seen in quickstart | Handling |
|---|---|---|
| Duplicate rows | 2 telemetry duplicates | drop, keep first |
| Implausible values | R106 temperature 85.0 °C | → NaN, `implausible` column keeps original |
| Meter reset | WM_R403 −27,020 L on 18 Jan 05:15Z (work order dated 15 Jan!) | consumption NaN for that step |
| Meter jitter | 338 small negative steps | clipped to 0 |
| Gaps | GW_F3 floor outage 11 Jan; AHU1 filter_dp 16 Jan 22:30Z → 21 Jan 03:15Z | kept NaN; DQ agent flags |
| Frozen sensor | R205 temperature constant ≥ 6 h on 12 Jan | DQ agent flags |

Water: `room_water` / `water_main` contain **valid readings only** with `use_l` (litres since previous valid reading),
`span_intervals`, `rate_l_per_interval`, `reset`. Main meter also covers common areas (lobby, staff rooms, technical
room, roof-garden irrigation) and losses: main − Σrooms ≈ 300–360 L/day in January.

## Quickstart incident map (hand labels in `eval/labels_quickstart.json`)
| Incident | Signal | Timeline (UTC) |
|---|---|---|
| **S02 loud — R104 burst hose** | room meter ~25–29 L / 15 min continuously (~100 L/h, ~3000 L) | starts 12 Jan **17:00**; BMS WATER_NIGHT_FLOW (on WM_MAIN) 13 Jan 01:45; plumber WO00003 created 13 Jan 22:30; stops 23:00 |
| **S02 subtle — R305 toilet** | ~1 L / 15 min, nonzero 70 % of intervals 19 Jan, 100 % from 20 Jan; ~18 L per night (00–04Z) vs 0–6 | starts ~19 Jan; guest message "La cisterna no para de correr" 22 Jan 08:50; WO00009 25 Jan 21:45 |
| **S03 — cold snap + HP1 refrigerant leak** | outdoor daily mean −9…−13.6 °C 14–17 Jan; COP 2.8 → 1.6; supply temp ↑; status `lockout_high_pressure` 16 Jan 15:15/22:00, 17 Jan 15:15/22:00 (+ one 7 Jan 21:30); ~60 ROOM_TEMP_LOW alarms; many `temperature_cold` requests | TECH_1 sick 15 Jan (known 01:15); WO00007 refrigerant leak 17 Jan 23:00 |
| **S04 — AHU1 filter** | filter_dp daily 92 → 110 (14 Jan) → 135 → 165 (16 Jan), gap, 256 Pa + airflow 3850 → 3316 m³/h | gap 16 Jan 22:30 → 21 Jan 03:15; BMS FILTER_DP_HIGH from 21 Jan 05:45; TECH_1 vacation 19–23 Jan (known Dec); contractor WO00008 23 Jan |
| **S05 — event weekend** | 25 arrivals 23 Jan, 20 on 26 Jan | CLEANER_1 sick 24 Jan (known 05:30); 6 guests waiting 24 Jan (R403, R302, R108, R304, R101, R306); ONCALL_1 called twice |
| **S01 — turnarounds** | early check-ins (37), `arrival_waiting_room` 8× | CLEANER_1 sick 8 Jan (known 05:15) → R308 waits 10:30; R207 waits 23 Jan 11:15; CLEANER_1 sick 17 Jan |

Guest requests quickstart: early_checkin 27, temperature_cold 23, late_checkout 11, noise 9, waiting_for_room 6, water_issue 1.

## Dev year — first look (verify before using in insights)
- 3,616 checkouts, 3,255 check-ins + 425 early, 98 early checkouts, 71 no-shows, 24 waiting-for-room events.
- BMS alarms 926: ROOM_TEMP_LOW 318, SENSOR_COMM_LOSS 318, ROOM_TEMP_HIGH 250, FILTER_DP_HIGH 20, AIRFLOW_LOW 18,
  **WATER_NIGHT_FLOW only 2** (the BMS misses most leaks → strong demo point).
- Guest requests 1,612: early_checkin 349, late_checkout 271, **temperature_hot 201**, noise 173, wifi 140, towels 112,
  arrival_delay 87, parking 57, maintenance_issue 50, temperature_cold 29 …; languages de 708, en 561, it 127, fr 92, es 82, nl 42.
  Texts are templated (≤ 129 variants per type); `request_type`, `priority`, `response_due_utc`, consent are given;
  requested times are in the text ("gegen 14:30", "bis 12:00").
- Roster: cleaner sick 24 shifts, robot out_of_service 13, technician vacation 17 / sick 3.
- Work orders 76 (59 corrective, 15 preventive, 2 metering). Key incidents (weak labels in `eval/evaluate.py --data`):
  HP1 refrigerant leak 27 Jan & 8 Dec; AHU1 filter postponed (lift platform) 23 Feb & 4 Jun, planned change 4 Mar & 9 Jun,
  heavily loaded filters 29 Jul & 24 Oct; burst hose R201 20 Mar, R401 17 Aug; toilet fill valve R403 28 Jun, R208 17 Nov;
  **DHW circulation pipe leak in technical room 18 Jul (WM_MAIN — the "gap" leak)**; meter exchanges WM_R105 1 Jul,
  WM_R405 16 Dec; 11 legionella flushes of long-vacant rooms; 29 generic guest-reported defects.
- Comfort (proxy: setpoint ≥ 19 °C as occupied — redo with stays): winter share < 20 °C floor 1: 19–22 % vs 9–13 % floors 2–4;
  summer share > 26 °C: 25–39 %, south worse.

## Open questions (document findings in PRs / docs/DECISIONS.md)
1. R305 subtle leak shows on the room meter but main − Σrooms turns slightly **negative** from 19 Jan — meter drift
   artefact mixed in? How should the gap detector treat it?
   *Finding (T01):* yes — the negative gap is the R305 meter over-reading vs. the main meter, so the gap
   detector (`s02_water.py`) only alarms on a **positive** night excess (night median of main − Σrooms vs. a rolling
   baseline of previous non-incident nights, ≥ 2 L/15 min for ≥ 2 nights). R305 is caught by the room-meter subtle
   rule (no zero interval for 6 h) instead; a negative gap is never treated as a leak.
2. Is the floor-1 cold effect real after controlling for occupancy, orientation and room size? (PLATO insight P1)
3. Robots "can carry bins — not used in current operations": quantify what using them would change (logistics + insight).
4. Holdout period/profile unknown — never assume January or 365 days.

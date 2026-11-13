# Architecture

## 1. Current system (what runs in the hackathon)

```
data_dir ──► io.load ──► Data (cleaned frames + DQ log)
                │
                ├─► State: stays (from events), housekeeping state, roster/robot/on-call availability,
                │           release preconditions
                ▼
          engine.run — clock 15 min from manifest.start_utc to end_utc
                │   for t in clock: for agent in agents: agent.step(ctx)
                ▼
   ┌──────────────┬──────────────┬─────────────┬──────────────┬─────────────┬──────────┬───────────┬───────────┐
   │ data_quality │ turnaround   │ water       │ comfort      │ maintenance │ surge    │ energy    │ logistics │
   │ (done)       │ S01 (T05)    │ S02 (T01)   │ S03 (T02)    │ S04 (T03)   │ S05 (T06)│ (T07)     │ (T08)     │
   └──────┬───────┴──────┬───────┴──────┬──────┴──────┬───────┴──────┬──────┴────┬─────┴─────┬─────┴─────┬─────┘
          │  shared: planning/housekeeping.py (T05)   messaging.py (T09)                                    │
          ▼                                                                                                  ▼
     decision.make_decision ── autonomy policy (approval / consent derived, only stricter)
          ▼
     DecisionLog ── episodes (one incident = one detection, follow-ups only on change)
          ▼
     decisions.jsonl ──► validate.py (contract + safety) ──► eval/evaluate.py (dev aid) ──► viewer (T10)
                                                           └► analysis/insights (T11) ──► insights.md / .jsonl
```

### Key design rules
- **Causal by construction.** Agents precompute features with backward-looking windows in `build_triggers`, emit
  triggers at the time the information existed; `handle` reads availability-timed data via `ctx.asof`.
- **Policy, not discipline.** `make_decision` derives `autonomy`, `requires_human_approval`, `requires_guest_consent`
  from the action + context hints. `validate.py` re-checks every output file (and is run on the holdout output).
- **Episodes.** `DecisionLog.add(decision, episode_key=...)` groups an incident; `close(key)` ends it.
- **Deterministic & offline.** Same input → same output. LLM wording (T09) optional, cached, template fallback.
- **Agents = modules.** "Multi-agent" here means domain agents with clear responsibilities sharing one state and one
  log; a coordinator role is played by shared planners (housekeeping capacity) so agents don't double-book staff.

### Module map
| Module | Responsibility | Owner ticket |
|---|---|---|
| `plato40/io.py` | load + clean + DQ log | done (shared) |
| `plato40/asof.py` | as-of views | done (shared) |
| `plato40/state.py` | stays, housekeeping, availability, release check | done (shared) |
| `plato40/decision.py` | Decision, vocab, ISSUE_TYPES, autonomy policy | done (shared) |
| `plato40/log.py` | episodes, writer | done (shared) |
| `plato40/engine.py` | replay clock | done (shared) |
| `plato40/detectors/data_quality.py` | gaps, gateway outages, frozen, implausible, meter resets | done |
| `plato40/detectors/s02_water.py` | leaks | T01 |
| `plato40/detectors/s03_comfort.py` | heat pump model, comfort protection, pre-heat | T02 |
| `plato40/detectors/s04_maintenance.py` | AHU filter cascade | T03 |
| `plato40/models/` | shared statistical models (e.g. expected COP) | T02 creates |
| `plato40/planning/housekeeping.py` | cleaning capacity + schedule (cleaners, robots, on-call) | T05 |
| `plato40/detectors/s01_turnaround.py` | turnaround conflicts | T05 |
| `plato40/detectors/s05_surge.py` | event weekend | T06 |
| `plato40/detectors/energy.py` | battery / load shifting (simulated) | T07 |
| `plato40/detectors/logistics.py` | waste, vending, robot bin runs | T08 |
| `plato40/messaging.py` | guest messages (6 languages), requested-time parsing, approval notes | T09 |
| `viewer/` | Streamlit demo UI | T10 |
| `analysis/` | Level 3 analysis scripts → insights | T11 |
| `analysis/bms_compare.py` | our episodes vs BMS alarms | T12 |

## 2. Decision record (extensions to the contract)
Contract fields + `scheduled_for_utc`, `message {language, text, source}`, `simulated_effect`, `agent`.
Extra actions: `water_shutoff` (approval + consent), `call_oncall_housekeeper` (approval, notable cost).
Canonical `issue_type`: see `plato40.decision.ISSUE_TYPES`.

Evidence string format: `<file>:<asset>:<iso_start>..<iso_end>[:<detail>]`, e.g.
`room_water.csv:R104:2026-01-12T17:00:00Z..2026-01-12T19:00:00Z:~27 L/15min vs night baseline 0`.

## 3. Target system (for insights.md §architecture — the "real" CLARA Future)
- **Data layer:** PMS (bookings, check-in), ACS (doors), BMS/BACnet (HP, AHU, room controllers), smart meters per
  room + per riser + main, energy (PV inverter, battery BMS, grid meter), weather & day-ahead price APIs, roster/HR,
  CMMS (work orders, contractors), guest messaging channels, robot fleet API, logistics sensors (bin fill, vending).
  Every record carries *event time* and *availability time*.
- **Agents:** operations coordinator, housekeeping planner, energy optimiser, maintenance/FDD (fault detection &
  diagnosis), water guard, guest service (multilingual), logistics; each with explicit autonomy envelope.
- **Robots:** floor cleaning + bin transport (fleet manager with task queue, occupancy lock-out).
- **Human loop:** approval inbox (technician, contractor, room block, guest move, cost), consent requests to guests,
  escalation timers, full audit trail.
- **Learning loop:** yearly (and continuous) analysis → CLARA rule updates (thresholds, pre-heat curves,
  maintenance intervals) and PLATO design feedback (envelope, shading, metering, plant access, room mix) per module type.

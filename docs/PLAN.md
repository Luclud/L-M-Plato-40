# Project plan

**Planner:** Claude (this repo's docs) · **Team:** Luca + teammate · **Implementation:** Devin agents, one ticket per session/PR.
**Code freeze: Saturday 14 Nov, 14:00 Europe/Vienna** (see docs/DECISIONS.md D8). Holdout release ~11:00. Live demo 15:00.

## Status (Fri 13 Nov, evening)
- ✅ Backbone: loader + cleaning, as-of views, state, autonomy policy, episodes, replay engine, CLI, validator
- ✅ Data-quality agent (gateway outages, frozen sensors, implausible values, meter resets)
- ✅ Evaluation harness (`eval/`), 25 tests, Docker, docs, tickets
- ⬜ Scenario agents, messaging, viewer, insights, release

Baseline: quickstart incidents detected **0/7**, dev weak-label incidents **0/40**, validator PASS.

## Ticket board
| Ticket | Title | Lane | Prio | Depends | Wave |
|---|---|---|---|---|---|
| [T01](tasks/T01-water-leaks.md) | S02 water leaks (loud, subtle, gap) | A | P0 | — | 1 |
| [T02](tasks/T02-heating-comfort.md) | S03 comfort + heat-pump model | A | P0 | — | 1 |
| [T03](tasks/T03-maintenance-cascade.md) | S04 AHU filter cascade | A | P0 | — | 1 |
| [T05](tasks/T05-turnaround-planner.md) | S01 turnaround + shared housekeeping planner | B | P0 | — | 1 |
| [T09](tasks/T09-messaging.md) | Multilingual messages, time parsing, approval notes | B | P0 | — (stub exists) | 1 |
| [T10](tasks/T10-viewer.md) | Streamlit demo viewer | C | P0 | — | 1 |
| [T11](tasks/T11-insights.md) | Level 3 insights + target architecture | C | P0 | — | 1 |
| [T06](tasks/T06-event-weekend.md) | S05 event weekend | B | P0 | T05 | 2 |
| [T04](tasks/T04-guest-service.md) | Guest service agent | B | P1 | T09 | 2 |
| [T07](tasks/T07-energy.md) | Energy / battery (simulated) | A | P1 | T02 optional | 2 |
| [T08](tasks/T08-logistics.md) | Waste, vending, robot bin runs | B | P1 | T05 optional | 2 |
| [T12](tasks/T12-bms-compare.md) | BMS vs ours + look-alikes | C | P1 | T01–T03 | 3 |
| [T13](tasks/T13-release-holdout.md) | Release check + holdout runbook | R | P0 | — | 2 (re-run Sat 10:30) |
| [T14](tasks/T14-pitch-video.md) | Pitch, demo script, video | C | P0 | T10, T11 | humans, Sat |
| [T15](tasks/T15-submission-repo.md) | Submission logistics, Proximata org repo | R | P0 | — | humans, now |

**Parallelism:** Wave 1 tickets touch disjoint files → run up to 7 Devin sessions in parallel. Merge order inside a lane:
T05 before T06; T09 before T04. Shared modules (`io/asof/state/decision/log/engine/validate`) change only with `[shared]` PRs.

## Timeline (Europe/Vienna)
| When | Milestone | Gate |
|---|---|---|
| Fri 19:00 | Wave 1 started (T01, T02, T03, T05, T09, T10, T11) | — |
| Fri 23:30 | **M1** Level 1 on quickstart: R104, R305, HP1 cold snap detected; S01 conflicts planned | evaluate ≥ 4/7, validate PASS |
| Sat 03:00 | **M2** Wave 1 merged; wave 2 started (T06, T04, T07, T08, T13) | dev run < 60 s |
| Sat 07:00 | **M3** Level 2 merged; dev weak-label recall reviewed; false alarms triaged | evaluate quickstart ≥ 6/7 |
| Sat 09:30 | **M4** insights.md + viewer demo-ready; T12 | demo click-path works |
| Sat 10:30 | **Freeze for holdout**: release_check, root `decisions.jsonl`, tag `pre-holdout` | all green |
| Sat ~11:00 | Holdout run unchanged → `decisions_holdout.jsonl` | validate PASS |
| Sat 11:30–13:30 | README final, video, submission, Proximata org repo | T14, T15 |
| **Sat 14:00** | **Code freeze** | — |
| Sat 15:00 | Live demo (track judging) | — |

## Cut list (if behind at M3) — drop in this order
1. T07 energy optimisation (drop entirely rather than fake it) · 2. T08 vending (keep waste) ·
3. T12 BMS compare (use manual numbers) · 4. T04 generic request types (keep maintenance + consent).
**Never cut:** S01–S03, validator, holdout run, insights (≥ 2 PLATO + 2 CLARA), viewer incident card.

## Human responsibilities
- **Luca:** review/merge PRs lane A + release (T13, T15), Discord questions, server.
- **Teammate:** review/merge lanes B + C, demo script & video (T14).
- Both: sleep in 3 h shifts; one person awake to review Devin PRs.

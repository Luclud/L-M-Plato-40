# Project plan

**Planner:** Claude (this repo's docs) · **Team:** Luca + teammate · **Implementation:** Devin (Luca) + Claude Code (teammate), one ticket per session/PR.
**Code freeze: Saturday 14 Nov, 14:00 Europe/Vienna** (see docs/DECISIONS.md D8). Holdout release ~11:00. Live demo 15:00.

## Status (Fri 13 Nov, 20:45)
- ✅ Backbone, data-quality agent, eval harness, Docker, docs
- ✅ Merged: T01–T13 + T16 (Devin + teammate), robustness check #17, viewer fix #12
- 🔍 In review (Devin): t02-warmup #19, t06-robot-replan #20, t16-quickstart #21, release dry run (this PR). Humans: T14, T15, `pre-holdout` tag Sat

main @ 73a2a5b: quickstart **7/7**, dev **39/40** (miss: SURGE-2026-09-19), validate PASS (0 violations), 313 tests, dev run ~31 s,
`scripts/release_check.sh` PASS. Unmatched on dev: 39 `room_turnaround` (real waits / shortfalls), 4 `occupancy_surge`, 2 `ventilation_maintenance`.
Robustness (start 1 Mar / 1 Jul / 60 d / 21 d): no crashes, validate PASS; comfort warm-up gap fixed in #19.
T11 re-run on 73a2a5b: insights regenerate byte-identical (5 PLATO, 6 CLARA; all say PLATO-40, evidence + uncertainty).

## Ticket board
| Ticket | Title | Lane | Prio | Depends | Status |
|---|---|---|---|---|---|
| [T01](tasks/T01-water-leaks.md) | S02 water leaks (loud, subtle, gap) | A | P0 | — | ✅ #1 Devin |
| [T02](tasks/T02-heating-comfort.md) | S03 comfort + heat-pump model | A | P0 | — | ✅ #4 Devin, warm-up #19 🔍 |
| [T03](tasks/T03-maintenance-cascade.md) | S04 AHU filter cascade | A | P0 | — | ✅ #3 Devin |
| [T05](tasks/T05-turnaround-planner.md) | S01 turnaround + shared housekeeping planner | B | P0 | — | ✅ #10 teammate |
| [T09](tasks/T09-messaging.md) | Multilingual messages, time parsing, approval notes | B | P0 | — | ✅ #7 teammate |
| [T10](tasks/T10-viewer.md) | Streamlit demo viewer | C | P0 | — | ✅ #5, #12 Devin |
| [T11](tasks/T11-insights.md) | Level 3 insights + target architecture | C | P0 | — | ✅ #2 Devin (re-run on 73a2a5b: unchanged) |
| [T04](tasks/T04-guest-service.md) | Guest service agent | B | P1 | T09 | ✅ #11 teammate |
| [T07](tasks/T07-energy.md) | Energy / battery (simulated) | A | P1 | — | ✅ #9 Devin |
| [T12](tasks/T12-bms-compare.md) | BMS vs ours + look-alikes | C | P1 | T01–T03 | ✅ #8 Devin |
| [T13](tasks/T13-release-holdout.md) | Release check + holdout runbook | R | P0 | — | ✅ #6 Devin (dry run Fri PASS; re-run Sat 10:30) |
| [T06](tasks/T06-event-weekend.md) | S05 event weekend | B | **P0** | T05 ✅ | ✅ #14, #20 robot re-plan 🔍 — qs 7/7, dev 39/40 |
| [T16](tasks/T16-turnaround-noise.md) | Turnaround: only real conflicts are incidents | B | P1 | T05 ✅ | ✅ #15, #21 🔍 — dev unmatched 137→39 |
| [T08](tasks/T08-logistics.md) | Waste, vending, robot bin runs | B | P1 | — | ✅ #16 — 78 % in time, 16 % unnecessary |
| — | Robustness check (trimmed dev copies) | R | P1 | — | ✅ #17 — no crashes; comfort warm-up fix #19 🔍 |
| [T14](tasks/T14-pitch-video.md) | Pitch, demo script, video | C | P0 | T10, T11 | humans, Sat |
| [T15](tasks/T15-submission-repo.md) | Submission logistics, Proximata org repo | R | P0 | — | humans, now |

**Claiming:** before starting a ticket, post "Taking TNN" in the team chat and push the empty branch `tNN-<slug>` right away
— an existing branch means the ticket is taken. Open tickets touch disjoint files (T06 `s05_surge.py`, T16 `s01_turnaround.py`,
T08 `logistics.py`), so they can run in parallel; `plato40/planning/housekeeping.py` changes need a heads-up in the chat.

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

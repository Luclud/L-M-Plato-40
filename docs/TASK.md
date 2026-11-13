# Task brief — PLATO · CLARA Future: The Autonomous Building

Proximata Hackathon '26 · Home of Innovation, Vienna · 13–14 Nov 2026 · Track partner **Rhomberg** · Prize €1,500.
Consolidated from the organiser case book and `data/*/CHALLENGE.md` (the latter is authoritative where they differ).

## 1. Context
- **PLATO** (Rhomberg product): elevated modular timber buildings on stilts **above existing car parks**
  (grid = parking-space grid; staff/student housing, serviced apartments, self-check-in hotels).
- **CLARA** (Rhomberg product): building operating system — energy, PV, automation, room climate, sensors,
  maintenance, KPIs; today monitoring + optimisation.
- **CLARA Future** (challenge concept): the building largely runs itself — agents & robots act, humans stay
  in the loop for safety, privacy and cost. Loop: PLATO builds → CLARA operates → agents act → learn →
  feed back into the **next PLATO building**.
- **PLATO-40**: fictional 40-room, 4-floor self-check-in hotel. All data synthetic. Every finding concerns
  PLATO-40 only. **Say "PLATO-40" in the pitch.** Do not name our product after PLATO/CLARA, no Rhomberg logos.

## 2. Mission (build a system that…)
1. **Observes** — loads and correlates building, booking, access, telemetry, weather, tariff, equipment,
   logistics, staffing, maintenance and guest data.
2. **Understands** — detects operational risks, explains evidence incl. uncertainty, bad data, benign look-alikes.
3. **Decides & acts** — feasible, prioritised actions across domains, executed by agents/robots/automations
   within the autonomy rules (mock executor / own simulation; label simulated effects).
4. **Coordinates** — staff & robot availability, room readiness, comfort, deadlines, guest consent, approvals.
5. **Verifies** — which signals confirm success or require escalation.
6. **Learns & feeds back** — evidence-based recommendations for CLARA (operating rules) and PLATO (design).

## 3. Scenarios
Each class occurs several times per year with varying severity; data also contains benign look-alikes and
long normal stretches. Scenarios are problem classes, not timestamps.

**Level 1 — mandatory**
- **S01 The Perfect Checkout** — early departure + early check-in request + tight cleaning shift →
  turnaround conflict. Never release an uncleaned room; prioritise cleaning; give the guest a realistic time.
- **S02 The Hidden Water Leak** — unusual consumption: loud, barely above normal, or only in the gap between
  main meter and room meters. Separate from guests, cleaning, meter artefacts; inspect and escalate safely.
- **S03 Energy vs Comfort** — cold weather + dynamic prices + reduced heating performance + occupied rooms.
  Protect occupants, use the forecast, organise service — don't blindly minimise cost.

**Level 2 — advanced**
- **S04 The Maintenance Cascade** — filter degradation + missing sensor readings + unavailable technician.
  Tell a data gap from real degradation; organise a realistic service.
- **S05 The Unexpected Weekend** — event weekend fills the house while a cleaner calls in sick. Plan cleaning,
  robots, on-call staff; inform guests; never invent capacity.
- **Building & energy optimisation** — day-ahead prices, PV, battery, weather forecast → shift load within the comfort band.
- **Cleaning & logistics** — robots, waste bins, vending machine: plan robot tasks, order extra pickups/restocks in time.

**Level 3 — learn & feed back**
- What should CLARA do differently next year (thresholds, cleaning strategy, pre-heating, maintenance intervals, alarm tuning)?
- What should PLATO build differently? Properties that only show over a year — find, prove with data, turn into design recommendations.
- Sketch the target system: data, agents, robots, technologies, interfaces.

## 4. Autonomy rules (implemented in `plato40/decision.py`)
| Level | Examples | Rule |
|---|---|---|
| Autonomous | schedule cleaning, dispatch robots, notify guests, vacant-room setpoints, occupied rooms **within** comfort band (20–23 °C), battery/load shifting, order waste pickup/restock, flag anomalies | execute in mock executor; log + explain |
| Human approval | dispatch technician/contractor, block a room, move a booked guest, anything with notable cost | `requires_human_approval = true` |
| Approval + guest consent | entering an occupied room, water shutoff, safety-relevant interventions | both flags true; consent is **needed, not granted** |

Also: a proposed guest message is not proof of delivery; a simulated effect is not a measurement; never schedule
absent staff/robots; never release an uncleaned room; never silently trade safety or comfort for an energy metric.

## 5. Realism traps (from the brief)
- Data-quality issues all year; blank = missing (never zero); implausible values exist.
- BMS alarms are delayed, incomplete, sometimes false — a clue, not ground truth.
- Information has a **time of availability** (forecasts, tariffs, roster changes) — decide only on what was known.
- A booking does not guarantee a clean, released room. Two robots clean floors and can carry waste bins; they
  cannot certify or release a room. One on-call housekeeper in the evening at limited capacity.
- No ground truth in the archives.

## 6. Deliverables (repo root)
- `decisions.jsonl` (dev dataset) and `decisions_holdout.jsonl` (holdout) per `DECISION_CONTRACT.md`
  (copy in `data/dev/`). Required: `event_id, timestamp_utc, affected_assets, issue_type, action, priority,
  reason, requires_human_approval`. Recommended: `autonomy, episode_id, evidence, expected_outcome,
  verification_plan, dependencies, guest_request_ids, requires_guest_consent, confidence`.
- `insights.md` (+ optional `insights.jsonl`: `insight_id, target (PLATO|CLARA), topic, finding, evidence[],
  recommendation, expected_benefit, confidence`) — each recommendation with data evidence + uncertainty,
  plus target architecture.
- Git tag **`pre-holdout`** before holdout release.
- `LICENSE` (MIT), runnable README (setup, run command, environment, architecture, assumptions),
  `.env.example` without secrets. Never commit credentials.
- **3-minute end-to-end demo video**, written submission (title, one-line pitch, team, problem, solution,
  stack, links). Optional: live demo link, PDF deck.
- Repository: public, MIT, in the **Proximata GitHub organisation** (path assigned by organiser) —
  this repo must be mirrored/transferred there before code freeze.

**Minimum viable demo:** reproducible pipeline + interface showing evidence, risk, decision, autonomy level /
approval and verification for S01–S03, plus ≥ 1 evidence-based feedback recommendation.
A dashboard alone is not enough; a chat agent without grounded decisions is not enough.

## 7. Holdout evaluation
- Third unseen dataset (same structure, different run), released **Saturday ~11:00** on Discord
  (`PLATO40_v2_holdout_encrypted.7z` is in `data/raw/`; password comes with the release).
- Pipeline must run as **one documented command taking the data dir as argument** → writes decisions.
- Tag `pre-holdout` before release; run **unchanged**; commit `decisions_holdout.jsonl` before code freeze.
- Changes after `pre-holdout` must be justified in README; jury compares both states.
- Automated check: detection per incident, false alarms, alert spam, safety violations (input, not final grade).

## 8. Scoring (100 points)
| Category | Pts | Breakdown |
|---|---:|---|
| Technical implementation | 35 | single-command pipeline incl. holdout run (10); evidence-based detection on dev **and** holdout (10); prioritisation & constraints (8); robustness to bad data & look-alikes (7) |
| Autonomous operation & impact | 30 | safe, proportionate autonomy (10); cross-domain coordination of staff, robots, HVAC, logistics (8); guest communication & deadlines (6); energy/comfort/efficiency trade-offs (6) |
| Learning loop & vision | 15 | evidence-based feedback to CLARA and PLATO (10); target architecture (5) |
| Demo & presentation | 20 | scenario demo & visualisation (8); decision & action trail (7); limitations & verification (5) |

**Fairness:** no points for fabricated execution, unsafe actions or savings presented as real; simulated estimates
welcome if labelled. Repeated alerts for one incident = one detection; **alert spam counts negatively**; false alarms
judged across normal operation. Ties → safer, more reproducible, better-evidenced solution.

## 9. Scope
In: autonomous-operation prototypes, AI agents / multi-agent systems, robot task planning, mock executors, own
simulations / digital-twin views, energy optimisation, logistics, guest communication workflows, auditability,
feedback to building design. Out: integration with real PLATO/CLARA systems, physical robots, real guest data,
full booking backend, real safety-critical control, claims of measured savings.

## 10. Schedule
| When (Europe/Vienna) | What |
|---|---|
| Fri 13 Nov 14:00 | Hacking starts |
| Sat 14 Nov ~11:00 | Holdout release (Discord) |
| Sat 14 Nov 11:00 | n8n & Adaption workshop (optional) |
| **Sat 14 Nov 14:00** | **Code freeze** per dataset CHALLENGE.md (case book says 15:00 — we plan for 14:00) |
| Sat 15:00–16:00 | Track judging: live demo |
| Sat 16:00 | Track winners pitch to external jury (VCs, founders, tech leads), winner announcement |
| Sat 18:00 | End / networking |

## 11. Licences & IP
Data: CC BY 4.0 — attribution "PLATO-40 synthetic dataset, Proximata Hackathon '26 – Rhomberg track".
Code: MIT, we keep copyright; Rhomberg gets a perpetual, worldwide, royalty-free, non-exclusive licence.
Third-party components must be MIT-compatible. No confidential or personal data.

# AGENTS.md — instructions for AI coding agents (Devin & co.)

You are implementing part of a 24-hour hackathon solution. Read this file fully, then
`docs/TASK.md` (what we must deliver), then **your ticket** in `docs/tasks/`. Ticket files
are self-contained specs with acceptance criteria — implement exactly one ticket per session/PR.

## Project in one paragraph
Offline replay of one synthetic year of **PLATO-40**, a fictional 40-room self-check-in
hotel. Domain agents ("detectors") watch the data at 15-min steps, detect problems
(turnaround conflicts, leaks, heating vs comfort, filter degradation, event weekends,
energy, logistics), decide actions within strict autonomy rules, and write
`decisions.jsonl`. Plus `insights.md` with lessons for CLARA (operations) and PLATO (design).
Judges re-run us on an **unseen holdout dataset**, so generalise — never hard-code dates/rooms.

## Setup & commands
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                    # pandas, numpy, tzdata + pytest

python run.py data/quickstart --out out/qs.jsonl       # 21 days, ~1 s
python validate.py out/qs.jsonl data/quickstart        # MUST print RESULT: PASS
python eval/evaluate.py out/qs.jsonl --labels eval/labels_quickstart.json   # detection score
python run.py data/dev --out out/dev.jsonl             # 365 days, ~10 s (+ your detector)
python eval/evaluate.py out/dev.jsonl --data data/dev  # weak labels from work orders
python -m pytest -q                                    # all tests green
python run.py data/quickstart --out out/qs.jsonl --detectors water   # run only one agent
```
Data is in the repo: `data/quickstart/`, `data/dev/` (see `docs/DATA.md`). `out/` is git-ignored.

## Definition of done (every PR)
1. `python -m pytest -q` green, including new tests for your ticket.
2. `validate.py` PASS on **quickstart and dev** (0 violations; explain any warnings in the PR).
3. Ticket acceptance criteria met — paste the `eval/evaluate.py` table into the PR description.
4. Runtime of the full dev run stays < 60 s.
5. README / docs updated if you add actions, fields, files or assumptions.
6. Only touch the files listed in your ticket (+ new tests). Shared modules: see "Shared code".

## Hard rules (violating any of these loses points or disqualifies)
- **No future data.** A decision at time *t* may only use information known at *t*.
  Read via `ctx.asof` (tariff by `published_utc`, forecast by `issued_utc`, roster by
  `status_known_utc`, work orders by `created_utc`, bookings by `created_utc`) or causal
  (backward-looking) rolling windows. Never `shift(-n)`, centred windows, or whole-period stats
  computed *including* the future for a decision (baselines: expanding/rolling up to *t*).
- **Missing ≠ zero.** Empty fields are NaN. A data gap is "unknown", never "normal" or "fixed".
- **Safety flags only via policy.** Create decisions only with `plato40.decision.make_decision`.
  Pass context hints (`enters_occupied_room`, `notable_cost`, `safety_relevant`, `service`,
  `outside_comfort_band`); never set `requires_human_approval`/`requires_guest_consent` by hand.
  Never weaken `plato40/decision.py` policy or `validate.py` checks.
- **Never release an uncleaned room** (`State.can_release`), never schedule absent staff/robots
  (`State.available(resource, when, known_at=t)`), robots never release rooms or enter occupied rooms,
  `ONCALL_1` only 15:30–22:00 local, one room at a time, for waiting guests.
- **Consent needed ≠ consent granted.** A proposed message (`message` field) is not a delivery.
  Label any simulated effect in `simulated_effect`. Never claim real savings.
- **One episode per incident.** Use `ctx.log.add(..., episode_key=...)`. Follow-up decisions only when
  the situation changes (escalation, new evidence, resolution). Alert spam is penalised.
- **Canonical issue types only** (`plato40.decision.ISSUE_TYPES`). Actions from the vocabulary;
  new actions go into `EXTRA_ACTIONS` **and** README "Action vocabulary".
- **Never hard-code** dates, room ids, or thresholds tuned to one incident. Baselines must be learned
  from the dataset being run (holdout is a different run).
- **`plato40/` must never read `eval/`** (labels are for evaluation only; a test enforces this).
- No network calls in the default pipeline. LLM use is optional, behind `.env`, with template fallback.
- No secrets in git. Say "PLATO-40" (fictional), never claim facts about real PLATO/CLARA.

## Architecture (see docs/ARCHITECTURE.md)
`io.load` → `State` → `engine.run` (15-min clock) → detectors (`plato40/detectors/*.py`,
`TriggerDetector`: `build_triggers` causal features → `handle` decisions) →
`DecisionLog` (episodes) → `decisions.jsonl` → `validate.py` / `eval/evaluate.py` / viewer.

## Writing a detector
Copy the pattern in `plato40/detectors/data_quality.py`:
```python
class WaterAgent(TriggerDetector):
    name = "water"
    def build_triggers(self, data, state):          # vectorised, CAUSAL features only
        yield ts, "leak_suspected", {"room": "R104", "rate": 27.0}
    def handle(self, ctx, ts, kind, payload):       # called at first tick >= ts
        ctx.log.add(make_decision(t=ctx.t, affected_assets=[...], issue_type="water_leak",
                    action="request_inspection", priority="high", reason="...",
                    enters_occupied_room=ctx.state.occupied(room, ctx.t),
                    evidence=[f"room_water.csv:{room}:{iso(a)}..{iso(b)}"],
                    verification_plan="...", confidence=0.8, agent=self.name),
                    episode_key=f"water:{room}")
```
Every decision should carry: `evidence` (file:asset:time-range), `verification_plan`
(which signal confirms success / triggers escalation), `confidence`, `expected_outcome`.

## Shared code (coordinate before changing)
`plato40/io.py`, `asof.py`, `state.py`, `decision.py`, `log.py`, `engine.py`, `validate.py`.
If your ticket needs a change there, keep it backwards compatible, add a test, and mention it
in the PR title (`[shared]`). Planning helpers live in `plato40/planning/` (ticket T05).

## Git workflow
- Branch `t<NN>-<slug>` from `main`, one ticket per PR, small commits, rebase on `main` before PR.
- PR description: what/why, eval table before → after, validate summary, known limitations.
- Never commit `out/`, `.env`, decrypted secrets. Do not rewrite history on `main`.
- Deadline-critical: see `docs/PLAN.md` (code freeze Saturday 14:00 Europe/Vienna).
- **After the `pre-holdout` tag (Sat ~10:30):** only `viewer/`, `.streamlit/`, `docs/`, `README.md`, `video/` may change.
  Never touch `plato40/`, `run.py`, `validate.py`, `analysis/`, `requirements.txt`, `insights.*` or `decisions*.jsonl`.
  Every commit after the tag gets a row in README "Changes after pre-holdout" (organizer rule, `docs/ENDGAME.md` §0b).

## Where things are
| Path | What |
|---|---|
| `docs/TASK.md` | Full task brief, scoring, rules, deliverables |
| `docs/DATA.md` | Dataset reference, quirks, incident map, open questions |
| `docs/ARCHITECTURE.md` | Current design + target system |
| `docs/PLAN.md` | Milestones, ticket board, timeline |
| `docs/DECISIONS.md` | Why we chose what (read before re-arguing a choice) |
| `docs/tasks/T*.md` | Tickets |
| `eval/` | Labels + evaluator (dev aid only) |
| `data/` | Datasets (CC BY 4.0) |

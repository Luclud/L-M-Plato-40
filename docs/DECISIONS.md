# Decision log (ADR-lite)

Read before re-arguing a choice. Add new entries at the bottom: date, decision, why, consequences.

| # | Decision | Why | Consequence |
|---|---|---|---|
| D1 | Python 3.11+ with pandas/numpy only for the pipeline | fast to write, everyone knows it, jury can run it | no heavy deps; extra libs need justification + MIT-compatible licence |
| D2 | Replay clock (15 min) + causal triggers precomputed per agent | provably no future data, easy to explain, fast (dev year ~10 s) | agents must keep `build_triggers` causal |
| D3 | Autonomy flags derived by policy in `decision.py`, re-checked by `validate.py` | "safe by design" is 10 points; impossible for an agent to forget a flag | agents pass context hints only |
| D4 | Episodes in `DecisionLog` | repeated alerts count once; spam penalised | every agent defines episode keys + closes them |
| D5 | Canonical `ISSUE_TYPES` | evaluator + automated jury check match by type | `make_decision` rejects unknown types |
| D6 | LLM optional, only for wording (guest messages, approval notes, insights prose); never for detection, priority, flags, scheduling, release | reproducibility, holdout run without keys, judges reward grounded results | templates must cover all cases |
| D7 | Data committed in repo (`data/quickstart`, `data/dev`, raw archives) | agents (Devin) need it without manual download; CC BY 4.0 allows it | repo ~25–30 MB packed; never commit decrypted secrets |
| D8 | Plan for code freeze **Sat 14:00** | dataset CHALLENGE.md says 14:00, case book 15:00 — take the earlier | pre-holdout tag by 10:30, holdout run ~11:00–12:00 |
| D9 | Quickstart hand labels + dev weak labels (work orders, waiting events) in `eval/` | measurable acceptance criteria for agents | labels are hints; `plato40/` must never read them (test) |
| D10 | Viewer = Streamlit (T10) | fastest path to a demo UI in Python reading `decisions.jsonl` | optional deploy via Docker on our server |
| D11 | Robots used for floor cleaning + bin carrying, never for release / occupied rooms | building.json capabilities | planners must model robot availability (`out_of_service`) |
| D12 | `ONCALL_1` treated as approval-level (`call_oncall_housekeeper`, notable cost) | building.json: "costly, for waiting guests only" | S01/S05 escalate via approval |

# Running the tickets with Devin

## One-time setup (Devin repo setup)
- Repo: this repository, default branch `main`.
- Setup commands:
  ```bash
  python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
  python run.py data/quickstart --out out/qs.jsonl && python validate.py out/qs.jsonl data/quickstart
  ```
- Devin reads `AGENTS.md` (root) and `plato40/detectors/AGENTS.md` automatically.
- No secrets required. Network is not needed for the pipeline.

## Session prompt (copy, replace TNN)
```
You are working on the L-M PLATO-40 hackathon repo. Read AGENTS.md, docs/TASK.md and docs/DATA.md,
then implement ticket docs/tasks/TNN-*.md exactly. Work on branch tNN-<slug> from main.
Definition of done: pytest green, validate.py PASS on quickstart and dev, ticket acceptance criteria met.
Paste the eval/evaluate.py table (quickstart + dev) before/after and the validate summary into the PR
description. Only touch files listed in the ticket (+ tests). Do not modify plato40/decision.py policy,
validate.py checks or eval/ labels. Never use future data (ctx.asof / causal windows only). Open a PR when done.
```

## Wave 1 (parallel, disjoint files)
T01 water · T02 comfort · T03 maintenance · T05 turnaround+planner · T09 messaging · T10 viewer · T11 insights

## Wave 2 (after merges)
T06 surge (needs T05) · T04 guest service (needs T09) · T07 energy · T08 logistics · T13 release check

## Wave 3
T12 BMS compare (needs T01–T03) · re-run T11 with final outputs

## Review checklist for humans (per PR)
- [ ] `git diff --stat` only ticket files (+ tests); any `[shared]` change justified and tested
- [ ] eval table improved, unmatched episodes listed and plausible (look at 3 of them in the data)
- [ ] no `shift(-`, `center=True`, full-period `.mean()` used for decisions; as-of reads for roster/tariff/forecast/work orders
- [ ] no hard-coded dates/rooms; thresholds learned or physically motivated
- [ ] validate PASS on quickstart + dev, runtime OK
- [ ] after merge: `python -m pytest -q` on main, re-run both datasets

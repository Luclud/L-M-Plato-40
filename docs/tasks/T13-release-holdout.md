# T13 — Release check & holdout runbook

**Lane:** R release · **Priority:** P0 · **Depends on:** none (run again before 10:30 Sat) · **Score impact:** single-command pipeline incl. holdout (10)

## Goal
Make the holdout run boring: one script proves a fresh clone works, produces the root deliverables and passes all checks.

## Deliverables
- `scripts/release_check.sh` (bash) + `scripts/release_check.ps1` (Windows): fresh `git clone` into a temp dir → venv → install →
  `pytest -q` → `python run.py data/dev --out decisions.jsonl` → `validate.py` PASS → `eval/evaluate.py` summary → check that
  `LICENSE`, `README.md`, `.env.example`, `insights.md`, `decisions.jsonl` exist at root, no `.env` committed → exit non-zero on any failure.
- `docs/RELEASE.md` step list for Saturday:
  1. 10:30 run release_check, commit root `decisions.jsonl` (dev), `git tag pre-holdout && git push origin pre-holdout`.
  2. ~11:00 get password from Discord → `7z x -p'<pw>' data/raw/PLATO40_v2_holdout_encrypted.7z -odata/holdout` (do not commit the
     password) → `python run.py data/holdout --out decisions_holdout.jsonl` (code unchanged!) → `validate.py` → commit + push.
  3. If a fix is unavoidable after the tag: separate commit, justification in README "Changes after pre-holdout", re-run holdout.
  4. Before 14:00: repo in Proximata org (see T15), video link, submission form.

## Acceptance criteria
`scripts/release_check.sh` passes on a clean machine (CI-like run in Devin's VM) and finishes < 5 min.

## Files to touch
`scripts/release_check.sh`, `scripts/release_check.ps1`, `docs/RELEASE.md`, README (Changes after pre-holdout section placeholder).

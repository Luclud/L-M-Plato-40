# Release & holdout runbook (Saturday 14 Nov, Europe/Vienna)

Goal: make the holdout run boring. One script proves a fresh clone works, produces the root
deliverables and passes all checks; the holdout is then run with **unchanged** code.

## The release check

```bash
scripts/release_check.sh                 # Linux/macOS/Git Bash
powershell -ExecutionPolicy Bypass -File scripts\release_check.ps1   # Windows (pwsh works too)
```

It tests the **committed** `HEAD` (uncommitted edits are ignored, a warning says so):
fresh `git clone` into a temp dir → venv → `pip install -r requirements-dev.txt` → `pytest -q` →
`python run.py data/dev --out decisions.jsonl` → `validate.py` must print `RESULT: PASS` →
`eval/evaluate.py` summary → `LICENSE`, `README.md`, `.env.example`, `insights.md`, `decisions.jsonl`
exist at the root and are committed, no `.env` committed. Any failure → exit code ≠ 0 and the last
40 log lines; full log in `out/release_check.log`. Budget < 5 min (≈ 25 s with a warm pip cache).

Warnings (do not fail the check, but fix before tagging): root `decisions.jsonl` differs from a fresh
run (stale), tag `pre-holdout` missing, LICENSE not MIT, run took longer than `--max-seconds`.

| Option (`.sh` / `.ps1`) | Effect |
|---|---|
| `--repo PATH\|URL` / `-Repo` | clone something else, e.g. `https://github.com/<org>/<repo>.git` after the transfer (T15) |
| `--ref REF` / `-Ref` | check out a branch, tag (`pre-holdout`) or commit |
| `--holdout DIR` / `-Holdout` | also run + validate the holdout → `decisions_holdout.jsonl` (data/holdout is git-ignored, so the path is resolved against your working copy) |
| `--copy-back` / `-CopyBack` | copy the regenerated `decisions*.jsonl` + `*_dq.json` into your working copy |
| `--python EXE` / `-Python` | python used for the venv (≥ 3.10) |
| `--keep` / `-Keep` | keep the temp clone for debugging |

## Saturday step list

### 1. 10:30 — freeze for holdout
```bash
git switch main && git pull --ff-only
scripts/release_check.sh --copy-back         # must end with RELEASE CHECK: PASS
git add decisions.jsonl decisions_dq.json && git commit -m "Release: dev decisions.jsonl"   # skip if unchanged
git push origin main
scripts/release_check.sh                     # optional: re-check the pushed commit, no stale warning
git tag pre-holdout && git push origin pre-holdout
```
From now on the pipeline code is frozen. `insights.md` / docs edits are fine, code is not.

### 2. ~11:00 — holdout release (password from Discord)
Do **not** commit, paste or log the password (type it into your own shell only).
```bash
(cd data/raw && sha256sum -c --ignore-missing SHA256SUMS.txt)   # archive intact
7z x -p'<pw>' data/raw/PLATO40_v2_holdout_encrypted.7z -odata/holdout   # needs 7-Zip: apt install p7zip-full / 7-zip.org
python run.py data/holdout --out decisions_holdout.jsonl          # code unchanged! (dir or its parent both work)
python validate.py decisions_holdout.jsonl data/holdout           # RESULT: PASS
git add decisions_holdout.jsonl decisions_holdout_dq.json && git commit -m "Holdout run (pre-holdout code)" && git push
```
Alternative in one go, from a clean clone of the tagged code:
`scripts/release_check.sh --ref pre-holdout --holdout data/holdout --copy-back`, then commit the two holdout files.
`data/holdout/` is git-ignored — do not commit the decrypted data. Never assume the holdout period/profile
(not January, not 365 days); if the run fails, read the error before touching code.

### 2b. Viewer / docs changes after the tag (allowed, organizer clarification Sat 14 Nov)
Visualisation, dashboard, README and demo changes are fine after `pre-holdout` (`viewer/`, `.streamlit/`, `docs/`,
`README.md`, `video/`). Never `plato40/`, `run.py`, `validate.py`, `analysis/`, `requirements.txt` or the deliverables.
Add one row per commit to README → "Changes after pre-holdout" (files + why + "no effect on `decisions_holdout.jsonl`").
Their automated check flags every change after the tag; that row is how they verify it. Details: `docs/ENDGAME.md` §0b.

### 3. Only if a fix is unavoidable after the tag
- Separate commit (no amend, no force-push, never move the `pre-holdout` tag).
- Justify it in README → "Changes after pre-holdout" (what, why, which incidents/rows it changes).
- Re-run the release check and the holdout, commit the new `decisions_holdout.jsonl`; the jury compares both states.

### 4. Before 14:00 — code freeze & submission
- Repo transferred/mirrored into the Proximata GitHub org (T15); run `scripts/release_check.sh --repo <new URL>` once.
- Video link (3 min) and written submission form (title, pitch, team, problem, solution, stack, links).

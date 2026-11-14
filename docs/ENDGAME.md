# Endgame — from "frontend done" to submission

State Fri 13 Nov 22:30 (main @ 545abb3): pipeline complete (quickstart 7/7, dev 39/40, validate PASS, 326 tests, dev run ~20 s,
robustness 4/4 PASS). Viewer: T17–T21 merged; open T22 tour → (T24, T25 optional) → T23 polish.
Code freeze **Sat 14 Nov 14:00 sharp** · holdout release **11:00** (Discord) · live demo **15:00–16:00**.

## 0. Decisions only we (humans) can make — needed tonight
| # | Decision | Default if nobody decides | Needed by |
|---|---|---|---|
| D-1 | ✅ **Product name: Steward** — "Runs the building, answers to people" (`APP_NAME`, README, pitch) | — | done |
| D-2 | **Pipeline freeze now?** No more changes to `plato40/`, `run.py`, `validate.py` unless a test fails | Yes, from 23:00 | 23:00 |
| D-3 | Learning loop: add "Proposed rule changes for next year" table with mock approve/adjust (option A) to *What we learned* | Yes (viewer only, ~30 min) | before T23 |
| D-4 | AI layer: run T24 summaries / T25 copilot? Who holds the Claude key, on which machine? | Only if T22 is merged by 00:30; key on Luca's laptop `.env` | 00:30 |
| D-5 | Live demo dataset: tour on **quickstart**, holdout results on the Proof page | as stated | Sat 11:30 |
| D-6 | Viewer: local laptop only, or also hosted (live demo link in the submission) | local only | Sat 12:00 |
| D-7 | Who presents, who drives the laptop, who records the video; team names for the submission | Luca presents + drives | Sat 09:00 |
| D-9 | **Install 7-Zip** on the laptop that runs the holdout; who runs it at 11:00 | Luca's laptop | tonight |
| D-8 | UI feedback after clicking through the new viewer (only a human can judge "easy to understand") | — | before T23 |

## 0b. Organizer clarification (Sat 14 Nov, Rhomberg track): UI changes after `pre-holdout` are allowed
The pre-holdout rule freezes **detection and decision logic, not the UI**. Visualisations, dashboards, README and demo
may change after the tag, as long as:
1. `decisions_holdout.jsonl` is produced by the pipeline **at the `pre-holdout` tag**, with no change to logic,
   thresholds, prompts or data processing.
2. Every change after the tag is listed in README → "Changes after pre-holdout" (files/folders + one line why).
   Their automated check flags **every** code change after the tag; the README note is how they verify it.
   Tip from them: keep visualisation code in its own folder (ours: `viewer/`), so the check is quick.

**What this means for us**

| After the tag | Allowed? |
|---|---|
| `viewer/` (pages, charts, CSS, assets), `.streamlit/`, `docs/`, `README.md`, `video/` | ✅ yes, log each commit in the README table |
| `plato40/` (incl. message templates / LLM prompts in `messaging.py`), `run.py`, `validate.py`, `requirements.txt` | ❌ no (only an unavoidable crash fix, see `docs/RELEASE.md` §3) |
| `analysis/` (generates `insights.md`), `insights.md` / `insights.jsonl`, `decisions.jsonl`, `data/` | ❌ no (counts as data processing / deliverable) |

**Therefore before the tag (by 10:30):** anything that changes `insights.md` (e.g. the Earned-autonomy CLARA
recommendation from `analysis/`), `release_check --copy-back` + commit the refreshed root `decisions.jsonl`
(the committed one is 1 decision behind the current code: battery plan 2026-06-16).
**After the holdout (11:30–13:30):** viewer-only work (Earned-autonomy table, Steward-vs-BMS scoreboard,
year heatmap on the floor plan), one README line per commit. Stop at 13:45.

## 1. Tonight after the frontend (until ~01:00)
1. **Click through the viewer** on quickstart and dev (D-8). Note anything confusing → one fix list for T23.
2. **Pipeline freeze** (D-2): after this, only release steps touch the pipeline outputs.
3. **Headline numbers for the pitch**: detection lead times vs BMS alarm and vs guest complaint, per scenario (Claude).
4. **Pitch storyline + submission draft** (title, one-line pitch, team, problem, solution, stack, links).
5. If D-4 = yes: put the key in `.env` on the demo laptop, `python scripts/summarise_incidents.py ...`, build the copilot cache.
6. Merge T23 when green; check `docker compose up --build viewer` once.
7. **Sleep.** Alarm 08:30.

## 2. Saturday morning — release (09:00–10:30)
1. `git pull` on the demo laptop; `python -m pytest -q`; run quickstart + dev; check 7/7 and 40/40.
2. `python analysis/insights.py data/dev --out insights.md --jsonl insights.jsonl` → commit if changed.
3. `scripts/release_check.ps1 -CopyBack` (Windows) or `scripts/release_check.sh --copy-back` → must end with
   `RELEASE CHECK: PASS`; commit the refreshed `decisions.jsonl` / `decisions_dq.json` if they changed (see `docs/RELEASE.md` §1).
5. README final pass: name, one-line pitch, team, run command, holdout section, "Changes after pre-holdout" (empty table, filled after the tag per §0b), viewer.
6. **`git tag pre-holdout && git push origin pre-holdout` by 10:30.** From here: no pipeline/analysis changes; viewer, docs, README and video only, each listed in README "Changes after pre-holdout" (see §0b).
7. Rehearse the guided tour twice (time it, ≤ 3 min) incl. the live approval click.

## 3. Holdout (11:00–11:45) — exact commands in `docs/RELEASE.md` §2
1. Get the password from Discord (never paste it into git, chat or logs); check `data/raw/SHA256SUMS.txt`; unpack
   `data/raw/PLATO40_v2_holdout_encrypted.7z` into `data/holdout/` with **7-Zip** (install it tonight on the demo laptop).
2. `python run.py data/holdout --no-llm --out decisions_holdout.jsonl` — **unchanged code** (the `pre-holdout` tag). `--no-llm`
   because the Claude key sits in `.env` on this laptop (D-4): without it every guest message would be an API call.
3. `python validate.py decisions_holdout.jsonl data/holdout` → PASS; `python scripts/robustness_check.py --data data/holdout`
   optional sanity run.
4. Commit `decisions_holdout.jsonl` + `decisions_holdout_dq.json` and push (`data/holdout/` is git-ignored — never commit it).
5. Open the viewer on the holdout run: note the numbers for the pitch ("unseen data: N incidents, 0 violations").
6. If something crashes: fix minimally, document it under "Changes after pre-holdout" in the README, re-run, commit both.

## 4. Submission (11:45–13:45)
1. Repo into the **Proximata organisation** at the assigned path (public, MIT): push a mirror with tags
   (`git remote add proximata <url> && git push proximata main --tags`). Check the tag is there.
2. **3-minute video**: record the guided tour on the final build (screen + voice), upload, link in README.
3. **Written submission**: title, one-line pitch, team, problem, solution, stack, links (repo, video, optional live demo).
4. Final check list (repo root): LICENSE (MIT) · README runnable · `.env.example` (no secrets) · `decisions.jsonl` ·
   `decisions_holdout.jsonl` · `insights.md` · tag `pre-holdout` · no `.env` / keys committed.
5. **13:45** stop. **14:00 code freeze.**

## 5. Live demo (15:00–16:00)
- Laptop charged, viewer already running (`--server.headless true`), first load done (cache warm), Wi-Fi not required
  (AI answers cached). Backup: the video and screenshots.
- Flow: guided tour (≈ 3 min) → Proof page with holdout numbers and the safety scorecard → questions.
- Say "PLATO-40" for every finding; show limitations on purpose (remaining unmatched turnarounds; weak labels are not ground truth).

## 6. Cut list if late
T25 copilot → T24 summaries → option A table → hosted demo link. Never cut: `pre-holdout` tag, holdout run, validator PASS,
the submission items in §4.4, the guided tour.

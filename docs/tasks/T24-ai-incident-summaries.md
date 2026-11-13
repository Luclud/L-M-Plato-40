# T24 — AI incident summaries (optional, explain-only)

**Lane:** C · **Priority:** P2 · **Depends on:** T18 · **Score impact:** demo & presentation (20), guest/decision trail readability

## Goal
2–3 plain-language sentences per incident for the Incidents list and story page (`docs/FRONTEND_PLAN.md` §7a). AI explains
existing decisions only — never detects, decides or changes flags (docs/DECISIONS.md D6).

## Do
- `scripts/summarise_incidents.py <decisions.jsonl> --data <dir> --out out/<run>_summaries.json`: for each incident episode,
  build a compact fact sheet (friendly type, assets, local times, actions, autonomy, evidence details, verification plan) and
  ask Claude for a 2–3 sentence summary in English. Model + key from `.env` (`LLM_PROVIDER=anthropic`, `LLM_API_KEY`,
  `LLM_MODEL`, default a fast model such as `claude-haiku-5-5`); reuse `plato40/llm.py` (HTTP, cache) — no new pipeline dependency.
- Guard: reject a summary if it contains any number, time, room/asset id or resource not present in the fact sheet, or claims
  delivery/execution ("we sent", "was repaired"); then fall back to a deterministic template summary. Record
  `source: llm|template` per incident. Cache by fact-sheet hash; temperature 0.
- `viewer/summaries.py`: load the file if present next to the decisions file; T18 shows the summary under the headline with
  the label "AI summary — facts from the decision log" (template: no label).
- Never run in `run.py` / the holdout pipeline.

## Acceptance criteria
- Without a key: script writes template summaries for every incident; viewer works unchanged.
- With a mock client in tests: guard rejects altered numbers / ids / delivery claims; cache hit on second run.
- Quickstart + dev: one entry per incident episode; script runtime noted in the PR. Key never committed.

## Files to touch
`scripts/summarise_incidents.py`, `viewer/summaries.py`, `viewer/views/incident*.py` (display only), `tests/test_summaries.py`,
`.env.example` (comment only).

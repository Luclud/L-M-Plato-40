# T09 — Multilingual guest messaging + approval notes

**Lane:** B operations · **Priority:** P0 · **Depends on:** none (replace bodies in `plato40/messaging.py`) · **Score impact:** guest communication & deadlines (6), demo (8)

## Goal
Realistic, deadline-aware guest messages in the guest's language (de, en, it, fr, es, nl) and short approval notes for humans.
Deterministic templates by default; optional LLM wording behind `.env` with cache and template fallback.

## Interfaces (keep signatures from the stub)
- `compose_guest_message(kind, language, **facts) -> {"language","text","source"}` for all `KINDS` in 6 languages.
- `parse_requested_time(text) -> "HH:MM" | None` — robust for all early_checkin / late_checkout / arrival_delay texts
  ("gegen 14:30", "bis 12:00", "at 12:45", "vers 14h15", "alle 13:00", "om 13.00").
- `approval_note(reason, evidence, proposal, if_no_action) -> str`.
- Optional: `plato40/llm.py` — provider from `.env` (`LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`), temperature 0, on-disk cache
  keyed by hash; any error → template. `--no-llm` flag in `run.py` forces templates. Never let LLM change facts (time, room).

## Acceptance criteria
- Unit tests: every kind × language renders, contains the facts (room, time), no placeholder left.
- `parse_requested_time` extracts a time for ≥ 95 % of dev early_checkin + late_checkout texts (report coverage in PR).
- Pipeline runs identically without `.env`. Messages marked `source: template|llm`.

## Pitfalls
Never imply delivery ("we sent"); use local time with DST; polite register per language (Sie/vous/Lei/usted/u).

## Files to touch
`plato40/messaging.py`, optional `plato40/llm.py`, `run.py` (wire `--no-llm`), `tests/test_messaging.py`, `.env.example`.

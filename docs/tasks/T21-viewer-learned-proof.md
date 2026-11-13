# T21 — "What we learned" + "Proof" pages

**Lane:** C · **Priority:** P0 · **Depends on:** T17 · **Parallel with:** T18, T19, T20 ·
**Score impact:** learning loop & vision (15), limitations & verification (5), robustness (7)

## Goal
Implement `docs/FRONTEND_PLAN.md` §5.5 and §5.6.

## Do
- **What we learned** (`insights.jsonl` + `insights.md`): two columns "For the next PLATO building" / "For CLARA next year",
  one card per insight (topic, first sentence of finding, recommendation, expected benefit labelled as estimate, confidence
  chip, "show full evidence" expander). Below: target architecture (render the `insights.md` section) and Limitations.
  Missing files → friendly empty state with the command to generate them.
- **Proof**: validator result (button "Run check", cached; never on page load), detection vs labels as a friendly table
  only if labels exist ("dev aid — labels are not ground truth"), BMS vs us (T12 output via `core.side_files`, plus a
  one-sentence summary), data-quality summary + gateway outages, known misses (unmatched / undetected), download buttons for
  decisions and insights, and the "Classic views" from T17 until T23.

## Acceptance criteria
- Quickstart + dev: 11 insight cards (5 PLATO, 6 CLARA) render; validator button shows PASS; BMS sentence uses real numbers.
- AppTest on quickstart, dev, empty decisions and missing insights files. PR with screenshots.

## Files to touch
`viewer/views/learned.py`, `viewer/views/proof.py`, `tests/test_viewer_learned_proof.py`.

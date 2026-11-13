# T20 — Approvals grouped per incident

**Lane:** C · **Priority:** P1 · **Depends on:** T17 · **Parallel with:** T18, T19, T21 ·
**Score impact:** safe, proportionate autonomy (10), decision trail (7)

## Goal
Implement `docs/FRONTEND_PLAN.md` §5.4: a human sees in seconds what to decide and why.

## Do
- One card per episode with ≥ 1 `requires_human_approval` decision; most urgent first (priority, then time); Pending / Decided
  filter; 20 per page; nav label shows pending count ("Approvals (12)").
- Card: plain question built from friendly action + asset + scheduled/local time ("Send the technician to the ventilation unit
  on Fri 16 Jan, 08:00?"), one-line why (headline of reason), cost hint when the approval comes from notable cost, red banner
  "Guest consent needed — not granted" when `requires_guest_consent`, Approve / Reject (mock: `core.record_approval` →
  `out/approvals.json`), "Open incident" link. Multiple approval decisions in one episode → listed inside the same card.
- Yellow strip on top: "Mock approvals — nothing is executed; decisions are written to out/approvals.json".

## Acceptance criteria
- Approve/Reject persists across reloads; counts update; nothing outside `out/approvals.json` is written.
- AppTest on quickstart, dev, empty decisions. PR with screenshots.

## Files to touch
`viewer/views/approvals.py`, `tests/test_viewer_approvals.py`.

# Branch `extras` — showcase features outside `main`

`main` is the submission: pipeline frozen, viewer T17–T28. The three showcase features below live **only on the
`extras` branch** until the team decides otherwise. They are viewer-only, so the pipeline output and the holdout run
are identical on both branches.

| Ticket | Feature | Why it stands out |
|---|---|---|
| [T29](tasks/T29-alarm-fatigue.md) | Alarms vs Steward: 926 building alarms vs ~100 incidents in one picture | the operator's problem (alarm flood, missed leaks) in one chart |
| [T30](tasks/T30-plato-design-board.md) | PLATO design board: lessons pinned to a side view of the building | the learning loop made physical, in a builder's language |
| [T31](tasks/T31-guest-conversations.md) | Guest messages: request ↔ Steward reply, deadlines, consent mock | the one screen every judge understands at once |

## Branch rules (for Devin and humans)
- Feature branches start from `extras` (`t29-alarm-fatigue`, `t30-design-board`, `t31-guest-conversations`) and
  their PRs target **`extras`**, never `main`.
- Keep `extras` current: after anything lands on `main` (e.g. T28), merge `main` → `extras` (never the other way).
  Expected conflict: one router line in `viewer/app.py` — keep both entries.
- Never merge `extras` into `main`, never push to `main` from this work, never force-push either branch.
- Each feature = its own new files; shared files are touched only where the ticket says (one router line, one link).
- Same rules as main: pytest green, pipeline output unchanged, wording via `viewer/labels.py`, honesty labels kept.
- Demo from `extras`: `git checkout extras && docker compose up -d --build viewer` (Docker builds from the checkout).

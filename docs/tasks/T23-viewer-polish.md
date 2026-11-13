# T23 — Viewer polish, docs and screenshots

**Lane:** C · **Priority:** P0 · **Depends on:** T22 · **Score impact:** demo & presentation (20)

## Do
- Remove the "Classic views" once every old function is covered by the new pages (check the list in T17 PR).
- Consistency pass: one wording per concept (labels.py only), local time everywhere outside Technical details, empty states,
  long names truncated with tooltip, page titles, favicon/title = `APP_NAME`.
- Performance: dev first load < 10 s, then < 5 s cached; page switch < 1 s; measure and put numbers in the PR.
- `docs/DEMO.md` rewritten around the tour (the tour *is* the live demo and the video script) + a fallback click path.
- New screenshots in `docs/img/` (Building, Incidents, R104 story, Approvals, What we learned, Proof), README "Viewer" section
  updated (start command, Docker, dataset picker).
- `docker compose up --build viewer` works and shows the new UI.

## Acceptance criteria
- Full test suite green; AppTest for all pages and the tour on quickstart, dev, empty; validator untouched.
- Someone who has not seen it before can answer "what happened in Room 104 and what did the system do?" within 30 s.

## Files to touch
`viewer/*`, `docs/DEMO.md`, `docs/img/*`, README Viewer section, `docker-compose.yml` / `viewer/Dockerfile` only if needed.

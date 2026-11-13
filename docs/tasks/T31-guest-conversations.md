# T31 — Guest messages: conversations, deadlines and the consent mock

**Branch:** feature branch from `extras`, PR into `extras` (see `docs/EXTRAS.md`) · **Priority:** P1 ·
**Viewer only** · **Score impact:** guest communication and deadlines (6), safe autonomy (10), demo (8)

## Goal
Show what guests wrote next to what Steward proposed to answer, in the guest's language, with the deadline and whether
it was met; and make "consent needed, not granted" tangible with a mock consent request. Today only Steward's replies
are visible (inside incident stories); the guest's own words are not shown anywhere.

## Data
- `guest_requests.csv`: `request_id, timestamp_utc, room_id, booking_id, channel (app_chat/email/phone/kiosk),
  language, request_type, text, priority, response_due_utc, requires_guest_consent`. Dev: 1,612 requests, 128 need
  consent.
- Decisions with `guest_request_ids` (dev: 1,854 decisions covering 1,610 requests) and their `message`
  (`language, text, source, kind`), `timestamp_utc`, `scheduled_for_utc`, `requires_guest_consent`, `episode_id`.
- Consent-related decisions: `requires_guest_consent == true` (dev: 99; request_inspection 52, schedule_cleaning 44,
  water_shutoff 2, dispatch_technician 1).

## Do
1. `viewer/conversations.py` (pure, cached): `threads(data, df)` → one thread per request: request row + the linked
   decisions in time order; `response_minutes` (first decision with a message − request time) and `on_time`
   (≤ `response_due_utc`); `consent_requests(df)`; `record_consent(path, event_id, status)` writing
   **`out/consents.json`** (mock, like approvals; never touches anything else).
2. `viewer/views/guests.py`, page **"Guest messages (n)"** (router entry after Approvals, icon `:material/chat:`):
   - Left: filterable list (language, request type, channel, room, "needs consent", "late") with search; 25 per page,
     newest first.
   - Right: the selected conversation as a chat: guest bubble (original text, language flag, channel, local time,
     "reply due 17:56"), Steward bubble(s) with the proposed reply, label **"Proposed — not sent"**, source
     (template / AI wording), response time and an on-time chip; system lines for actions taken (e.g. "cleaning
     scheduled 11:00–11:45 · Cleaner 3"); link to the related incident (`?episode=`) when there is one.
   - Consent: when a linked decision needs guest consent, show a consent card in the thread — "May we enter Room 104
     between 10:00 and 11:00? [Guest says yes] [Guest says no]" — clearly labelled **"Mock: simulates the guest's
     answer; consent is needed, not granted, until a real guest answers"**; the choice is stored in
     `out/consents.json` and shown in the thread and on the incident story ("consent: yes (mock)").
   - Summary strip: requests · answered on time % · median response time · languages · consent requests open.
3. Incident story: for decisions with `guest_request_ids`, show the guest's original message above Steward's reply
   (one small addition in `viewer/views/incident.py`).

## Acceptance criteria
- Dev: 1,612 threads, 1,610 with at least one Steward decision; the 2 without are listed as "no reply" (not hidden).
- Quickstart: GR000034 ("Zu kalt zum Schlafen …", R101) shows the guest text, Steward's German reply, the deadline and
  the link to the heat-pump incident.
- On-time % and response times are computed, never typed; times in Europe/Vienna.
- The consent mock writes only `out/consents.json`, survives reloads, and never changes decisions or approvals.
- AppTest on quickstart, dev, empty decisions; unit tests for threads, on-time and consent recording; pipeline output
  unchanged. PR with screenshots (a German, an Italian and a consent thread).

## Files to touch
`viewer/conversations.py`, `viewer/views/guests.py`, `viewer/app.py` (one router line), `viewer/views/incident.py`
(guest text above the reply only), `viewer/labels.py` (channel / request-type labels), `tests/test_viewer_guests.py`.

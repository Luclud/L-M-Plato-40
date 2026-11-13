# T04 — Guest service agent (requests, deadlines, consent)

**Lane:** B operations · **Priority:** P1 · **Depends on:** T09 interface (stub exists) · **Score impact:** guest communication & deadlines (6), safe autonomy (10)

## Goal
Every guest request gets a timely, correct handling decision in the guest's language — before `response_due_utc` —
routed to the right agent, with consent handling for room entry.

## Context
`guest_requests.csv`: `request_id, timestamp_utc, room_id, booking_id, channel, language, request_type, text, priority,
response_due_utc, requires_guest_consent` (`required` = entering needs consent — **not granted**). No request is answered in
the data. Types (dev): early_checkin 349, late_checkout 271, temperature_hot 201, noise 173, wifi_help 140, extra_towels 112,
arrival_delay 87, parking 57, maintenance_issue 50, cleaning_request 44, luggage_storage 44, temperature_cold 29, invoice 19,
access_issue 17, waiting_for_room 14, air_quality 4, water_issue 1.

## Suggested approach
- Ownership: `early_checkin`, `late_checkout`, `waiting_for_room`, `arrival_delay` → **T05 turnaround** (do not duplicate;
  T04 skips them). `temperature_*`, `air_quality` → T02 comfort (T04 adds `notify_guest` ack + `dependencies` only if T02 has
  no decision for that room within 30 min). `water_issue` → T01 water (corroborating evidence).
- T04 handles the rest: `maintenance_issue` (vacant → `request_inspection` approval; occupied → approval + consent,
  `enters_occupied_room=True`, plus `notify_guest` maintenance_consent_request), `cleaning_request`/`extra_towels`
  (`schedule_cleaning` / delivery with a housekeeping slot — only staff on shift; entering occupied room needs consent),
  `noise_complaint` (notify + flag), `access_issue` (high: check `access_log` for denied attempts → `flag_anomaly` +
  notify), `wifi_help`/`parking`/`luggage_storage`/`invoice` (`notify_guest` with standard info).
- Each decision: `guest_request_ids=[request_id]`, `message` from `plato40.messaging.compose_guest_message(kind, language, ...)`,
  timestamp ≤ `response_due_utc`, priority from the request priority.

## Interfaces
New detector `plato40/detectors/guest_service.py` (`GuestServiceAgent`, name `guest_service`), register in
`detectors/__init__.py`. issue_type `guest_request` (or the owning domain's type when it is a symptom, e.g. `water_leak`).
Episode key `guest:<request_id>`.

## Acceptance criteria
- 100 % of T04-owned requests in quickstart and dev have exactly one first decision with their `request_id` and
  `timestamp_utc ≤ response_due_utc`; message language == request language (once T09 lands; until then `requested_language`).
- Occupied-room entry always `requires_guest_consent=true`; validator PASS.
- Script/test prints the response-time distribution (median, p95). Tests `tests/test_guest_service.py`.

## Pitfalls
Requests arrive at night when no staff is on shift → schedule for next shift and say so in the message (realistic time).

## Files to touch
`plato40/detectors/guest_service.py`, `plato40/detectors/__init__.py`, `tests/test_guest_service.py`.

"""S01 The Perfect Checkout.

Early departure + early check-in request + tight cleaning shift.
Data: events.jsonl (checkout*, checkin*, arrival_waiting_room, cleaning_*), guest_requests.csv
(early_checkin: requested time is in the text, e.g. 'gegen 14:30'), staff_roster.csv (sick shifts,
status_known_utc), room_telemetry.csv housekeeping_state, building.json staff notes.
Plan: queue dirty rooms by next arrival; capacity = available cleaners (State.available_resources)
+ robots for floors only (cannot release) + ONCALL_1 15:30-22:00 for waiting guests only.
Emit schedule_cleaning / dispatch_robot / notify_guest (realistic ETA, guest language) and
release_room ONLY if State.can_release() is True. Quickstart: 37 early check-ins, 8 waiting guests,
CLEANER_1 sick 8 + 17 Jan.

TODO: implement. Keep detections causal (only data <= ctx.t) and use one episode per incident.
"""
from __future__ import annotations

from .base import TriggerDetector


class TurnaroundAgent(TriggerDetector):
    name = "turnaround"

    def build_triggers(self, data, state):
        return []

    def handle(self, ctx, ts, kind, payload):
        pass

"""S05 The Unexpected Weekend.

Event weekend fills the house while a cleaner calls in sick.
Data: bookings (created_utc as-of: a surge becomes visible days ahead), roster sick shift
(status_known_utc), robots, ONCALL_1. Plan: forecast cleaning demand vs capacity per day; plan
robots for floors, on-call for waiting guests; notify guests of realistic times; never invent
capacity -> request_human_approval for extra staff. Quickstart: 25 arrivals 23 Jan, 20 on 26 Jan,
CLEANER_1 sick 24 Jan (known 05:30).

TODO: implement. Keep detections causal (only data <= ctx.t) and use one episode per incident.
"""
from __future__ import annotations

from .base import TriggerDetector


class SurgeAgent(TriggerDetector):
    name = "surge"

    def build_triggers(self, data, state):
        return []

    def handle(self, ctx, ts, kind, payload):
        pass

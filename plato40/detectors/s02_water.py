"""S02 The Hidden Water Leak.

Loud, subtle and 'gap' leaks. Data: room_water / water_main (use_l, rate_l_per_interval,
reset already handled in io.py), presence + housekeeping_state, cleaning events, work_orders (late!).
Plan: per-room night baseline (01:00-05:00 local, vacant vs occupied), persistent excess over
several nights; main minus sum(rooms) for riser/common-area leaks; rule out showers/cleaning/
meter artefacts. Actions: flag_anomaly -> request_inspection (approval; occupied -> consent),
water_shutoff = approval + consent. Quickstart: R104 burst hose 13 Jan (loud), R305 toilet fill
valve running until 25 Jan (subtle).

TODO: implement. Keep detections causal (only data <= ctx.t) and use one episode per incident.
"""
from __future__ import annotations

from .base import TriggerDetector


class WaterAgent(TriggerDetector):
    name = "water"

    def build_triggers(self, data, state):
        return []

    def handle(self, ctx, ts, kind, payload):
        pass

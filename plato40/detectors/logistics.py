"""Cleaning & logistics.

Waste bins WASTE_F1-F4, container WASTE_MAIN (pickup Tue+Fri 07:00 local, extra ~24 h lead),
VEND1 stock (restock Mon+Thu 10:00 local, extra ~24 h lead). Values capped at 100 % (a full bin may
be overflowing). Plan: forecast fill from departures/occupancy; order_service(service='waste_pickup'
| 'vending_restock') >= 24 h before predicted overflow / stock-out; dispatch_robot to carry floor
bins (~20 min).

TODO: implement. Keep detections causal (only data <= ctx.t) and use one episode per incident.
"""
from __future__ import annotations

from .base import TriggerDetector


class LogisticsAgent(TriggerDetector):
    name = "logistics"

    def build_triggers(self, data, state):
        return []

    def handle(self, ctx, ts, kind, payload):
        pass

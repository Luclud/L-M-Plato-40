"""S04 The Maintenance Cascade.

Filter degradation + missing readings + unavailable technician.
Data: equipment AHU1 (filter_dp_pa, airflow_m3h, fan_kw), bms_alarm FILTER_DP_HIGH/AIRFLOW_LOW,
work_orders (postponed, lift platform), roster TECH_1 (vacation known weeks ahead).
Plan: trend filter_dp; a gap is 'unknown' (not 'fixed'); airflow drop confirms real
degradation; if TECH_1 unavailable -> order_service contractor (approval) with realistic date.
Quickstart: dp 92 -> 165 Pa, gap 17-20 Jan, 256 Pa + airflow drop, TECH_1 vacation 19-23 Jan,
filters replaced 23 Jan.

TODO: implement. Keep detections causal (only data <= ctx.t) and use one episode per incident.
"""
from __future__ import annotations

from .base import TriggerDetector


class MaintenanceAgent(TriggerDetector):
    name = "maintenance"

    def build_triggers(self, data, state):
        return []

    def handle(self, ctx, ts, kind, payload):
        pass

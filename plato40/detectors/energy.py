"""Building & energy optimisation.

Day-ahead prices + PV + battery (BAT1 100 kWh, 40 kW, SoC 10-95 kWh) + forecasts.
Plan: optimize_energy decisions (charge in cheap/PV hours, discharge in expensive hours) without
leaving the comfort band. Effects are SIMULATED - label them (simulated_effect).

TODO: implement. Keep detections causal (only data <= ctx.t) and use one episode per incident.
"""
from __future__ import annotations

from .base import TriggerDetector


class EnergyAgent(TriggerDetector):
    name = "energy"

    def build_triggers(self, data, state):
        return []

    def handle(self, ctx, ts, kind, payload):
        pass

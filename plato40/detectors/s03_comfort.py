"""S03 Energy vs Comfort.

Cold weather + dynamic prices + degraded heat pump + occupied rooms.
Data: room_telemetry (temperature vs 20-23 C band when occupied), equipment HP1 (heat_output_kw,
electric_kw, supply_temp_c, status incl. lockout codes), weather + weather_forecast (as-of!),
tariff (published 13:00 local day before). Plan: expected-COP model vs outdoor temp on normal
days -> residual = degradation (the cold snap itself is a look-alike); pre-heat occupied rooms
in cheap hours before forecast cold; dispatch_technician (approval) if degraded; never trade
comfort for price. Quickstart: cold snap 14-17 Jan, COP 2.8 -> 1.6, refrigerant leak fixed 17 Jan,
TECH_1 sick 15 Jan.

TODO: implement. Keep detections causal (only data <= ctx.t) and use one episode per incident.
"""
from __future__ import annotations

from .base import TriggerDetector


class ComfortAgent(TriggerDetector):
    name = "comfort"

    def build_triggers(self, data, state):
        return []

    def handle(self, ctx, ts, kind, payload):
        pass

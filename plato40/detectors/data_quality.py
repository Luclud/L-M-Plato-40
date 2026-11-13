"""Data-quality agent (example / template detector).

Detects: missing room telemetry (grouped per gateway when several rooms on a floor
drop out together), frozen temperature sensors, implausible values, water meter
resets (cross-checked against metering work orders known at that time).

Output is low priority flag_anomaly decisions, one per episode. This also feeds the
other detectors: a gap is "unknown", never "normal" and never "zero".
"""
from __future__ import annotations

import pandas as pd

from ..clock import iso
from ..decision import make_decision
from .base import TriggerDetector, floor_of, run_starts

MISSING_MIN = 8       # 2 h without temperature -> data gap
FROZEN_MIN = 24       # 6 h identical temperature -> frozen sensor
GATEWAY_ROOMS = 3     # >= 3 rooms on one floor missing together -> gateway problem


class DataQualityAgent(TriggerDetector):
    name = "data_quality"

    def build_triggers(self, data, state):
        tel = data.telemetry
        temp = tel.pivot_table(index="timestamp_utc", columns="room_id", values="temperature_c", aggfunc="first",
                               dropna=False)
        grid = pd.date_range(data.start, data.end, freq="15min", inclusive="left")
        temp = temp.reindex(grid)  # absent rows are missing too
        self.temp = temp
        for room in temp.columns:
            s = temp[room]
            starts, ends = run_starts(s.isna(), MISSING_MIN)
            for ts in starts:
                yield ts, "missing_start", room
            for ts in ends:
                yield ts, "missing_end", room
            frozen = s.diff().eq(0) & s.notna()
            fs, fe = run_starts(frozen, FROZEN_MIN)
            for ts in fs:
                yield ts, "frozen_start", room
            for ts in fe:
                yield ts, "frozen_end", room
        bad = tel[tel.implausible != ""]
        for row in bad.itertuples(index=False):
            yield row.timestamp_utc, "implausible", (row.room_id, row.implausible)
        for src in (data.room_water, data.water_main):
            for row in src[src.reset].itertuples(index=False):
                yield row.timestamp_utc, "meter_reset", row.room_id

    # ------------------------------------------------------------------------
    def handle(self, ctx, ts, kind, payload):
        log = ctx.log
        if kind == "missing_start":
            room = payload
            floor = floor_of(room)
            gw = f"GW_F{floor}"
            if log.is_open(f"dq:gw:{gw}"):
                return  # already covered by the gateway episode
            window = self.temp.loc[:ctx.t].tail(MISSING_MIN)
            same_floor = [r for r in window.columns if floor_of(r) == floor and window[r].isna().all()]
            if len(same_floor) >= GATEWAY_ROOMS:
                log.add(make_decision(
                    t=ctx.t, affected_assets=[gw] + same_floor, issue_type="data_quality", action="flag_anomaly",
                    priority="medium",
                    reason=f"{len(same_floor)} rooms on floor {floor} without telemetry for >= 2 h -> likely gateway {gw} outage; "
                           "their comfort/occupancy readings are unknown, not normal",
                    evidence=[f"room_telemetry.csv:{r}:temperature_c missing since <= {iso(ctx.t - pd.Timedelta(hours=2))}" for r in same_floor],
                    verification_plan=f"Readings resume on {gw}; if gap > 12 h request on-site check of {gw}",
                    expected_outcome="Other agents treat these rooms as 'unknown state' until data returns",
                    confidence=0.7, agent=self.name), episode_key=f"dq:gw:{gw}", prefix="DQ")
                for r in same_floor:
                    log.close(f"dq:missing:{r}")
                return
            if not log.is_open(f"dq:missing:{room}"):
                log.add(make_decision(
                    t=ctx.t, affected_assets=[room], issue_type="data_quality", action="flag_anomaly", priority="low",
                    reason=f"No temperature reading from {room} for >= 2 h; room state unknown",
                    evidence=[f"room_telemetry.csv:{room}:temperature_c missing {iso(ctx.t - pd.Timedelta(hours=2))}..{iso(ctx.t)}"],
                    verification_plan="Readings resume; escalate if > 12 h or the room is occupied in cold weather",
                    confidence=0.8, agent=self.name), episode_key=f"dq:missing:{room}", prefix="DQ")
        elif kind == "missing_end":
            room = payload
            log.close(f"dq:missing:{room}")
            gw = f"GW_F{floor_of(room)}"
            if log.is_open(f"dq:gw:{gw}"):
                recent = self.temp.loc[:ctx.t].tail(1)
                if not any(recent[r].isna().all() for r in recent.columns if floor_of(r) == floor_of(room)):
                    log.close(f"dq:gw:{gw}")
        elif kind == "frozen_start":
            room = payload
            log.add(make_decision(
                t=ctx.t, affected_assets=[room], issue_type="data_quality", action="flag_anomaly", priority="low",
                reason=f"Temperature of {room} unchanged for 6 h - sensor possibly frozen; do not trust it for comfort decisions",
                evidence=[f"room_telemetry.csv:{room}:temperature_c constant {iso(ctx.t - pd.Timedelta(hours=6))}..{iso(ctx.t)}"],
                verification_plan="Value starts changing again; otherwise include sensor check in next vacant-room visit",
                confidence=0.6, agent=self.name), episode_key=f"dq:frozen:{room}", prefix="DQ")
        elif kind == "frozen_end":
            ctx.log.close(f"dq:frozen:{payload}")
        elif kind == "implausible":
            room, what = payload
            key = f"dq:implausible:{room}:{ctx.t.date()}"
            if not log.is_open(key):
                log.add(make_decision(
                    t=ctx.t, affected_assets=[room], issue_type="data_quality", action="flag_anomaly", priority="low",
                    reason=f"Implausible reading in {room} ({what.strip(';')}) discarded as sensor fault",
                    evidence=[f"room_telemetry.csv:{room}:{iso(ts)}:{what.strip(';')}"],
                    verification_plan="Following readings plausible; repeated faults -> sensor replacement at next vacancy",
                    confidence=0.9, agent=self.name), episode_key=key, prefix="DQ")
        elif kind == "meter_reset":
            meter = payload
            meter_id = meter if meter == "WM_MAIN" else f"WM_{meter}"
            wo = ctx.asof.work_orders()
            explained = wo[(wo.asset_id == meter_id) & (wo.type == "metering")]
            if not explained.empty:
                reason = (f"Meter {meter_id} counter restarted; matches work order {explained.iloc[-1].work_order_id} "
                          "(meter exchange). Consumption for that step unknown, baseline continues")
                conf = 0.95
            else:
                reason = (f"Meter {meter_id} counter restarted without a known metering work order; "
                          "treated as meter exchange/artefact, not as consumption")
                conf = 0.6
            log.add(make_decision(
                t=ctx.t, affected_assets=[meter_id] + ([] if meter == "WM_MAIN" else [meter]),
                issue_type="data_quality", action="flag_anomaly", priority="low", reason=reason,
                evidence=[f"{'water_main.csv' if meter == 'WM_MAIN' else 'room_water.csv'}:{meter}:{iso(ts)} negative step"],
                verification_plan="Subsequent readings increase normally from the new counter value",
                confidence=conf, agent=self.name), episode_key=f"dq:reset:{meter_id}:{ts}", prefix="DQ")
            log.close(f"dq:reset:{meter_id}:{ts}")

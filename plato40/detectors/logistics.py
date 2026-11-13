"""Cleaning & logistics agent (ticket T08): waste, vending, robot bin runs.

Orders an extra waste pickup / vending restock **before** the predicted overflow / stock-out, inside the
~24 h lead time window, and sends a robot to carry a nearly full floor bin to the central container.

Prediction (causal, per hourly reading at t): consumption rate = mean hourly drop (vending stock) or
rise (waste fill) over the previous RATE_WINDOW, so it adapts to season and occupancy of the dataset
being run. Hours to shortfall = stock / rate (stock-out) or (WASTE_FULL_PCT - fill) / rate (overflow).
Order (``order_service``, routine ``service`` -> autonomous) when the shortfall falls
LEAD..ORDER_HORIZON from now **and** before the next regular service (pickup Tue+Fri 07:00 local,
restock Mon+Thu 10:00 local); at most one order per asset/product per ORDER_COOLDOWN.
Floor bins >= FLOOR_BIN_PCT -> ``dispatch_robot`` bin run (~20 min) with a robot on shift as known at t,
else a flag for the next housekeeping round. Values are capped at 100 % (a full bin may overflow).
"""
from __future__ import annotations

import pandas as pd

from ..clock import iso, local, local_hm
from ..decision import make_decision
from .base import TriggerDetector

ISSUE = "logistics"
RATE_WINDOW = pd.Timedelta(days=14)   # two weekly cycles: smooths weekday/weekend use
RATE_MIN_HOURS = 24
LEAD = pd.Timedelta(hours=24)         # building.json: extra pickup / restock ~24 h lead time
ORDER_HORIZON = pd.Timedelta(hours=48)  # order no earlier than needed: forecasts beyond 2 days are weak
ORDER_COOLDOWN = pd.Timedelta(hours=48)
WASTE_FULL_PCT = 95.0
FLOOR_BIN_PCT = 80.0
BIN_RUN = pd.Timedelta(minutes=20)
BIN_COOLDOWN = pd.Timedelta(hours=12)

# asset.metric -> (service, regular service weekdays (Mon=0), local hh:mm, kind)
SUPPLIES = {
    "WASTE_MAIN.fill_pct": ("waste_pickup", (1, 4), "07:00", "fill"),
    "VEND1.stock_drinks": ("vending_restock", (0, 3), "10:00", "stock"),
    "VEND1.stock_snacks": ("vending_restock", (0, 3), "10:00", "stock"),
}


def next_regular(t: pd.Timestamp, weekdays, hhmm: str) -> pd.Timestamp:
    """Next regular service strictly after t (local weekday + time, DST-safe)."""
    lt = local(t)
    h, m = int(hhmm[:2]), int(hhmm[3:])
    for k in range(0, 9):
        c = (lt.normalize() + pd.Timedelta(days=k)).replace(hour=h, minute=m)
        c = c.tz_localize(None).tz_localize(lt.tz) if c.tzinfo is None else c
        if c.weekday() in weekdays and c > lt:
            return c.tz_convert("UTC")
    raise ValueError("no regular service in the next week")


def hourly_rate(series: pd.Series, kind: str) -> pd.Series:
    """Causal mean consumption per hour (window ends at t): drops of stock or rises of fill level."""
    dd = series.diff()
    use = (-dd).clip(lower=0) if kind == "stock" else dd.clip(lower=0)
    win = f"{int(RATE_WINDOW.total_seconds() // 3600)}h"
    n = use.rolling(win, min_periods=1).count()
    return use.rolling(win, min_periods=1).mean().where(n >= RATE_MIN_HOURS)


def plan_orders(series: pd.Series, kind: str, weekdays, hhmm: str) -> list[dict]:
    """Order times for one supply series (pure, causal: an order at t uses values <= t only)."""
    s = series.dropna()
    rate = hourly_rate(s, kind)
    out, last = [], None
    for t, v in s.items():
        r = rate.get(t)
        if r is None or pd.isna(r) or r <= 0:
            continue
        left = v if kind == "stock" else max(0.0, WASTE_FULL_PCT - v)
        eta = t + pd.Timedelta(hours=left / r)
        if not (t + LEAD <= eta <= t + ORDER_HORIZON):
            continue
        nxt = next_regular(t, weekdays, hhmm)
        if eta >= nxt or (last is not None and t - last < ORDER_COOLDOWN):
            continue
        last = t
        out.append(dict(t=t, value=float(v), rate=float(r), eta=eta, next_regular=nxt))
    return out


class LogisticsAgent(TriggerDetector):
    name = "logistics"

    def build_triggers(self, data, state):
        self.robots = [r for r, role in data.resources.items() if role == "robot"]
        lw = data.logistics_wide()
        for col, (service, days, hhmm, kind) in SUPPLIES.items():
            if col not in lw:
                continue
            for o in plan_orders(lw[col], kind, days, hhmm):
                yield o["t"], "order", dict(o, col=col, service=service, kind=kind)
        for col in [c for c in lw.columns if c.startswith("WASTE_F") and c.endswith(".fill_pct")]:
            s = lw[col].dropna()
            last = None
            for t, v in s[s >= FLOOR_BIN_PCT].items():
                if last is None or t - last >= BIN_COOLDOWN:
                    last = t
                    yield t, "bin", dict(col=col, value=float(v))

    def handle(self, ctx, ts, kind, p):
        asset, metric = p["col"].split(".", 1)
        key = f"logistics:{asset}:{local(ctx.t).date()}"
        ev = [f"logistics.csv:{asset}:{iso(ts - RATE_WINDOW)}..{iso(ts)}:{metric}={p['value']:.0f}"]
        if kind == "order":
            what = metric.replace("stock_", "") if p["kind"] == "stock" else "fill"
            short = "stock-out" if p["kind"] == "stock" else f"{WASTE_FULL_PCT:.0f} % fill (overflow risk)"
            d = make_decision(
                t=ctx.t, affected_assets=[asset], issue_type=ISSUE, action="order_service", priority="medium",
                service=p["service"],
                reason=(f"{asset} {what} {p['value']:.0f}{' %' if p['kind'] == 'fill' else ' units'}, using "
                        f"~{p['rate']:.1f}/h (learned, last {RATE_WINDOW.days} days) -> {short} expected "
                        f"~{local(p['eta']).strftime('%a %H:%M')} local, before the next regular "
                        f"{p['service'].replace('_', ' ')} {local(p['next_regular']).strftime('%a %H:%M')} local; "
                        f"extra {p['service'].replace('_', ' ')} needs ~{int(LEAD.total_seconds() // 3600)} h -> order now"),
                evidence=ev, scheduled_for_utc=ctx.t + LEAD,
                expected_outcome=f"Extra {p['service'].replace('_', ' ')} before the predicted {short}",
                verification_plan=(f"{'vending_restocked' if p['kind'] == 'stock' else 'waste_pickup'} event for {asset} "
                                   f"before ~{local_hm(p['eta'])} local and no "
                                   f"{'vending_out_of_stock' if p['kind'] == 'stock' else 'waste_bin_full'} event"),
                confidence=0.6, agent=self.name)
            ctx.log.add(d, episode_key=key, prefix="LG")
        elif kind == "bin":
            free = [r for r in self.robots if ctx.state.available(r, ctx.t, known_at=ctx.t)[0]
                    and ctx.state.available(r, ctx.t + BIN_RUN, known_at=ctx.t)[0]]
            if free:
                d = make_decision(
                    t=ctx.t, affected_assets=[asset, free[0]], issue_type=ISSUE, action="dispatch_robot",
                    priority="low", resource_id=free[0], scheduled_for_utc=ctx.t,
                    reason=(f"Floor bin {asset} at {p['value']:.0f} % (>= {FLOOR_BIN_PCT:.0f} %): {free[0]} carries it to "
                            f"WASTE_MAIN (~{int(BIN_RUN.total_seconds() // 60)} min, corridor only - never enters rooms)"),
                    evidence=ev, expected_outcome=f"{asset} emptied before it overflows (planned, not yet executed)",
                    verification_plan=f"floor_bin_emptied event for {asset} / fill_pct drops", confidence=0.6,
                    agent=self.name)
            else:
                d = make_decision(
                    t=ctx.t, affected_assets=[asset], issue_type=ISSUE, action="flag_anomaly", priority="low",
                    reason=(f"Floor bin {asset} at {p['value']:.0f} % and no robot on shift as known -> housekeeping to "
                            "empty it on the next round"),
                    evidence=ev, verification_plan=f"floor_bin_emptied event for {asset}", confidence=0.6,
                    agent=self.name)
            ctx.log.add(d, episode_key=key, prefix="LG")

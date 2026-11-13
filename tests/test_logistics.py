"""T08 logistics agent: orders inside the lead-time window, causal forecasts, robots only on shift."""
import pandas as pd
import pytest

from plato40 import engine
from plato40.detectors.logistics import (LEAD, ORDER_HORIZON, LogisticsAgent, hourly_rate, next_regular,
                                         plan_orders)

T = pd.Timestamp


def _stock(start="2026-07-06T09:00Z", hours=24 * 30, use=2.0, restock_days=(0, 3), full=100.0):
    """Synthetic vending stock: -use/h, refilled to `full` at the regular restock (Mon+Thu 10:00 local)."""
    idx = pd.date_range(start, periods=hours, freq="h", tz="UTC")
    vals, v = [], full
    for t in idx:
        loc = t.tz_convert("Europe/Vienna")
        if loc.weekday() in restock_days and loc.hour == 10:
            v = full
        vals.append(v)
        v = max(0.0, v - use)
    return pd.Series(vals, index=idx)


def test_next_regular_is_local_and_dst_safe():
    assert next_regular(T("2026-01-05T08:00Z"), (0, 3), "10:00") == T("2026-01-05T09:00Z")   # Mon, winter
    assert next_regular(T("2026-01-05T09:00Z"), (0, 3), "10:00") == T("2026-01-08T09:00Z")   # strictly after
    assert next_regular(T("2026-03-28T12:00Z"), (1, 4), "07:00") == T("2026-03-31T05:00Z")   # after DST switch


def test_orders_only_inside_lead_window_and_before_regular_service():
    s = _stock(use=2.0)          # 100 units, 2/h -> stock-out after 50 h; Thu->Mon gap is 96 h
    orders = plan_orders(s, "stock", (0, 3), "10:00")
    assert orders
    for o in orders:
        assert o["t"] + LEAD <= o["eta"] <= o["t"] + ORDER_HORIZON
        assert o["eta"] < o["next_regular"]


def test_no_order_when_regular_service_comes_first():
    s = _stock(use=0.5)          # 200 h to stock-out, regular restock always earlier
    assert plan_orders(s, "stock", (0, 3), "10:00") == []


def test_forecast_is_causal():
    s = _stock(use=2.0)
    cut = s.index[len(s) // 2]
    full = [o["t"] for o in plan_orders(s, "stock", (0, 3), "10:00") if o["t"] <= cut]
    trunc = [o["t"] for o in plan_orders(s.loc[:cut], "stock", (0, 3), "10:00")]
    assert full == trunc
    r = hourly_rate(s, "stock")
    assert r.loc[:cut].equals(hourly_rate(s.loc[:cut], "stock"))


def test_missing_values_are_not_zero_stock():
    s = _stock(use=2.0)
    gap = s.index[100:110]
    s.loc[gap] = float("nan")        # gateway gap: unknown, not empty
    orders = plan_orders(s, "stock", (0, 3), "10:00")
    assert orders and all(o["t"] not in gap and o["value"] == o["value"] for o in orders)
    assert plan_orders(s.loc[gap], "stock", (0, 3), "10:00") == []


@pytest.fixture(scope="module")
def decisions(qs):
    return [d.to_json() for d in engine.run(qs, [LogisticsAgent()], verbose=False).decisions]


def test_quickstart_decisions_are_safe(qs_state, decisions):
    for d in decisions:
        assert d["issue_type"] == "logistics"
        if d["action"] == "order_service":
            assert d["autonomy"] == "autonomous" and d["requires_human_approval"] is False
        if d["action"] == "dispatch_robot":
            ok, why = qs_state.available(d["resource_id"], T(d["scheduled_for_utc"]), known_at=T(d["timestamp_utc"]))
            assert ok, why
        assert d["episode_id"].startswith("LG-")

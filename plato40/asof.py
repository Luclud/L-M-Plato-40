"""As-of views: what was *known* at decision time t.

Every detector must read data through these helpers (or through windows that end
at t). This is how the pipeline guarantees "decide only on what was known at
that moment" (CHALLENGE §5): forecasts and tariffs by publication time, roster
changes by status_known_utc, work orders by created_utc, requests/bookings by
creation time, measurements by timestamp.
"""
from __future__ import annotations

import pandas as pd

from .io import Data


def upto(df: pd.DataFrame, col: str, t: pd.Timestamp, inclusive: bool = True) -> pd.DataFrame:
    """Rows with df[col] <= t. df must be sorted by col."""
    side = "right" if inclusive else "left"
    idx = df[col].searchsorted(t, side=side)
    return df.iloc[:idx]


def between(df: pd.DataFrame, col: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Rows with start < df[col] <= end. df must be sorted by col."""
    a = df[col].searchsorted(start, side="right")
    b = df[col].searchsorted(end, side="right")
    return df.iloc[a:b]


def _memo(data: Data) -> dict:
    """Per-dataset cache for as-of views that only change when a new row becomes known."""
    c = data.__dict__.get("_asof_memo")
    if c is None:
        c = data.__dict__["_asof_memo"] = {}
    return c


def _known_count(memo: dict, df: pd.DataFrame, col: str, t: pd.Timestamp) -> int:
    """How many non-null values of df[col] are <= t (sorted copy cached per source frame)."""
    key = ("sorted", id(df), len(df), col)
    hit = memo.get(key)
    if hit is None or hit[0] is not df:
        hit = memo[key] = (df, df[col].dropna().sort_values().reset_index(drop=True))
    return int(hit[1].searchsorted(t, side="right"))


def _monotonic(memo: dict, df: pd.DataFrame, col: str) -> bool:
    """df[col] has no nulls and is non-decreasing (cached per source frame)."""
    key = ("monotonic", id(df), len(df), col)
    hit = memo.get(key)
    if hit is None or hit[0] is not df:
        hit = memo[key] = (df, bool(df[col].notna().all() and df[col].is_monotonic_increasing))
    return hit[1]


_PREFIX: dict = {}


def prefix_of(df: pd.DataFrame) -> tuple[pd.DataFrame, int] | None:
    """(source, n) if ``df`` is an as-of view returned by AsOf.bookings() equal to ``source.iloc[:n]``."""
    hit = _PREFIX.get(id(df))
    return (hit[1], hit[2]) if hit is not None and hit[0] is df else None


class AsOf:
    def __init__(self, data: Data, t: pd.Timestamp):
        self.d = data
        self.t = t

    # --- published information ---------------------------------------------
    def tariff(self) -> pd.DataFrame:
        """Hourly prices that were published by t (includes tomorrow after 13:00 local)."""
        tr = self.d.tariff
        return tr[tr.published_utc <= self.t]

    def forecast(self) -> pd.DataFrame:
        """Latest 48 h forecast issued at or before t (empty if none yet)."""
        fc = upto(self.d.forecast, "issued_utc", self.t)
        if fc.empty:
            return fc
        return fc[fc.issued_utc == fc.issued_utc.iloc[-1]]

    def roster(self) -> pd.DataFrame:
        """Roster as operations believed it at t: absences not yet known look 'scheduled'.

        Cached per number of status changes known by t (the view only changes when one becomes
        known); treat the returned frame as read-only."""
        src = self.d.roster
        memo = _memo(self.d)
        key = ("roster", id(src), len(src), _known_count(memo, src, "status_known_utc", self.t))
        r = memo.get(key)
        if r is None:
            r = src.copy()
            unknown = r.status_known_utc.notna() & (r.status_known_utc > self.t)
            r.loc[unknown, "status"] = "scheduled"
            r.attrs["asof_source"] = (id(src), len(src))   # same rows/order as src: lets planners share prep
            memo[key] = r
        return r

    def work_orders(self) -> pd.DataFrame:
        return upto(self.d.work_orders, "created_utc", self.t)

    def bookings(self) -> pd.DataFrame:
        """Bookings created by t (cached per number known; treat as read-only)."""
        b = self.d.bookings
        memo = _memo(self.d)
        key = ("bookings", id(b), len(b), _known_count(memo, b, "created_utc", self.t))
        out = memo.get(key)
        if out is None:
            if _monotonic(memo, b, "created_utc"):
                # sorted by creation: the known bookings are exactly the first n rows (same rows, same order)
                out = memo[key] = b.iloc[:key[-1]]
                _PREFIX[id(out)] = (out, b, key[-1])
            else:
                out = memo[key] = b[b.created_utc <= self.t]
        return out

    def guest_requests(self) -> pd.DataFrame:
        return upto(self.d.guest_requests, "timestamp_utc", self.t)

    def events(self, since: pd.Timestamp | None = None) -> pd.DataFrame:
        ev = self.d.events
        return between(ev, "timestamp_utc", since, self.t) if since is not None else upto(ev, "timestamp_utc", self.t)

    # --- measurements ------------------------------------------------------
    def window(self, df: pd.DataFrame, lookback: pd.Timedelta, col: str = "timestamp_utc") -> pd.DataFrame:
        """Rows in (t - lookback, t] of a timestamp-sorted frame."""
        return between(df, col, self.t - lookback, self.t)

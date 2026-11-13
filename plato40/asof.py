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
        """Roster as operations believed it at t: absences not yet known look 'scheduled'."""
        r = self.d.roster.copy()
        unknown = r.status_known_utc.notna() & (r.status_known_utc > self.t)
        r.loc[unknown, "status"] = "scheduled"
        return r

    def work_orders(self) -> pd.DataFrame:
        return upto(self.d.work_orders, "created_utc", self.t)

    def bookings(self) -> pd.DataFrame:
        b = self.d.bookings
        return b[b.created_utc <= self.t]

    def guest_requests(self) -> pd.DataFrame:
        return upto(self.d.guest_requests, "timestamp_utc", self.t)

    def events(self, since: pd.Timestamp | None = None) -> pd.DataFrame:
        ev = self.d.events
        return between(ev, "timestamp_utc", since, self.t) if since is not None else upto(ev, "timestamp_utc", self.t)

    # --- measurements ------------------------------------------------------
    def window(self, df: pd.DataFrame, lookback: pd.Timedelta, col: str = "timestamp_utc") -> pd.DataFrame:
        """Rows in (t - lookback, t] of a timestamp-sorted frame."""
        return between(df, col, self.t - lookback, self.t)

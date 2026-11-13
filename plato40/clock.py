"""Time helpers. Data is UTC; operations follow Europe/Vienna (mind DST)."""
from __future__ import annotations

import pandas as pd

LOCAL_TZ = "Europe/Vienna"


def utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def local(ts) -> pd.Timestamp:
    return utc(ts).tz_convert(LOCAL_TZ)


def iso(ts) -> str:
    """ISO 8601 UTC with Z, as required by the decision contract."""
    return utc(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_hm(ts) -> str:
    return local(ts).strftime("%H:%M")


def at_local(day_ts, hhmm: str) -> pd.Timestamp:
    """UTC timestamp for local clock time hhmm on the local calendar day of day_ts."""
    d = local(day_ts).normalize()
    h, m = (int(x) for x in hhmm.split(":"))
    return utc(d.replace(hour=h, minute=m))
